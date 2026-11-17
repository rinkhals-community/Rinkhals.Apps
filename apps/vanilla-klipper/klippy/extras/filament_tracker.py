# extras/filament_tracker.py
#
# Dual-input filament tracker (ADC or GPIO) with optional motion
# detection (clog/jam sensing by comparing encoder vs extruder movement).
#
# Two hardware variants:
#   1. Dual-encoder (K3M): both pins are channels of a single rotary
#      encoder wheel.  Neither is a simple presence switch.
#   2. Switch + encoder (KS1): detect pin is a dedicated filament
#      presence switch; encoder pin counts filament movement.
#
# Presence logic:
#   detect_pin_is_switch = False (dual-encoder, default):
#     - Any encoder edge immediately latches filament_present = 1.
#     - Filament is declared absent only after BOTH channels read "open"
#       continuously for 'absence_timeout' seconds with no encoder edges.
#   detect_pin_is_switch = True (switch + encoder):
#     - Detect pin alone determines presence (open = absent, closed =
#       present).  No timeout needed.
#     - Encoder pin is used for pulse counting only.
#
# ADC polarity:
#   adc_inverted = False (default): voltage > threshold = open (no filament)
#   adc_inverted = True:            voltage < threshold = open (no filament)
#   Use adc_inverted = True when the sensor reads low voltage with no
#   filament (e.g. RDM hub on main MCU).  GPIO pins use the '!' prefix
#   in the pin name for inversion instead.
# - Pulses: counted from the encoder input.
# - Optional callback hook to notify other modules.
#
# Motion detection (enabled when ``extruder`` is configured):
#   Compares encoder pulse movement with extruder stepper movement.
#   If the extruder extrudes ``detection_length`` mm without any encoder
#   pulse, a clog/jam is reported through RunoutHelper.  Uses the same
#   config keys as Klipper's filament_motion_sensor for compatibility.

import logging
from . import filament_switch_sensor

ADC_REPORT_TIME       = 0.025   # seconds between ADC callbacks (40 Hz)
ADC_SAMPLE_TIME       = 0.001   # seconds per individual ADC sample
ADC_SAMPLE_COUNT      = 4       # samples to average per report (= 4 ms)
# NOTE: sample_count * sample_time MUST be well below report_time,
# otherwise the MCU will shutdown with "Rescheduled timer in the past".
# Current headroom: 25 ms - 4 ms = 21 ms margin.
ADC_REFER_VOLTAGE     = 0.70    # volts; above this = "open" on that channel
ABSENCE_TIMEOUT       = 5.0     # seconds; both-open must persist this long
CHECK_RUNOUT_TIMEOUT  = 0.250   # seconds between motion-detection checks

class FilamentTrackerStatus:
    """Mutable container for the filament tracker's runtime state.

    Attributes:
        filament_distance: Cumulative filament travel in mm as measured
            by the encoder (encoder_pulse × length_per_pulse).  Stays
            0.0 when length_per_pulse is not configured.
        filament_present: 1 when filament is detected, 0 when absent.
        encoder_pulse: Total rising/falling edge count from the
            encoder input.
        encoder_signal_state: Current digital level of the encoder
            channel (0 or 1).
    """
    def __init__(self):
        self.filament_distance = 0.0
        self.filament_present = 0
        self.encoder_pulse = 0
        # Start at 1 (open) to match the initial ADC value of 1.0,
        # preventing a false encoder edge on the first ADC callback.
        self.encoder_signal_state = 1

class FilamentTracker:
    """Dual-input filament tracker supporting ADC or GPIO signal types,
    with optional motion detection for clog/jam sensing.

    Monitors a detect pin and an encoder pin to determine filament
    presence and count encoder pulses.  When an ``extruder`` is
    configured, also compares encoder activity against extruder
    movement to detect clogs (filament present but not moving).

    Two hardware modes (``detect_pin_is_switch``):

    False (default, dual-encoder — K3M):
        Both pins are channels of a single rotary encoder wheel.
        Any encoder edge immediately latches filament as present.
        Absence is only declared after **both** channels read "open"
        continuously for ``absence_timeout`` seconds with no edges.

    True (switch + encoder — KS1):
        Detect pin is a dedicated filament presence switch.  It alone
        determines presence (open = absent, closed = present).
        Encoder pin is used only for pulse counting.

    Motion detection (optional, enabled by setting ``extruder``):
        Uses the same approach as Klipper's ``filament_motion_sensor``.
        On each encoder pulse the allowed-extrusion window is reset to
        ``extruder_pos + detection_length``.  A periodic timer checks
        whether the extruder has moved beyond that window without any
        encoder activity — if so, a clog/jam is signalled via
        RunoutHelper.

    Signal modes:
        adc  — Each pin is read via ADC; voltage > ADC_REFER_VOLTAGE
               means "open".
        gpio — Both pins are registered as digital buttons; a combined
               bitmask is decoded per callback.
    """
    def __init__(self, config):
        """Initialise the tracker from a Klipper config section.

        Args:
            config: A Klipper ConfigWrapper providing the keys
                ``tracker_detect_pin``, ``tracker_encoder_pin``,
                ``signal_type`` ("adc" or "gpio"),
                ``detect_pin_is_switch`` (bool, default False),
                ``safe_unwind_len``, ``length_per_pulse``, and
                optionally ``extruder`` + ``detection_length`` for
                motion detection.
        """
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.tracker_status = FilamentTrackerStatus()
        self.last_pos_map = {}
        self.callback = None

        self.safe_unwind_len = config.getfloat("safe_unwind_len", 100., above=0.)
        self.length_per_pulse = config.getfloat("length_per_pulse", 0., above=0.)
        self.signal_type = config.get("signal_type", "gpio")
        self._detect_pin_is_switch = config.getboolean(
            "detect_pin_is_switch", False)
        self._adc_inverted = config.getboolean("adc_inverted", False)
        # Initial ADC values represent "open" (no filament).
        # Normal: high voltage = open.  Inverted: low voltage = open.
        self._detect_adc_val = 0.0 if self._adc_inverted else 1.0
        self._encoder_adc_val = 0.0 if self._adc_inverted else 1.0
        self._absence_timeout = config.getfloat(
            "absence_timeout", ABSENCE_TIMEOUT, above=0.)
        self._last_edge_time = -self._absence_timeout
        # Zero-cost tracing: when disabled _trace is a no-op;
        # when enabled it logs state transitions at INFO level.
        self._debug_trace = config.getboolean("debug_trace", False)
        if self._debug_trace:
            self._trace = self._trace_enabled
            self._adc_trace_counter = 0
        else:
            self._trace = lambda *args: None

        # --- Motion detection (clog/jam sensing) ---
        # When 'extruder' is configured, we compare encoder activity
        # against extruder movement to detect filament jams, using the
        # same approach as Klipper's filament_motion_sensor.
        self._extruder_name = config.get("extruder", None)
        self._motion_detection_enabled = self._extruder_name is not None
        self._detection_length = config.getfloat(
            "detection_length", 7., above=0.)
        self._extruder = None
        self._estimated_print_time = None
        self._filament_runout_pos = None
        self._extruder_pos_update_timer = None

        # RunoutHelper gives us runout_gcode/insert_gcode support,
        # QUERY/SET_FILAMENT_SENSOR commands, and sensor_enabled toggle.
        self.runout_helper = filament_switch_sensor.RunoutHelper(config)
        self.get_status = self._get_status

        # Optional UI compatibility alias:
        # expose this object also as "filament_motion_sensor <name>"
        # so Moonraker/Mainsail can discover it through existing sensor
        # whitelists without requiring frontend patches.
        self._expose_as_motion_sensor = config.getboolean(
            "expose_as_filament_motion_sensor", True)
        self._register_motion_sensor_alias(config)

        self.detect_pin = config.get("tracker_detect_pin")
        self.encoder_pin = config.get("tracker_encoder_pin")
        if not self.detect_pin or not self.encoder_pin:
            raise config.error("tracker_detect_pin and tracker_encoder_pin are required")

        if self.signal_type == "adc":
            ppins = self.printer.lookup_object("pins")
            self._detect_adc_pin = ppins.setup_pin("adc", self.detect_pin)
            self._detect_adc_pin.setup_adc_sample(ADC_SAMPLE_TIME, ADC_SAMPLE_COUNT)
            self._detect_adc_pin.setup_adc_callback(ADC_REPORT_TIME, self._detect_adc_handler)

            self._encoder_adc_pin = ppins.setup_pin("adc", self.encoder_pin)
            self._encoder_adc_pin.setup_adc_sample(ADC_SAMPLE_TIME, ADC_SAMPLE_COUNT)
            self._encoder_adc_pin.setup_adc_callback(ADC_REPORT_TIME, self._encoder_adc_handler)
        else:
            buttons = self.printer.load_object(config, "buttons")
            buttons.register_buttons([self.detect_pin, self.encoder_pin],
                                     self._gpio_handler)
            self._last_gpio_state = 0
            self._absence_timer = None

        if self._motion_detection_enabled:
            self.printer.register_event_handler(
                'klippy:ready', self._handle_ready)
            self.printer.register_event_handler(
                'idle_timeout:printing', self._handle_printing)
            self.printer.register_event_handler(
                'idle_timeout:ready', self._handle_not_printing)
            self.printer.register_event_handler(
                'idle_timeout:idle', self._handle_not_printing)

    def _register_motion_sensor_alias(self, config):
        """Register a compatibility object alias for UI auto-discovery.

        If enabled, mirrors this tracker object under the equivalent
        ``filament_motion_sensor`` section name.
        """
        if not self._expose_as_motion_sensor:
            return
        section_name = config.get_name()
        if not section_name.startswith("filament_tracker"):
            return
        alias_name = section_name.replace(
            "filament_tracker", "filament_motion_sensor", 1)
        if self.printer.lookup_object(alias_name, None) is not None:
            logging.info(
                "filament_tracker: motion alias '%s' already exists;"
                " skipping compatibility alias", alias_name)
            return
        self.printer.add_object(alias_name, self)

    # Public API -------------------------------------------------------
    def register_callback(self, cb):
        """Register a callback invoked on filament presence changes.

        Args:
            cb: Callable(eventtime: float, is_present: int) called
                whenever filament_present transitions between 0 and 1.
        """
        self.callback = cb

    def is_filament_present(self):
        """Return True if filament is currently detected."""
        return bool(self.tracker_status.filament_present)

    @property
    def are_both_channels_open(self):
        """Instantaneous raw channel state — no absence timeout.

        Returns True when both the detect and encoder channels currently
        read "open" (no filament engaging the sensor wheel).  Unlike
        ``filament_present``, this does **not** wait for the absence
        timeout.

        In switch mode (``detect_pin_is_switch``), returns the inverse
        of the detect pin (already instantaneous).

        Use this when the caller *knows* filament was recently moving
        (e.g. during active retraction) and needs the fastest possible
        response.

        Returns:
            bool: True if both channels are open right now.
        """
        if self._detect_pin_is_switch:
            # Switch mode: detect pin is authoritative, already instant
            return not bool(self.tracker_status.filament_present)
        if self.signal_type == "adc":
            if self._adc_inverted:
                detect_open = self._detect_adc_val < ADC_REFER_VOLTAGE
                encoder_open = self._encoder_adc_val < ADC_REFER_VOLTAGE
            else:
                detect_open = self._detect_adc_val > ADC_REFER_VOLTAGE
                encoder_open = self._encoder_adc_val > ADC_REFER_VOLTAGE
            return detect_open and encoder_open
        else:
            # GPIO: _last_gpio_state is the latest bitmask
            return self._last_gpio_state == 0

    def start_pos_record(self, label):
        """Snapshot the current filament_distance under *label*.

        Args:
            label: Arbitrary string key identifying this snapshot.
        """
        self.last_pos_map[label] = self.tracker_status.filament_distance

    def get_pos_record(self, label):
        """Return the filament travel since the snapshot stored as *label*.

        Args:
            label: Key previously passed to :meth:`start_pos_record`.

        Returns:
            Delta in mm between the current filament_distance and the
            snapshot.
        """
        return self.tracker_status.filament_distance - self.last_pos_map[label]

    def _get_status(self, eventtime=0.):
        """Return a dict of the tracker's current state for Klipper status queries.

        Merges tracker-specific fields with RunoutHelper fields
        (``filament_detected``, ``enabled``) for full compatibility with
        both ``filament_tracker`` and ``filament_switch_sensor`` templates.
        """
        status = {
            "filament_distance": self.tracker_status.filament_distance,
            "filament_present": self.tracker_status.filament_present,
            "encoder_pulse": self.tracker_status.encoder_pulse,
            "encoder_signal_state": self.tracker_status.encoder_signal_state,
            "detection_length": self._detection_length,
        }
        status.update(self.runout_helper.get_status(eventtime))
        return status

    # Internal helpers -------------------------------------------------
    def _note_filament_present(self, present_int, eventtime=None):
        """Update filament presence, firing the callback on transitions.

        When motion detection is active, only *absence* is forwarded to
        RunoutHelper (immediate runout on physical removal).  Presence
        is signalled by encoder pulses instead, which confirm the
        filament is actually moving.

        Args:
            present_int: 1 if filament detected, 0 if absent.
            eventtime:   Monotonic timestamp; if *None*, reads the clock.
        """
        if present_int == self.tracker_status.filament_present:
            return
        self.tracker_status.filament_present = present_int
        self._trace(present_int)
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        if self._motion_detection_enabled:
            # Only forward absence — presence comes from encoder pulses.
            if not present_int:
                self.runout_helper.note_filament_present(eventtime, False)
        else:
            self.runout_helper.note_filament_present(
                eventtime, bool(present_int))
        if self.callback:
            self.callback(eventtime, present_int)

    def _on_encoder_pulse(self, eventtime):
        """Handle an encoder pulse: update distance and motion detection.

        Called after every encoder edge.  Updates ``filament_distance``
        from the new pulse count and, when motion detection is active,
        resets the runout window and tells RunoutHelper that filament is
        present (encoder activity = filament is moving).
        """
        self.tracker_status.filament_distance = (
            self.tracker_status.encoder_pulse * self.length_per_pulse)
        if self._motion_detection_enabled and self._extruder is not None:
            self._update_filament_runout_pos(eventtime)
            self.runout_helper.note_filament_present(eventtime, True)

    def _trace_enabled(self, present_int):
        """Log a filament presence transition (only bound when debug_trace is on)."""
        logging.info(
            "filament_tracker [%s]: filament %s"
            " (pulses=%d, last_edge=%.3f, timeout=%.1f)",
            self.signal_type,
            "PRESENT" if present_int else "ABSENT",
            self.tracker_status.encoder_pulse,
            self._last_edge_time,
            self._absence_timeout,
        )

    # ADC path
    def _detect_adc_handler(self, read_time, read_value):
        """ADC callback for the detect (presence) pin."""
        self._detect_adc_val = read_value
        self._update_adc_state(read_time)

    def _encoder_adc_handler(self, read_time, read_value):
        """ADC callback for the encoder pin."""
        self._encoder_adc_val = read_value
        self._update_adc_state(read_time)

    def _update_adc_state(self, eventtime):
        """Re-evaluate presence and encoder state from the latest ADC readings.

        When ``detect_pin_is_switch`` is True, the detect pin alone
        determines presence (open = absent, closed = present).  The
        encoder pin is used only for pulse counting.

        When False (default, dual-encoder mode), encoder edges
        immediately latch filament as present.  Absence is only
        reported when both channels read open *and* no edge has
        occurred within ``absence_timeout`` seconds.
        """
        if self._adc_inverted:
            detect_open = self._detect_adc_val < ADC_REFER_VOLTAGE
            encoder_open = self._encoder_adc_val < ADC_REFER_VOLTAGE
        else:
            detect_open = self._detect_adc_val > ADC_REFER_VOLTAGE
            encoder_open = self._encoder_adc_val > ADC_REFER_VOLTAGE

        # Count encoder edges from the encoder channel
        encoder_state = 1 if encoder_open else 0
        if encoder_state != self.tracker_status.encoder_signal_state:
            self.tracker_status.encoder_signal_state = encoder_state
            self.tracker_status.encoder_pulse += 1
            self._last_edge_time = eventtime
            self._on_encoder_pulse(eventtime)
            if self._debug_trace:
                logging.info(
                    "filament_tracker ADC EDGE: t=%.3f detect=%.4f"
                    " encoder=%.4f new_state=%d pulses=%d",
                    eventtime, self._detect_adc_val,
                    self._encoder_adc_val, encoder_state,
                    self.tracker_status.encoder_pulse)
            if not self._detect_pin_is_switch:
                # Dual-encoder: edge means filament is moving
                self._note_filament_present(1, eventtime)
            if self.tracker_status.encoder_pulse % 20 == 0:
                logging.debug("filament tracker encoder pulse: %d",
                              self.tracker_status.encoder_pulse)
        elif self._debug_trace:
            # Periodic ADC value log (every 40th call ≈ once/sec at 40Hz)
            self._adc_trace_counter += 1
            if self._adc_trace_counter % 40 == 0:
                age = eventtime - self._last_edge_time
                logging.info(
                    "filament_tracker ADC POLL: t=%.3f detect=%.4f(%s)"
                    " encoder=%.4f(%s) present=%d edge_age=%.1fs"
                    " timeout=%.1fs",
                    eventtime, self._detect_adc_val,
                    "open" if detect_open else "CLOSED",
                    self._encoder_adc_val,
                    "open" if encoder_open else "CLOSED",
                    self.tracker_status.filament_present,
                    age, self._absence_timeout)

        # Presence logic
        if self._detect_pin_is_switch:
            # Switch mode: detect pin is authoritative
            if detect_open:
                self._note_filament_present(0, eventtime)
            else:
                self._note_filament_present(1, eventtime)
        else:
            # Dual-encoder mode: absent only after both open for > timeout
            if detect_open and encoder_open:
                if eventtime - self._last_edge_time > self._absence_timeout:
                    self._note_filament_present(0, eventtime)
            else:
                self._note_filament_present(1, eventtime)

    # GPIO path
    def _gpio_handler(self, eventtime, state_bits):
        """GPIO callback receiving a bitmask of both pins.

        Args:
            eventtime: Monotonic timestamp of the event.
            state_bits: Bitmask — bit 0 = detect pin, bit 1 = encoder pin.

        In switch mode (``detect_pin_is_switch``), bit 0 alone determines
        presence.  In dual-encoder mode, any edge latches present;
        both-zero starts a delayed absence timer.
        """
        # Count encoder edges from the encoder bit
        encoder_bit = (state_bits >> 1) & 1
        last_encoder_bit = (self._last_gpio_state >> 1) & 1
        if encoder_bit != last_encoder_bit:
            self.tracker_status.encoder_signal_state = encoder_bit
            self.tracker_status.encoder_pulse += 1
            self._last_edge_time = eventtime
            self._on_encoder_pulse(eventtime)
        self._last_gpio_state = state_bits

        if self._detect_pin_is_switch:
            # Switch mode: detect bit (bit 0) is authoritative
            detect_bit = state_bits & 1
            if detect_bit:
                self._note_filament_present(1, eventtime)
            else:
                self._note_filament_present(0, eventtime)
        else:
            # Dual-encoder mode
            if state_bits:
                # At least one channel active → filament present
                self._note_filament_present(1, eventtime)
                if self._absence_timer is not None:
                    self.reactor.update_timer(self._absence_timer,
                                              self.reactor.NEVER)
            else:
                # Both channels open — schedule delayed absence check
                if self._absence_timer is None:
                    self._absence_timer = self.reactor.register_timer(
                        self._gpio_absence_event)
                self.reactor.update_timer(
                    self._absence_timer,
                    eventtime + self._absence_timeout)

    def _gpio_absence_event(self, eventtime):
        """Timer callback: declare filament absent if no recent edges."""
        if eventtime - self._last_edge_time >= self._absence_timeout:
            self._note_filament_present(0, eventtime)
        return self.reactor.NEVER

    # Motion detection (clog/jam sensing) --------------------------------
    def _handle_ready(self):
        """Look up the extruder and prepare the motion detection timer."""
        self._extruder = self.printer.lookup_object(self._extruder_name)
        self._estimated_print_time = (
            self.printer.lookup_object('mcu').estimated_print_time)
        self._update_filament_runout_pos()
        self._extruder_pos_update_timer = self.reactor.register_timer(
            self._extruder_pos_update_event)

    def _handle_printing(self, print_time):
        """Start the motion detection timer when printing begins."""
        self.reactor.update_timer(
            self._extruder_pos_update_timer, self.reactor.NOW)

    def _handle_not_printing(self, print_time):
        """Stop the motion detection timer when printing stops."""
        self.reactor.update_timer(
            self._extruder_pos_update_timer, self.reactor.NEVER)

    def _get_extruder_pos(self, eventtime=None):
        """Return the extruder stepper position in mm at *eventtime*."""
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        print_time = self._estimated_print_time(eventtime)
        return self._extruder.find_past_position(print_time)

    def _update_filament_runout_pos(self, eventtime=None):
        """Reset the runout window to current extruder pos + detection_length."""
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        self._filament_runout_pos = (
            self._get_extruder_pos(eventtime) + self._detection_length)

    def _extruder_pos_update_event(self, eventtime):
        """Periodic timer: check extruder pos against runout window.

        If the extruder has moved past ``filament_runout_pos`` without
        any encoder activity, signal a clog to RunoutHelper.
        """
        extruder_pos = self._get_extruder_pos(eventtime)
        self.runout_helper.note_filament_present(
            eventtime, extruder_pos < self._filament_runout_pos)
        return eventtime + CHECK_RUNOUT_TIMEOUT

def load_config_filament_tracker(config):
    """Klipper config loader entry point for bare [filament_tracker] sections."""
    return FilamentTracker(config)

def load_config_prefix(config):
    """Klipper config loader for named [filament_tracker <name>] sections."""
    return FilamentTracker(config)

