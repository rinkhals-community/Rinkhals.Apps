# KS1 Custom Probe support (utilizing Klipper probe helpers)
#
# Copyright (C) 2025 Antiriad <mail.antiriad@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# Adapted for the mainline klipper probe.py rewrite that replaced the old
# ProbeEndstopWrapper/ProbeSessionHelper/HomingViaProbeHelper composition
# with per-probe "endstop" wrapper classes (see bltouch.py for the
# reference implementation this module now mirrors).

import logging
from . import probe

# KS1 "endstop" wrapper. Drives the CS1237 strain gauge ADC on/off around
# each probing attempt (instead of a physical actuator like bltouch's pin),
# then delegates the actual homing move to the standard
# probe.DescendToEndstopHelper using the MCU endstop pin.
class KS1ProbeEndstop:
    def __init__(self, config, probe_offsets, param_helper, ks1):
        self.printer = config.get_printer()
        self.ks1 = ks1
        self.param_helper = param_helper
        # Unlike the stock [probe], default to enabling/disabling the
        # strain gauge once per probe session (not per sample), since
        # enabling involves a 500ms settle dwell.
        self.stow_on_each_sample = config.getboolean(
            'deactivate_on_each_sample', False)
        # Create an "endstop" object to handle the probe pin
        ppins = self.printer.lookup_object('pins')
        self.mcu_endstop = ppins.setup_pin('endstop', config.get('pin'))
        self.get_mcu = self.mcu_endstop.get_mcu
        self.add_stepper = self.mcu_endstop.add_stepper
        self.get_steppers = self.mcu_endstop.get_steppers
        self.home_start = self.mcu_endstop.home_start
        self.home_wait = self.mcu_endstop.home_wait
        self.query_endstop = self.mcu_endstop.query_endstop
        # Probing via homing to endstop
        self.homing_helper = probe.DescendToEndstopHelper(
            config, self, probe_offsets, param_helper)
        # multi probe state
        self.multi = 'OFF'

    def _check_pre_triggered(self):
        """Guard against a stale/latched trigger before the probing move."""
        toolhead = self.printer.lookup_object("toolhead")
        reactor = self.printer.get_reactor()

        def _is_endstop_active():
            pt = toolhead.get_last_move_time() + 0.050  # 50 ms in the future
            try:
                return bool(self.query_endstop(pt))
            except Exception:
                return False

        pre_trig_1 = _is_endstop_active()
        if pre_trig_1:
            reactor.pause(reactor.monotonic() + 0.030)  # 30 ms settle
            pre_trig_2 = _is_endstop_active()
        else:
            pre_trig_2 = False

        if pre_trig_1 and pre_trig_2:
            logging.warning(
                "ProbeKS1: endstop TRIGGERED before move; lifting 2mm to clear")
            cur = toolhead.get_position()
            toolhead.manual_move(
                [cur[0], cur[1], cur[2] + 2.0], self.param_helper.lift_speed)
            toolhead.wait_moves()
            if _is_endstop_active():
                raise self.printer.command_error(
                    "ProbeKS1: Endstop still triggered before probing "
                    "after 2mm lift")

    def _enable_probe(self):
        logging.info("ProbeKS1: enabling CS1237 strain gauge sensor")
        cs1237 = self.ks1.cs1237
        # Reset sensor baseline before enabling for a fresh reference
        cs1237._cmd_reset.send([cs1237._oid, 3])
        cs1237._enable_cs1237(1)
        # 500ms stabilization dwell for EMA filter convergence
        gcode = self.printer.lookup_object('gcode')
        gcode.run_script_from_command('G4 P500')

    def _disable_probe(self):
        logging.info("ProbeKS1: disabling CS1237 strain gauge sensor")
        if self.ks1.cs1237 is not None:
            self.ks1.cs1237._enable_cs1237(0)

    def start_probe_session(self, gcmd):
        self.homing_helper.clear_trigger_positions()
        if not self.stow_on_each_sample:
            self.multi = 'FIRST'
        return self

    def _probe_prepare(self):
        if self.multi == 'OFF' or self.multi == 'FIRST':
            self._enable_probe()
            if self.multi == 'FIRST':
                self.multi = 'ON'
        self._check_pre_triggered()

    def _probe_finish(self):
        if self.multi == 'OFF':
            self._disable_probe()

    def run_probe(self, gcmd):
        self._probe_prepare()
        try:
            self.homing_helper.descend_until_trigger(gcmd)
        except self.printer.command_error:
            self._probe_finish()
            raise
        self._probe_finish()

    def pull_probed_results(self):
        return self.homing_helper.pull_trigger_positions()

    def end_probe_session(self):
        self.homing_helper.clear_trigger_positions()
        if not self.stow_on_each_sample:
            self._disable_probe()
            self.multi = 'OFF'


class ProbeKS1:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()

        # Currently not used/supported setting from printer.cfg.
        # As this module uses probe helpers for probing (and not a manual
        # approach), this setting is currently without any effect. It is
        # left active here so it's still possible to load a standard KS1
        # probe config without klipper complaining about an unused key.
        self.final_speed = config.getfloat("final_speed", 2.0, above=0.0)

        # CS1237 strain gauge sensor reference (deferred to connect handler)
        self.cs1237 = None
        self.printer.register_event_handler('klippy:connect',
                                            self._handle_connect)

        # Standard Klipper probe helper composition (mirrors probe.PrinterProbe
        # / bltouch.PrinterBLTouch in current mainline klipper)
        self.probe_offsets = probe.ProbeOffsetsHelper(config)
        self.param_helper = probe.ProbeParameterHelper(config)
        self.mcu_probe = KS1ProbeEndstop(
            config, self.probe_offsets, self.param_helper, self)
        self.probe_session = probe.SampleAveragingHelper(
            config, self.param_helper, self.mcu_probe.start_probe_session)
        self.query_endstop = self.mcu_probe.query_endstop
        self.cmd_helper = probe.ProbeCommandHelper(
            config, self, self.query_endstop)
        probe.HomingViaProbeHelper(
            config, self.probe_offsets.get_offsets()[2], self.query_endstop)

        # Register as the printer's probe object, so probe_ks1 gets
        # registered as the standard probe
        self.printer.add_object('probe', self)

    def _handle_connect(self):
        self.cs1237 = self.printer.lookup_object('cs1237', None)
        if self.cs1237 is None:
            raise self.printer.config_error(
                "ProbeKS1: [cs1237] section not found in printer config. "
                "The strain gauge sensor is required for safe probing.")
        logging.info("ProbeKS1: cs1237 sensor connected")

    # Interface for ProbeCommandHelper (PrinterProbe-alike)
    def get_probe_params(self, gcmd=None):
        return self.param_helper.get_probe_params(gcmd)

    def get_offsets(self, gcmd=None):
        return self.probe_offsets.get_offsets(gcmd)

    def get_status(self, eventtime):
        return self.cmd_helper.get_status(eventtime)

    def start_probe_session(self, gcmd):
        return self.probe_session.start_probe_session(gcmd)


def load_config(config):
    return ProbeKS1(config)
