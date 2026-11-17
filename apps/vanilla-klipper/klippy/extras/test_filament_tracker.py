import unittest

import extras.filament_tracker as tracker


class FakeReactor:
    NOW = 0.0
    NEVER = 1e16

    def __init__(self):
        self.time = 0.0
        self._timers = {}  # callback -> waketime

    def monotonic(self):
        return self.time

    def register_timer(self, callback):
        self._timers[callback] = self.NEVER
        return callback

    def update_timer(self, timer_id, waketime):
        self._timers[timer_id] = waketime

    def fire_ready_timers(self):
        """Fire all timers whose waketime <= self.time."""
        for cb, wake in list(self._timers.items()):
            if wake <= self.time:
                self._timers[cb] = cb(self.time)


class FakeExtruder:
    """Minimal extruder stub for motion detection tests."""
    def __init__(self):
        self.position = 0.0

    def find_past_position(self, print_time):
        return self.position


class FakeMcu:
    """Minimal MCU stub providing estimated_print_time."""
    def estimated_print_time(self, eventtime):
        return eventtime


class FakeButtons:
    def __init__(self):
        self.callbacks = []

    def register_buttons(self, pins, callback):
        self.callbacks.append((pins, callback))


class FakeADC:
    def __init__(self, name):
        self.name = name
        self.sampled = False
        self.cb = None

    def setup_adc_sample(self, *args, **kwargs):
        self.sampled = True

    def setup_adc_callback(self, period, cb):
        self.cb = cb


class FakePins:
    def __init__(self):
        self.adcs = {}

    def setup_pin(self, pin_type, pin_name):
        assert pin_type == "adc"
        adc = FakeADC(pin_name)
        self.adcs[pin_name] = adc
        return adc


class FakeTemplate:
    """Minimal template stub for RunoutHelper."""
    def render(self):
        return ""


class FakeGcodeMacro:
    """Stub for the gcode_macro Klipper module."""
    def load_template(self, config, key, default=""):
        return FakeTemplate()


class FakeGcode:
    """Stub for the gcode Klipper object."""
    def register_mux_command(self, cmd, key, value, func, desc=""):
        pass

    def run_script(self, script):
        pass

    def respond_info(self, msg):
        pass


class FakePrinter:
    def __init__(self, signal_type):
        self.reactor = FakeReactor()
        self.buttons = FakeButtons()
        self.pins = FakePins()
        self.gcode = FakeGcode()
        self.gcode_macro = FakeGcodeMacro()
        self.signal_type = signal_type
        self.event_handlers = {}
        self.extruder = FakeExtruder()
        self.mcu = FakeMcu()

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name):
        if name == "pins":
            return self.pins
        if name == "gcode":
            return self.gcode
        if name == "mcu":
            return self.mcu
        if name.startswith("extruder"):
            return self.extruder
        raise ValueError(name)

    def load_object(self, config, name):
        if name == "buttons":
            return self.buttons
        if name == "gcode_macro":
            return self.gcode_macro
        raise ValueError(name)

    def register_event_handler(self, event, callback):
        self.event_handlers.setdefault(event, []).append(callback)


class FakeConfig:
    def __init__(self, printer, values):
        self.printer = printer
        self.values = values

    def get_printer(self):
        return self.printer

    def get_name(self):
        return "filament_tracker"

    def getfloat(self, key, default, above=None, minval=None):
        return float(self.values.get(key, default))

    def getboolean(self, key, default):
        val = self.values.get(key)
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("true", "1", "yes")

    def get(self, key, default=None):
        return self.values.get(key, default)

    def error(self, msg):
        return ValueError(msg)


class FilamentTrackerGpioTest(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter(signal_type="gpio")
        self.config = FakeConfig(self.printer, {
            "signal_type": "gpio",
            "tracker_detect_pin": "PB0",
            "tracker_encoder_pin": "PA1",
            "absence_timeout": "1.0",
            "pause_on_runout": False,
        })

    def test_both_open_does_not_immediately_report_absent(self):
        """Encoder resting at 0b00 must NOT instantly declare absent."""
        ft = tracker.FilamentTracker(self.config)
        pins, cb = self.printer.buttons.callbacks[0]
        # Simulate encoder activity (filament moving)
        cb(0.1, 0b01)
        cb(0.2, 0b11)  # block edge
        cb(0.3, 0b01)  # block edge
        self.assertTrue(ft.is_filament_present())
        # Encoder rests at 0b00
        cb(0.4, 0b00)
        # Should still be present (edge at t=0.3, timeout=1.0)
        self.assertTrue(ft.is_filament_present())

    def test_absence_declared_after_timeout(self):
        """After sustained both-open with no edges, absence is declared."""
        ft = tracker.FilamentTracker(self.config)
        reactor = self.printer.reactor
        pins, cb = self.printer.buttons.callbacks[0]
        # Some activity then both-open
        cb(0.1, 0b01)
        cb(0.2, 0b00)
        self.assertTrue(ft.is_filament_present())
        # Advance past absence timeout and fire the timer
        reactor.time = 1.3  # 0.2 + 1.0 + margin
        reactor.fire_ready_timers()
        self.assertFalse(ft.is_filament_present())

    def test_edge_during_timeout_cancels_absence(self):
        """An encoder edge while the absence timer is running resets it."""
        ft = tracker.FilamentTracker(self.config)
        reactor = self.printer.reactor
        pins, cb = self.printer.buttons.callbacks[0]
        cb(0.1, 0b01)
        cb(0.2, 0b00)  # starts absence timer
        self.assertTrue(ft.is_filament_present())
        # Edge arrives before timeout
        cb(0.5, 0b10)  # block channel active
        self.assertTrue(ft.is_filament_present())
        # Original timeout fires — should still be present
        reactor.time = 1.3
        reactor.fire_ready_timers()
        self.assertTrue(ft.is_filament_present())

    def test_pulses_counted_on_block_edges(self):
        ft = tracker.FilamentTracker(self.config)
        pins, cb = self.printer.buttons.callbacks[0]
        # Toggle block pin (bit 1) to generate pulses
        cb(0.1, 0b00)
        cb(0.2, 0b10)  # block rising
        cb(0.3, 0b00)  # block falling
        cb(0.4, 0b10)  # block rising
        self.assertEqual(ft.get_status()["encoder_pulse"], 3)

    def test_no_false_absent_at_any_rest_position(self):
        """All four encoder rest positions with recent activity stay present."""
        for rest_state in [0b00, 0b01, 0b10, 0b11]:
            ft = tracker.FilamentTracker(self.config)
            self.printer.buttons.callbacks.clear()
            ft2 = tracker.FilamentTracker(self.config)
            pins, cb = self.printer.buttons.callbacks[0]
            # Generate some activity
            cb(0.1, 0b01)
            cb(0.2, 0b11)
            cb(0.3, 0b01)
            # Rest at arbitrary position
            cb(0.4, rest_state)
            self.assertTrue(ft2.is_filament_present(),
                            f"False absent at rest state 0b{rest_state:02b}")


class FilamentTrackerAdcTest(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter(signal_type="adc")
        self.config = FakeConfig(self.printer, {
            "signal_type": "adc",
            "tracker_detect_pin": "adc0",
            "tracker_encoder_pin": "adc1",
            "absence_timeout": "1.0",
            "pause_on_runout": False,
        })

    def test_one_channel_closed_is_present(self):
        ft = tracker.FilamentTracker(self.config)
        break_adc = self.printer.pins.adcs["adc0"]
        block_adc = self.printer.pins.adcs["adc1"]
        # One channel below threshold -> present
        break_adc.cb(10.0, 0.5)
        block_adc.cb(10.0, 0.8)
        self.assertTrue(ft.is_filament_present())

    def test_both_open_does_not_immediately_report_absent(self):
        """Both above threshold should NOT instantly declare absent."""
        ft = tracker.FilamentTracker(self.config)
        break_adc = self.printer.pins.adcs["adc0"]
        block_adc = self.printer.pins.adcs["adc1"]
        # Generate an edge first (so _last_edge_time is set)
        block_adc.cb(10.0, 0.2)
        block_adc.cb(10.1, 0.9)  # edge at t=10.1
        # Both open, but within timeout
        break_adc.cb(10.2, 0.9)
        block_adc.cb(10.2, 0.9)
        self.assertTrue(ft.is_filament_present())

    def test_absence_after_timeout(self):
        """Both open for > absence_timeout -> absent."""
        ft = tracker.FilamentTracker(self.config)
        break_adc = self.printer.pins.adcs["adc0"]
        block_adc = self.printer.pins.adcs["adc1"]
        # Edge at t=10.0
        block_adc.cb(10.0, 0.9)  # encoder_signal_state 0->1, edge
        # Both open, well past timeout (t=20.0 >> 10.0 + 1.0)
        break_adc.cb(20.0, 0.9)
        block_adc.cb(20.0, 0.9)
        self.assertFalse(ft.is_filament_present())

    def test_encoder_pulse_counts_block_edges(self):
        ft = tracker.FilamentTracker(self.config)
        block_adc = self.printer.pins.adcs["adc1"]
        # encoder_signal_state starts at 1 (open), so first 0.9 is no edge
        block_adc.cb(0.0, 0.2)  # closed -> state 0 (edge 1)
        block_adc.cb(0.1, 0.9)  # open   -> state 1 (edge 2)
        block_adc.cb(0.2, 0.2)  # closed -> state 0 (edge 3)
        self.assertEqual(ft.get_status()["encoder_pulse"], 3)

    def test_recent_edge_keeps_present_despite_both_open(self):
        """Even when both channels read open, a recent edge keeps present."""
        ft = tracker.FilamentTracker(self.config)
        break_adc = self.printer.pins.adcs["adc0"]
        block_adc = self.printer.pins.adcs["adc1"]
        # Create a real edge: close then open
        block_adc.cb(4.9, 0.2)  # closed -> state 0 (edge)
        block_adc.cb(5.0, 0.9)  # open   -> state 1 (edge at t=5.0)
        # Both open at t=5.5 (within 1.0s timeout)
        break_adc.cb(5.5, 0.9)
        block_adc.cb(5.5, 0.9)
        self.assertTrue(ft.is_filament_present())
        # Still open at t=6.5 (past timeout: 6.5 - 5.0 = 1.5 > 1.0)
        break_adc.cb(6.5, 0.9)
        block_adc.cb(6.5, 0.9)
        self.assertFalse(ft.is_filament_present())


class FilamentTrackerAdcInvertedTest(unittest.TestCase):
    """Tests for adc_inverted=True (low voltage = open, e.g. RDM hub)."""

    def setUp(self):
        self.printer = FakePrinter(signal_type="adc")
        self.config = FakeConfig(self.printer, {
            "signal_type": "adc",
            "tracker_detect_pin": "adc0",
            "tracker_encoder_pin": "adc1",
            "absence_timeout": "1.0",
            "adc_inverted": True,
            "pause_on_runout": False,
        })

    def test_both_low_is_absent(self):
        """With adc_inverted, low voltage on both pins = open = absent."""
        ft = tracker.FilamentTracker(self.config)
        detect_adc = self.printer.pins.adcs["adc0"]
        encoder_adc = self.printer.pins.adcs["adc1"]
        # Both low (open in inverted mode), past timeout
        detect_adc.cb(20.0, 0.04)
        encoder_adc.cb(20.0, 0.04)
        self.assertFalse(ft.is_filament_present())

    def test_high_voltage_is_present(self):
        """With adc_inverted, high voltage = closed = filament present."""
        ft = tracker.FilamentTracker(self.config)
        detect_adc = self.printer.pins.adcs["adc0"]
        encoder_adc = self.printer.pins.adcs["adc1"]
        detect_adc.cb(10.0, 0.9)  # high = closed in inverted mode
        encoder_adc.cb(10.0, 0.04)
        self.assertTrue(ft.is_filament_present())

    def test_encoder_edges_counted_on_inverted(self):
        """Encoder pulses counted correctly with inverted polarity."""
        ft = tracker.FilamentTracker(self.config)
        encoder_adc = self.printer.pins.adcs["adc1"]
        # Init state: low (open, encoder_signal_state=1)
        # Go high (closed, state 0) → edge 1
        encoder_adc.cb(0.1, 0.9)
        # Go low (open, state 1) → edge 2
        encoder_adc.cb(0.2, 0.04)
        # Go high (closed, state 0) → edge 3
        encoder_adc.cb(0.3, 0.9)
        self.assertEqual(ft.get_status()["encoder_pulse"], 3)

    def test_absence_needs_timeout_inverted(self):
        """Recent edge prevents absence even with both low."""
        ft = tracker.FilamentTracker(self.config)
        detect_adc = self.printer.pins.adcs["adc0"]
        encoder_adc = self.printer.pins.adcs["adc1"]
        # Create a real edge
        encoder_adc.cb(5.0, 0.9)   # high → closed, edge
        encoder_adc.cb(5.1, 0.04)  # low → open, edge at t=5.1
        # Both low at t=5.5 (within 1.0s timeout)
        detect_adc.cb(5.5, 0.04)
        encoder_adc.cb(5.5, 0.04)
        self.assertTrue(ft.is_filament_present())
        # Past timeout: t=6.5 - 5.1 = 1.4 > 1.0
        detect_adc.cb(6.5, 0.04)
        encoder_adc.cb(6.5, 0.04)
        self.assertFalse(ft.is_filament_present())


class FilamentTrackerGpioSwitchModeTest(unittest.TestCase):
    """Tests for detect_pin_is_switch=True with GPIO signal type."""

    def setUp(self):
        self.printer = FakePrinter(signal_type="gpio")
        self.config = FakeConfig(self.printer, {
            "signal_type": "gpio",
            "tracker_detect_pin": "PB0",
            "tracker_encoder_pin": "PA1",
            "absence_timeout": "1.0",
            "detect_pin_is_switch": True,
            "pause_on_runout": False,
        })

    def test_detect_open_immediately_absent(self):
        """When detect pin is 0, filament absent regardless of encoder."""
        ft = tracker.FilamentTracker(self.config)
        _, cb = self.printer.buttons.callbacks[0]
        # Detect=1, encoder=1 → present
        cb(0.1, 0b11)
        self.assertTrue(ft.is_filament_present())
        # Detect=0, encoder=1 → absent immediately (no timeout)
        cb(0.2, 0b10)
        self.assertFalse(ft.is_filament_present())

    def test_detect_closed_present_regardless_of_encoder(self):
        """When detect pin is 1, filament present even if encoder is 0."""
        ft = tracker.FilamentTracker(self.config)
        _, cb = self.printer.buttons.callbacks[0]
        # Detect=1, encoder=0
        cb(0.1, 0b01)
        self.assertTrue(ft.is_filament_present())
        # Detect=1, encoder=1
        cb(0.2, 0b11)
        self.assertTrue(ft.is_filament_present())

    def test_encoder_pulses_still_counted(self):
        """Encoder edges are counted even in switch mode."""
        ft = tracker.FilamentTracker(self.config)
        _, cb = self.printer.buttons.callbacks[0]
        cb(0.1, 0b01)  # detect=1, encoder=0
        cb(0.2, 0b11)  # encoder 0→1 (edge)
        cb(0.3, 0b01)  # encoder 1→0 (edge)
        cb(0.4, 0b11)  # encoder 0→1 (edge)
        self.assertEqual(ft.get_status()["encoder_pulse"], 3)

    def test_no_absence_timer_in_switch_mode(self):
        """Switch mode should not use the absence timer at all."""
        ft = tracker.FilamentTracker(self.config)
        _, cb = self.printer.buttons.callbacks[0]
        cb(0.1, 0b01)
        # Detect=0, encoder=0 → absent immediately, no timer needed
        cb(0.2, 0b00)
        self.assertFalse(ft.is_filament_present())


class FilamentTrackerAdcSwitchModeTest(unittest.TestCase):
    """Tests for detect_pin_is_switch=True with ADC signal type."""

    def setUp(self):
        self.printer = FakePrinter(signal_type="adc")
        self.config = FakeConfig(self.printer, {
            "signal_type": "adc",
            "tracker_detect_pin": "adc0",
            "tracker_encoder_pin": "adc1",
            "absence_timeout": "1.0",
            "detect_pin_is_switch": True,
            "pause_on_runout": False,
        })

    def test_detect_open_immediately_absent(self):
        """Detect pin open → immediate absent even if encoder is closed."""
        ft = tracker.FilamentTracker(self.config)
        detect_adc = self.printer.pins.adcs["adc0"]
        encoder_adc = self.printer.pins.adcs["adc1"]
        # Detect closed, encoder closed → present
        detect_adc.cb(10.0, 0.3)
        encoder_adc.cb(10.0, 0.3)
        self.assertTrue(ft.is_filament_present())
        # Detect open, encoder still closed → absent immediately
        detect_adc.cb(10.1, 0.9)
        self.assertFalse(ft.is_filament_present())

    def test_detect_closed_present_with_encoder_open(self):
        """Detect pin closed → present even if encoder reads open."""
        ft = tracker.FilamentTracker(self.config)
        detect_adc = self.printer.pins.adcs["adc0"]
        encoder_adc = self.printer.pins.adcs["adc1"]
        # Detect closed (0.3 < 0.70), encoder open (0.9 > 0.70)
        detect_adc.cb(10.0, 0.3)
        encoder_adc.cb(10.0, 0.9)
        self.assertTrue(ft.is_filament_present())

    def test_encoder_pulses_counted_in_switch_mode(self):
        """Encoder edges are counted regardless of switch mode."""
        ft = tracker.FilamentTracker(self.config)
        detect_adc = self.printer.pins.adcs["adc0"]
        encoder_adc = self.printer.pins.adcs["adc1"]
        detect_adc.cb(0.0, 0.3)  # detect closed (present)
        # encoder_signal_state starts at 1, so start with close
        encoder_adc.cb(0.1, 0.2)  # closed → state 0 (edge 1)
        encoder_adc.cb(0.2, 0.9)  # open   → state 1 (edge 2)
        encoder_adc.cb(0.3, 0.2)  # closed → state 0 (edge 3)
        self.assertEqual(ft.get_status()["encoder_pulse"], 3)

    def test_no_timeout_dependency(self):
        """Absence should not depend on timeout in switch mode."""
        ft = tracker.FilamentTracker(self.config)
        detect_adc = self.printer.pins.adcs["adc0"]
        encoder_adc = self.printer.pins.adcs["adc1"]
        # Recent encoder edge, but detect open → still absent
        encoder_adc.cb(10.0, 0.9)  # edge at t=10.0
        detect_adc.cb(10.05, 0.9)  # detect open at t=10.05 (within timeout)
        self.assertFalse(ft.is_filament_present())


class FilamentDistanceTrackingTest(unittest.TestCase):
    """Tests for filament_distance (cumulative mm) tracking."""

    def test_filament_distance_updates_on_gpio_pulses(self):
        """filament_distance = encoder_pulse × length_per_pulse."""
        printer = FakePrinter(signal_type="gpio")
        config = FakeConfig(printer, {
            "signal_type": "gpio",
            "tracker_detect_pin": "PB0",
            "tracker_encoder_pin": "PA1",
            "absence_timeout": "1.0",
            "length_per_pulse": "0.5",
            "pause_on_runout": False,
        })
        ft = tracker.FilamentTracker(config)
        _, cb = printer.buttons.callbacks[0]
        cb(0.1, 0b00)
        cb(0.2, 0b10)  # encoder edge 1
        cb(0.3, 0b00)  # encoder edge 2
        cb(0.4, 0b10)  # encoder edge 3
        self.assertEqual(ft.get_status()["encoder_pulse"], 3)
        self.assertAlmostEqual(ft.get_status()["filament_distance"], 1.5)

    def test_filament_distance_updates_on_adc_pulses(self):
        """filament_distance tracks ADC encoder edges too."""
        printer = FakePrinter(signal_type="adc")
        config = FakeConfig(printer, {
            "signal_type": "adc",
            "tracker_detect_pin": "adc0",
            "tracker_encoder_pin": "adc1",
            "absence_timeout": "1.0",
            "length_per_pulse": "0.25",
            "pause_on_runout": False,
        })
        ft = tracker.FilamentTracker(config)
        encoder_adc = printer.pins.adcs["adc1"]
        # encoder_signal_state starts at 1 (open), so start with close
        encoder_adc.cb(0.0, 0.2)  # close → state 0 (edge 1)
        encoder_adc.cb(0.1, 0.9)  # open  → state 1 (edge 2)
        encoder_adc.cb(0.2, 0.2)  # close → state 0 (edge 3)
        encoder_adc.cb(0.3, 0.9)  # open  → state 1 (edge 4)
        self.assertEqual(ft.get_status()["encoder_pulse"], 4)
        self.assertAlmostEqual(ft.get_status()["filament_distance"], 1.0)

    def test_filament_distance_zero_when_no_length_per_pulse(self):
        """filament_distance stays 0 when length_per_pulse is not set."""
        printer = FakePrinter(signal_type="gpio")
        config = FakeConfig(printer, {
            "signal_type": "gpio",
            "tracker_detect_pin": "PB0",
            "tracker_encoder_pin": "PA1",
            "absence_timeout": "1.0",
            "pause_on_runout": False,
        })
        ft = tracker.FilamentTracker(config)
        _, cb = printer.buttons.callbacks[0]
        cb(0.1, 0b10)  # encoder 0→1 (edge 1)
        cb(0.2, 0b00)  # encoder 1→0 (edge 2)
        self.assertEqual(ft.get_status()["encoder_pulse"], 2)
        self.assertAlmostEqual(ft.get_status()["filament_distance"], 0.0)

    def test_status_exposes_detection_length_config(self):
        """detection_length in status reflects the config threshold."""
        printer = FakePrinter(signal_type="gpio")
        config = FakeConfig(printer, {
            "signal_type": "gpio",
            "tracker_detect_pin": "PB0",
            "tracker_encoder_pin": "PA1",
            "absence_timeout": "1.0",
            "detection_length": "10.0",
            "pause_on_runout": False,
        })
        ft = tracker.FilamentTracker(config)
        self.assertAlmostEqual(ft.get_status()["detection_length"], 10.0)

    def test_pos_record_uses_filament_distance(self):
        """start_pos_record/get_pos_record track filament_distance delta."""
        printer = FakePrinter(signal_type="gpio")
        config = FakeConfig(printer, {
            "signal_type": "gpio",
            "tracker_detect_pin": "PB0",
            "tracker_encoder_pin": "PA1",
            "absence_timeout": "1.0",
            "length_per_pulse": "1.0",
            "pause_on_runout": False,
        })
        ft = tracker.FilamentTracker(config)
        _, cb = printer.buttons.callbacks[0]
        cb(0.1, 0b10)  # 1 pulse = 1mm
        ft.start_pos_record("start")
        cb(0.2, 0b00)  # 2 pulses = 2mm
        cb(0.3, 0b10)  # 3 pulses = 3mm
        self.assertAlmostEqual(ft.get_pos_record("start"), 2.0)


class MotionDetectionGpioTest(unittest.TestCase):
    """Tests for motion detection (clog/jam sensing) with GPIO."""

    def _make_tracker(self, **overrides):
        """Helper to create a tracker with motion detection enabled."""
        printer = FakePrinter(signal_type="gpio")
        values = {
            "signal_type": "gpio",
            "tracker_detect_pin": "PB0",
            "tracker_encoder_pin": "PA1",
            "absence_timeout": "1.0",
            "extruder": "extruder",
            "detection_length": "7.0",
            "length_per_pulse": "1.0",
            "pause_on_runout": False,
        }
        values.update(overrides)
        config = FakeConfig(printer, values)
        ft = tracker.FilamentTracker(config)
        return ft, printer

    def _fire_ready(self, printer):
        """Simulate klippy:ready event."""
        for cb in printer.event_handlers.get('klippy:ready', []):
            cb()

    def test_motion_detection_registers_events(self):
        """When extruder is configured, ready/printing events are registered."""
        ft, printer = self._make_tracker()
        self.assertIn('klippy:ready', printer.event_handlers)
        self.assertIn('idle_timeout:printing', printer.event_handlers)
        self.assertIn('idle_timeout:ready', printer.event_handlers)
        self.assertIn('idle_timeout:idle', printer.event_handlers)

    def test_motion_detection_not_registered_without_extruder(self):
        """Without extruder config, no motion detection events are registered."""
        printer = FakePrinter(signal_type="gpio")
        config = FakeConfig(printer, {
            "signal_type": "gpio",
            "tracker_detect_pin": "PB0",
            "tracker_encoder_pin": "PA1",
            "absence_timeout": "1.0",
            "pause_on_runout": False,
        })
        ft = tracker.FilamentTracker(config)
        # idle_timeout events are only registered for motion detection
        self.assertNotIn('idle_timeout:printing', printer.event_handlers)
        self.assertFalse(ft._motion_detection_enabled)

    def test_clog_detected_when_extruder_moves_past_threshold(self):
        """Extruder moves > detection_length without encoder → clog."""
        ft, printer = self._make_tracker(detection_length="5.0")
        self._fire_ready(printer)
        # Extruder starts at 0, runout_pos = 0 + 5 = 5
        # Simulate printing start
        for cb in printer.event_handlers.get('idle_timeout:printing', []):
            cb(0.0)
        # Extruder advances past threshold (no encoder activity)
        printer.extruder.position = 6.0
        printer.reactor.time = 1.0
        printer.reactor.fire_ready_timers()
        # RunoutHelper should have been told filament is NOT present (clog)
        self.assertFalse(ft.runout_helper.filament_present)

    def test_encoder_pulse_prevents_clog_detection(self):
        """Encoder activity resets the runout window, preventing false clog."""
        ft, printer = self._make_tracker(detection_length="5.0")
        self._fire_ready(printer)
        for cb in printer.event_handlers.get('idle_timeout:printing', []):
            cb(0.0)
        _, gpio_cb = printer.buttons.callbacks[0]
        # Extruder moves to 4.0 (under threshold)
        printer.extruder.position = 4.0
        # Encoder pulse → resets runout_pos to 4.0 + 5.0 = 9.0
        gpio_cb(1.0, 0b10)  # encoder edge
        # Extruder moves to 8.0 (< 9.0, still OK)
        printer.extruder.position = 8.0
        printer.reactor.time = 2.0
        printer.reactor.fire_ready_timers()
        # Should be present — encoder kept up
        self.assertTrue(ft.runout_helper.filament_present)

    def test_presence_absent_still_signals_runout_with_motion_enabled(self):
        """Physical filament removal signals RunoutHelper even with motion on."""
        ft, printer = self._make_tracker(detect_pin_is_switch=True)
        self._fire_ready(printer)
        _, gpio_cb = printer.buttons.callbacks[0]
        # Filament present initially
        gpio_cb(0.1, 0b01)  # detect=1
        self.assertTrue(ft.is_filament_present())
        # Filament physically removed
        gpio_cb(0.2, 0b00)  # detect=0
        self.assertFalse(ft.is_filament_present())
        # RunoutHelper should know immediately
        self.assertFalse(ft.runout_helper.filament_present)

    def test_presence_present_does_not_signal_runout_helper_with_motion(self):
        """With motion detection on, presence→present doesn't tell RunoutHelper."""
        ft, printer = self._make_tracker(detect_pin_is_switch=True)
        self._fire_ready(printer)
        _, gpio_cb = printer.buttons.callbacks[0]
        # Start absent
        self.assertFalse(ft.runout_helper.filament_present)
        # Detect switch sees filament (but no encoder pulse yet)
        gpio_cb(0.1, 0b01)  # detect=1
        self.assertTrue(ft.is_filament_present())
        # RunoutHelper still False — only encoder pulses signal presence
        self.assertFalse(ft.runout_helper.filament_present)
        # Encoder pulse → NOW RunoutHelper is True
        gpio_cb(0.2, 0b11)  # detect=1, encoder=1 (edge)
        self.assertTrue(ft.runout_helper.filament_present)

    def test_timer_stopped_when_not_printing(self):
        """Motion detection timer is NEVER when not printing."""
        ft, printer = self._make_tracker()
        self._fire_ready(printer)
        # Timer registered at NEVER initially
        timer = ft._extruder_pos_update_timer
        self.assertEqual(printer.reactor._timers[timer], printer.reactor.NEVER)
        # Start printing → timer activated
        for cb in printer.event_handlers.get('idle_timeout:printing', []):
            cb(0.0)
        self.assertNotEqual(printer.reactor._timers[timer],
                            printer.reactor.NEVER)
        # Stop printing → timer back to NEVER
        for cb in printer.event_handlers.get('idle_timeout:ready', []):
            cb(0.0)
        self.assertEqual(printer.reactor._timers[timer], printer.reactor.NEVER)

    def test_encoder_pulse_signals_present_to_runout_helper(self):
        """Each encoder pulse tells RunoutHelper filament is present."""
        ft, printer = self._make_tracker()
        self._fire_ready(printer)
        _, gpio_cb = printer.buttons.callbacks[0]
        self.assertFalse(ft.runout_helper.filament_present)
        # Encoder edge
        gpio_cb(1.0, 0b10)
        self.assertTrue(ft.runout_helper.filament_present)


class MotionDetectionAdcTest(unittest.TestCase):
    """Tests for motion detection with ADC signal type."""

    def _make_tracker(self, **overrides):
        printer = FakePrinter(signal_type="adc")
        values = {
            "signal_type": "adc",
            "tracker_detect_pin": "adc0",
            "tracker_encoder_pin": "adc1",
            "absence_timeout": "1.0",
            "extruder": "extruder",
            "detection_length": "5.0",
            "length_per_pulse": "0.5",
            "pause_on_runout": False,
        }
        values.update(overrides)
        config = FakeConfig(printer, values)
        ft = tracker.FilamentTracker(config)
        return ft, printer

    def _fire_ready(self, printer):
        for cb in printer.event_handlers.get('klippy:ready', []):
            cb()

    def test_adc_clog_detected(self):
        """ADC mode: extruder past threshold without encoder → clog."""
        ft, printer = self._make_tracker()
        self._fire_ready(printer)
        for cb in printer.event_handlers.get('idle_timeout:printing', []):
            cb(0.0)
        # Extruder moves past detection_length (5.0)
        printer.extruder.position = 6.0
        printer.reactor.time = 1.0
        printer.reactor.fire_ready_timers()
        self.assertFalse(ft.runout_helper.filament_present)

    def test_adc_encoder_pulse_resets_runout_window(self):
        """ADC encoder edge resets the runout window."""
        ft, printer = self._make_tracker()
        self._fire_ready(printer)
        for cb in printer.event_handlers.get('idle_timeout:printing', []):
            cb(0.0)
        encoder_adc = printer.pins.adcs["adc1"]
        # Encoder pulse at extruder pos 3.0 → new window = 3.0 + 5.0 = 8.0
        printer.extruder.position = 3.0
        encoder_adc.cb(0.9, 0.2)  # close → state 0 (edge)
        encoder_adc.cb(1.0, 0.9)  # open  → state 1 (edge, resets window)
        # Extruder at 7.0 (< 8.0) → no clog
        printer.extruder.position = 7.0
        printer.reactor.time = 2.0
        printer.reactor.fire_ready_timers()
        self.assertTrue(ft.runout_helper.filament_present)

    def test_adc_filament_distance_tracks_correctly(self):
        """ADC mode: filament_distance = pulses × length_per_pulse."""
        ft, printer = self._make_tracker()
        self._fire_ready(printer)
        encoder_adc = printer.pins.adcs["adc1"]
        # encoder_signal_state starts at 1 (open), so start with close
        encoder_adc.cb(0.0, 0.2)  # close → state 0 (edge 1)
        encoder_adc.cb(0.1, 0.9)  # open  → state 1 (edge 2)
        self.assertAlmostEqual(
            ft.get_status()["filament_distance"], 1.0)  # 2 × 0.5


class RunoutHelperIntegrationTest(unittest.TestCase):
    """Verify RunoutHelper fields are exposed in status and track presence."""

    def test_status_includes_filament_detected_and_enabled(self):
        """get_status() must include RunoutHelper's fields."""
        printer = FakePrinter(signal_type="gpio")
        config = FakeConfig(printer, {
            "signal_type": "gpio",
            "tracker_detect_pin": "PB0",
            "tracker_encoder_pin": "PA1",
            "absence_timeout": "1.0",
            "pause_on_runout": False,
        })
        ft = tracker.FilamentTracker(config)
        status = ft.get_status()
        self.assertIn("filament_detected", status)
        self.assertIn("enabled", status)
        self.assertIn("filament_present", status)
        self.assertIn("encoder_pulse", status)
        self.assertIn("filament_distance", status)
        self.assertIn("detection_length", status)

    def test_runout_helper_tracks_presence(self):
        """RunoutHelper.filament_present must follow tracker transitions."""
        printer = FakePrinter(signal_type="gpio")
        config = FakeConfig(printer, {
            "signal_type": "gpio",
            "tracker_detect_pin": "PB0",
            "tracker_encoder_pin": "PA1",
            "absence_timeout": "1.0",
            "pause_on_runout": False,
        })
        ft = tracker.FilamentTracker(config)
        _, cb = printer.buttons.callbacks[0]

        self.assertFalse(ft.runout_helper.filament_present)

        # Encoder activity → present
        cb(1.0, 0b01)
        self.assertTrue(ft.runout_helper.filament_present)

        # Both open → schedule absence
        cb(1.1, 0b00)
        printer.reactor.time = 2.2
        printer.reactor.fire_ready_timers()
        self.assertFalse(ft.runout_helper.filament_present)

    def test_sensor_enabled_defaults_true(self):
        """sensor_enabled on RunoutHelper must default to True."""
        printer = FakePrinter(signal_type="gpio")
        config = FakeConfig(printer, {
            "signal_type": "gpio",
            "tracker_detect_pin": "PB0",
            "tracker_encoder_pin": "PA1",
            "absence_timeout": "1.0",
            "pause_on_runout": False,
        })
        ft = tracker.FilamentTracker(config)
        self.assertTrue(ft.runout_helper.sensor_enabled)


class AreBothChannelsOpenGpioTest(unittest.TestCase):
    """Tests for are_both_channels_open property with GPIO signal type."""

    def setUp(self):
        self.printer = FakePrinter(signal_type="gpio")
        self.config = FakeConfig(self.printer, {
            "signal_type": "gpio",
            "tracker_detect_pin": "PB0",
            "tracker_encoder_pin": "PA1",
            "absence_timeout": "5.0",
            "pause_on_runout": False,
        })

    def test_both_open_returns_true(self):
        """Both channels open (state_bits=0) → are_both_channels_open is True."""
        ft = tracker.FilamentTracker(self.config)
        gpio_cb = self.printer.buttons.callbacks[0][1]
        # First give an edge so filament_present is latched
        gpio_cb(10.0, 0b01)
        self.assertTrue(ft.is_filament_present())
        # Now both open
        gpio_cb(10.1, 0b00)
        # filament_present is still True (timeout hasn't expired)
        self.assertTrue(ft.is_filament_present())
        # But are_both_channels_open is instantly True
        self.assertTrue(ft.are_both_channels_open)

    def test_one_channel_closed_returns_false(self):
        """At least one channel closed → are_both_channels_open is False."""
        ft = tracker.FilamentTracker(self.config)
        gpio_cb = self.printer.buttons.callbacks[0][1]
        gpio_cb(10.0, 0b01)  # detect closed
        self.assertFalse(ft.are_both_channels_open)
        gpio_cb(10.1, 0b10)  # encoder closed
        self.assertFalse(ft.are_both_channels_open)
        gpio_cb(10.2, 0b11)  # both closed
        self.assertFalse(ft.are_both_channels_open)

    def test_instant_vs_debounced_during_retraction(self):
        """Key scenario: after retraction, instant detects before debounced."""
        ft = tracker.FilamentTracker(self.config)
        gpio_cb = self.printer.buttons.callbacks[0][1]
        # Simulate encoder edges during retraction
        for t in range(20):
            gpio_cb(10.0 + t * 0.05, 0b01 if t % 2 == 0 else 0b10)
        self.assertTrue(ft.is_filament_present())
        # Filament tail passes — both channels open
        gpio_cb(11.0, 0b00)
        # Debounced: still "present" (timeout is 5s)
        self.assertTrue(ft.is_filament_present())
        # Instant: immediately "clear"
        self.assertTrue(ft.are_both_channels_open)


class AreBothChannelsOpenAdcTest(unittest.TestCase):
    """Tests for are_both_channels_open property with ADC signal type."""

    def setUp(self):
        self.printer = FakePrinter(signal_type="adc")
        self.config = FakeConfig(self.printer, {
            "signal_type": "adc",
            "tracker_detect_pin": "adc0",
            "tracker_encoder_pin": "adc1",
            "absence_timeout": "5.0",
            "pause_on_runout": False,
        })

    def test_both_above_threshold_returns_true(self):
        """Both ADC channels above threshold → both open → True."""
        ft = tracker.FilamentTracker(self.config)
        detect_adc = self.printer.pins.adcs["adc0"]
        encoder_adc = self.printer.pins.adcs["adc1"]
        # Give an edge first
        encoder_adc.cb(10.0, 0.2)  # closed
        encoder_adc.cb(10.1, 0.9)  # open, edge
        # Both open
        detect_adc.cb(10.2, 0.9)
        encoder_adc.cb(10.2, 0.9)
        self.assertTrue(ft.are_both_channels_open)
        # But debounced still present (within 5s timeout)
        self.assertTrue(ft.is_filament_present())

    def test_one_below_threshold_returns_false(self):
        """One channel below threshold → not both open → False."""
        ft = tracker.FilamentTracker(self.config)
        detect_adc = self.printer.pins.adcs["adc0"]
        encoder_adc = self.printer.pins.adcs["adc1"]
        detect_adc.cb(10.0, 0.5)   # closed
        encoder_adc.cb(10.0, 0.9)  # open
        self.assertFalse(ft.are_both_channels_open)

    def test_inverted_both_low_returns_true(self):
        """adc_inverted=True: both below threshold → both open → True."""
        config = FakeConfig(self.printer, {
            "signal_type": "adc",
            "tracker_detect_pin": "adc0",
            "tracker_encoder_pin": "adc1",
            "absence_timeout": "5.0",
            "adc_inverted": True,
            "pause_on_runout": False,
        })
        ft = tracker.FilamentTracker(config)
        detect_adc = self.printer.pins.adcs["adc0"]
        encoder_adc = self.printer.pins.adcs["adc1"]
        detect_adc.cb(10.0, 0.04)
        encoder_adc.cb(10.0, 0.04)
        self.assertTrue(ft.are_both_channels_open)

    def test_inverted_one_high_returns_false(self):
        """adc_inverted=True: one high voltage → closed → False."""
        config = FakeConfig(self.printer, {
            "signal_type": "adc",
            "tracker_detect_pin": "adc0",
            "tracker_encoder_pin": "adc1",
            "absence_timeout": "5.0",
            "adc_inverted": True,
            "pause_on_runout": False,
        })
        ft = tracker.FilamentTracker(config)
        detect_adc = self.printer.pins.adcs["adc0"]
        encoder_adc = self.printer.pins.adcs["adc1"]
        detect_adc.cb(10.0, 0.9)   # high = closed in inverted mode
        encoder_adc.cb(10.0, 0.04)
        self.assertFalse(ft.are_both_channels_open)


class AreBothChannelsOpenSwitchModeTest(unittest.TestCase):
    """In switch mode, are_both_channels_open delegates to filament_present."""

    def setUp(self):
        self.printer = FakePrinter(signal_type="gpio")
        self.config = FakeConfig(self.printer, {
            "signal_type": "gpio",
            "tracker_detect_pin": "PB0",
            "tracker_encoder_pin": "PA1",
            "detect_pin_is_switch": True,
            "absence_timeout": "1.0",
            "pause_on_runout": False,
        })

    def test_switch_open_returns_true(self):
        """Switch open (detect=0) → absent → are_both_channels_open True."""
        ft = tracker.FilamentTracker(self.config)
        gpio_cb = self.printer.buttons.callbacks[0][1]
        gpio_cb(10.0, 0b00)  # detect=0 → absent
        self.assertTrue(ft.are_both_channels_open)

    def test_switch_closed_returns_false(self):
        """Switch closed (detect=1) → present → are_both_channels_open False."""
        ft = tracker.FilamentTracker(self.config)
        gpio_cb = self.printer.buttons.callbacks[0][1]
        gpio_cb(10.0, 0b01)  # detect=1 → present
        self.assertFalse(ft.are_both_channels_open)


if __name__ == "__main__":
    unittest.main()
