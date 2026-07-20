# CS1237 support 
#
# Copyright (C) 2025 Antiriad <mail.antiriad@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import ctypes


# State and error constants
SELF_CHECK_STATE      = 1 << 0
SCRATCH_STATE         = 1 << 1
BLOCK_FILAMENT_STATE  = 1 << 2
HEAD_BLOCK_STATE      = 1 << 3
SELF_CHECK_ERR        = SELF_CHECK_STATE      | 0x80
SCRATCH_STATE_ERR     = SCRATCH_STATE         | 0x80
BLOCK_FILAMENT_ERR    = BLOCK_FILAMENT_STATE  | 0x80
HEAD_BLOCK_STATE_ERR  = HEAD_BLOCK_STATE      | 0x80


class CS1237:
    head_block_sensitivity     = 0
    scratch_sensitivity        = 0
    self_check_sensitivity     = 0
    block_filament_sensitivity = 0
    
    def __init__(self, config):
        # Core references
        self.BLOCK_FILAMENT_STATE = BLOCK_FILAMENT_STATE
        self.SCRATCH_STATE = SCRATCH_STATE
        self.SELF_CHECK_STATE = SELF_CHECK_STATE
        self.HEAD_BLOCK_STATE = HEAD_BLOCK_STATE
        
        self.printer = config.get_printer()
        self.mcu = config.get_printer().lookup_object('mcu nozzle_mcu')
        ppins       = self.printer.lookup_object('pins')

        # Configuration parameters
        self.level_pin_name      = config.get('level_pin', None)
        self.dout_pin_name       = config.get('dout_pin')
        self.sclk_pin_name       = config.get('sclk_pin')
        self.register_address    = config.getint('register', default=60)
        self.sensitivity         = config.getint('sensitivity', default=-2500)
        self._samples_per_second = config.getint('samples_per_second', default=10)
        self._range              = (-0x800000, 0x7FFFFF)

        # Threshold sensitivities
        self.head_block_sensitivity = config.getint('head_block_sensitivity', default=-300000)
        self.scratch_sensitivity = config.getint('scratch_sensitivity', default=-100000)  
        self.self_check_sensitivity = config.getint('self_check_sensitivity', default=-400)
        self.block_filament_sensitivity = config.getint('block_filament_sensitivity', default=-3000)

        # Resolve pin numbers
        def _get_pin(name):
            return ppins.lookup_pin(name, True, True, None)['pin']

        self.level_pin = _get_pin(self.level_pin_name) if self.level_pin_name else 0
        self.dout_pin  = _get_pin(self.dout_pin_name)
        self.sclk_pin  = _get_pin(self.sclk_pin_name)

        # State variables
        self.adc_value       = None
        self.raw_value       = None
        self.sensor_state    = None
        self.checkself_flag  = None
        self._clients        = []
        self._reporting      = False
        self._query_complete = None
        self._enable_count   = 0
        self.capabilities    = set()

        # Register config callback for MCU command setup
        self.mcu.register_config_callback(self._build_config)

        # Register G-code commands
        gcode = self.printer.lookup_object('gcode')
        # Note: Klipper's extended command parser rejects digits inside names;
        # use digit-free aliases for this module.
        gcode.register_command('CS_ADC',      self.cmd_dump, when_not_ready=False, desc='')
        gcode.register_command('G9121',       self.cmd_g9121, False, '')
        gcode.register_command('G9122',       self.cmd_g9122, False, '')
        gcode.register_command('G9123',       self.cmd_g9123, False, '')
        gcode.register_command('CS_ADC_DUMP', self.cmd_dump, False, 'debug CS1237 DUMP')

        # Dedicated MCU test commands
        gcode.register_command('CS_ADC_CHECKSELF', self.cmd_checkself_cs1237, False, 'Trigger checkself_cs1237 MCU command')
        gcode.register_command('CS_ADC_CONFIG',    self.cmd_config_cs1237, False, 'Trigger config_cs1237 MCU command')
        gcode.register_command('CS_ADC_RESET',     self.cmd_reset_cs1237, False, 'Trigger reset_cs1237 MCU command')
        gcode.register_command('CS_ADC_REPORT',    self.cmd_start_cs1237_report, False, 'Trigger start_cs1237_report MCU command')
        gcode.register_command('CS_ADC_DIFF',      self.cmd_query_cs1237_diff, False, 'Trigger query_cs1237_diff MCU command')
        gcode.register_command('CS_ADC_ENABLE',    self.cmd_enable_cs1237, False, 'Trigger enable_cs1237 MCU command')

        # Register event handlers
        self.printer.register_event_handler('klippy:ready', self._handle_ready)

    def _handle_do_ready(self, eventtime):
        """Boot-time self-check: temporarily enables sensor, then disables."""
        params = None
        try:
            self._cmd_reset.send([self._oid, 3])

            reactor = self.printer.get_reactor()
            reactor.pause(reactor.monotonic() + 0.1)

            # Temporarily enable for self-check only (bypass ref-count)
            self._enable_cs1237(1)
            self._query_complete = reactor.completion()
            self.cmd_checkself.send([self._oid, 0])

            params = self._query_complete.wait(reactor.monotonic() + 2.0)
        except Exception:
            logging.exception("CS1237: Error in _handle_ready")
        finally:
            # Always disable after self-check
            self._enable_cs1237(0)
            self._query_complete = None

        if params is None:
            logging.warning("CS1237: boot self-check timed out (no response in 2s)")
        else:
            state = int(params.get('flag', 0))
            if state == 0:
                logging.warning(
                    "CS1237: boot self-check returned flag=0 "
                    "(flash at 0x08007800 may not have been written by factory calibration)")
            else:
                logging.info("CS1237: boot self-check passed (flag=%d)", state)

    def _handle_ready(self, *args):
        # Defer to reactor callback: klippy:ready runs with pause disabled.
        self.printer.get_reactor().register_callback(self._handle_do_ready)

    def cmd_enable_cs1237(self, gcmd):
        state = gcmd.get_int('STATE', 1, minval=0, maxval=1)
        self._enable_cs1237(state)
        gcmd.respond_info(f"CS_ADC_ENABLE state={state}")

    def _enable_cs1237(self, state=1):
        """Send raw enable/disable command to MCU."""
        self._cmd_enable.send([self._oid, state])
        logging.info("[CS1237] _enable_cs1237 STATE=%d enable_count=%d",
                     state, self._enable_count)
    
    # Dedicated G-code command handlers for MCU commands
    def cmd_checkself_cs1237(self, gcmd):
        w = gcmd.get_int('W', 0, minval=0, maxval=3)
        self.cmd_checkself.send([
            self._oid,
            w
        ])
        logging.info(f"[CS1237] Sent checkself_cs1237 with W={w}")
        gcmd.respond_info(f"CS_ADC_CHECKSELF sent W={w}")
    def cmd_config_cs1237(self, gcmd):
        cfg = (
            f"config_cs1237 oid={self._oid} "
            f"level_pin={self.level_pin} dout_pin={self.dout_pin} sclk_pin={self.sclk_pin} "
            f"register={self.register_address} sensitivity={self.sensitivity}"
        )
        self.mcu.add_config_cmd(cfg)
        gcmd.respond_info("CS_ADC_CONFIG queued")

    def cmd_reset_cs1237(self, gcmd):
        count = gcmd.get_int('COUNT', 3, minval=1, maxval=10)
        self._cmd_reset.send([
            self._oid,
            count
        ])
        gcmd.respond_info(f"CS_ADC_RESET sent count={count}")

    def cmd_start_cs1237_report(self, gcmd):
        ticks = gcmd.get_int('TICKS', self.mcu.seconds_to_clock(1.0 / self._samples_per_second))
        print_state = gcmd.get_int('STATE', 0, minval=0, maxval=3)
        sensitivity = gcmd.get_int('SENS', self.sensitivity)
        self.cmd_start_report.send([
            self._oid,
            1,
            ticks,
            print_state,
            sensitivity
        ])
        gcmd.respond_info(f"CS_ADC_REPORT enable=1 ticks={ticks} state={print_state} sens={sensitivity}")

    def cmd_query_cs1237_diff(self, gcmd):
        self._cmd_query_diff.send([
            self._oid
        ])
        self.printer.register_event_handler('cs1237:self_check', self._self_check_sequence)
        gcmd.respond_info("CS_ADC_DIFF requested")

    def cmd_cs1237_calibration_phase(self, gcmd):
        cali_state = gcmd.get_int('CALI_STATE', minval=0, maxval=255)
        speed_state = gcmd.get_int('SPEED_STATE', minval=0, maxval=255)
        self.cs1237_calibration_phase(cali_state, speed_state)
        gcmd.respond_info(
            f"CS_ADC_CAL_PHASE sent cali_state={cali_state} speed_state={speed_state}")

    def cmd_cs1237_calibration_data(self, gcmd):
        timeout = gcmd.get_float('TIMEOUT', 2.0, above=0.0)
        params = self.cs1237_calibration_data_process(timeout=timeout)
        gcmd.respond_info(
            "CS_ADC_CAL_DATA "
            f"BlockPreVal={params['BlockPreVal']} "
            f"TargetVal={params['TargetVal']} "
            f"RealVal={params['RealVal']}")

    def _build_config(self):
        self._oid = self.mcu.create_oid()
        cfg = (
            f"config_cs1237 oid={self._oid} "
            f"level_pin={self.level_pin} dout_pin={self.dout_pin} sclk_pin={self.sclk_pin} "
            f"register={self.register_address} sensitivity={self.sensitivity}"
        )

        self.mcu.add_config_cmd(cfg)
        self.cmd_start_report = self.mcu.lookup_command(
            "start_cs1237_report oid=%c enable=%c ticks=%i print_state=%c sensitivity=%i"
        )
        self.cmd_checkself = self.mcu.lookup_command(
            "checkself_cs1237 oid=%c write=%c"
        )
        self._cmd_query_diff = self.mcu.lookup_command(
            "query_cs1237_diff oid=%c"
        )
        self._cmd_enable = self.mcu.lookup_command("enable_cs1237 oid=%c state=%c")
        self._cmd_reset  = self.mcu.lookup_command("reset_cs1237 oid=%c count=%c")

        # Ensure sensor is disabled after config (guards against warm restart
        # leaving EXTI enabled and level_out pin in stale triggered state)
        self.mcu.add_config_cmd(
            f"enable_cs1237 oid={self._oid} state=0"
        )

        self.mcu.register_serial_response(
            self._handle_cs1237_report,
            'cs1237_state oid=%c adc=%i raw=%i state=%c',
            self._oid,
        )
        self.mcu.register_serial_response(
            self._handle_cs1237_diff,
            'cs1237_diff oid=%c diff=%i raw=%i',
            self._oid,
        )
        self.mcu.register_serial_response(
            self._handle_cs1237_check,
            'cs1237_checkself_flag oid=%c flag=%c',
            self._oid,
        )
        self._maybe_enable_stock_calibration()

    def _handle_start_report_ack(self, params):
        logging.info(f"[CS1237] start_cs1237_report ACK received: {params}")

    def _handle_checkself_ack(self, params):
        logging.info(f"[CS1237] checkself_cs1237 ACK received: {params}")

    # ---- MCU Response Handlers ----
    def _int32_conversion(self, value):
        return ctypes.c_int32(value).value

    def _handle_cs1237_report(self, params):
        try:
            self.adc_value = self._int32_conversion(int(params.get('adc', 0)))
            self.raw_value = self._int32_conversion(int(params.get('raw', 0)))
            self.sensor_state = int(params.get('state', 0))
            now = self.printer.get_reactor().monotonic()
            logging.info(f"[CS1237 REPORT] adc={self.adc_value} raw={self.raw_value} state={self.sensor_state}")

            msg = {
                'timestamp': now,
                'adc': self.adc_value,
                'raw': self.raw_value,
                'state': self.sensor_state
            }
            
            # Process sensor state events
            if self.sensor_state == SCRATCH_STATE_ERR:
                logging.info("CS1237: SCRATCH_STATE_ERR detected")
                self.printer.send_event("CS1237:scratch_notice", None)
            elif self.sensor_state == BLOCK_FILAMENT_ERR:
                logging.info("CS1237: BLOCK_FILAMENT_ERR detected")
                self.printer.send_event("CS1237:block_filament_notice", None)
            elif self.sensor_state == HEAD_BLOCK_STATE_ERR:
                logging.info("CS1237: HEAD_BLOCK_STATE_ERR detected")
                self.printer.send_event("CS1237:head_block_notice", None)
                
            # Notify clients
            for cb in list(self._clients):
                if cb(msg) is False:
                    self._clients.remove(cb)
        except Exception:
            logging.exception("CS1237: report handler error")

    def _handle_cs1237_diff(self, params):
        try:
            raw_val = self._int32_conversion(int(params.get('raw', 0)))
            diff_val = self._int32_conversion(int(params.get('diff', 0)))
            self.raw_value = raw_val

            logging.info(
                "[CS1237] diff raw=%d diff=%d",
                raw_val, diff_val
            )

            if self._query_complete:
                self._query_complete.complete(params)
        except Exception:
            logging.exception("CS1237: diff handler error")


    def _handle_cs1237_check(self, params):
        try:
            self.checkself_flag = int(params.get('flag', 0))
            if self._query_complete:
                self._query_complete.complete(params)
        except Exception:
            logging.exception("CS1237: self-check handler error")

    def _handle_cs1237_calibration_val(self, params):
        try:
            logging.info(
                "[CS1237 CALTRACE] calibration_Val raw params keys=%s vals=%s",
                list(params.keys()) if hasattr(params, 'keys') else type(params).__name__,
                {k: v for k, v in params.items()} if hasattr(params, 'items') else params,
            )
            block_pre_val = self._int32_conversion(
                int(params.get('BlockPreVal', 0))
            )
            target_val = self._int32_conversion(int(params.get('TargetVal', 0)))
            real_val = self._int32_conversion(int(params.get('RealVal', 0)))

            payload = {
                'BlockPreVal': block_pre_val,
                'TargetVal': target_val,
                'RealVal': real_val,
            }
            logging.info(
                "[CS1237 CALTRACE] cal raw=(%d,%d,%d) delta=%d",
                block_pre_val, target_val, real_val,
                real_val - target_val,
            )
            if self._query_complete:
                self._query_complete.complete(payload)
        except Exception:
            logging.exception("CS1237: calibration value handler error")

    def cs1237_diff_process(self, timeout=2.0):
        """Request the current stock diff/raw pair and wait for reply."""
        if self._query_complete is not None:
            raise self.printer.command_error(
                "CS1237: another synchronous query is already pending")
        reactor = self.printer.get_reactor()
        params = None
        try:
            self._query_complete = reactor.completion()
            self._cmd_query_diff.send([self._oid])
            params = self._query_complete.wait(reactor.monotonic() + timeout)
        finally:
            self._query_complete = None
        if params is None:
            raise self.printer.command_error("CS1237: diff query timed out")
        return {
            'diff': self._int32_conversion(int(params.get('diff', 0))),
            'raw': self._int32_conversion(int(params.get('raw', 0))),
        }

    def _stock_cs1237_calibration_phase(self, cali_state, speed_state):
        self._cmd_calibration_phase.send([
            self._oid,
            int(cali_state) & 0xff,
            int(speed_state) & 0xff,
        ])
        logging.info(
            "[CS1237 CALTRACE] phase cali_state=%d speed_state=%d",
            cali_state, speed_state)

    def _stock_cs1237_calibration_data_process(self, timeout=2.0):
        if self._query_complete is not None:
            raise self.printer.command_error(
                "CS1237: another synchronous query is already pending")
        reactor = self.printer.get_reactor()
        params = None
        try:
            self._query_complete = reactor.completion()
            self._cmd_calibration_data.send([self._oid])
            params = self._query_complete.wait(reactor.monotonic() + timeout)
        finally:
            self._query_complete = None
        if params is None:
            raise self.printer.command_error(
                "CS1237: calibration data timed out")
        return {
            'BlockPreVal': self._int32_conversion(int(params.get('BlockPreVal', 0))),
            'TargetVal': self._int32_conversion(int(params.get('TargetVal', 0))),
            'RealVal': self._int32_conversion(int(params.get('RealVal', 0))),
        }


    # ---- Status ----
    def get_status(self, eventtime=None):
        if not self._reporting:
            return {}
        
        # Return status data when reporting is enabled
        return {
            'adc': self.adc_value,
            'raw': self.raw_value,
            'state': self.sensor_state,
            'checkself_flag': self.checkself_flag
        }
    
    def stats(self, eventtime):
        return False, f"cs1237:adc_value={self.adc_value} raw_value={self.raw_value} sensor_state={self.sensor_state}"

    # ---- Reporting Control ----
    def start_reporting(self):
        if self._reporting:
            return
        self._reporting = True
        ticks = self.mcu.seconds_to_clock(1.0 / self._samples_per_second)
        self.cmd_start_report.send([
            self._oid,
            1,
            ticks,
            0,
            self.sensitivity
        ])
    
    def stop_reporting(self):
        if not self._reporting:
            return
        self._reporting = False
        self.cmd_start_report.send([
            self._oid,
            0,
            0,
            0,
            self.sensitivity
        ])

    def add_client(self, callback):
        if callback not in self._clients:
            self._clients.append(callback)
        self.start_reporting()

    def remove_client(self, callback):
        if callback in self._clients:
            self._clients.remove(callback)
        if not self._clients:
            self.stop_reporting()

    # ---- G-Code Commands ----

    def cmd_dump(self, gcmd):
        e = gcmd.get_int('E', 0, minval=0, maxval=1)
        t = gcmd.get_float('T', 1.0, minval=0.1, maxval=1.0)
        s = gcmd.get_int('S', SELF_CHECK_STATE, minval=SELF_CHECK_STATE, maxval=HEAD_BLOCK_STATE)
        # map thresholds
        sensitivity_map = {
            SELF_CHECK_STATE:     self.self_check_sensitivity,
            SCRATCH_STATE:        self.scratch_sensitivity,
            BLOCK_FILAMENT_STATE: self.block_filament_sensitivity,
            HEAD_BLOCK_STATE:     self.head_block_sensitivity
        }
        sens = sensitivity_map.get(s, self.sensitivity)

        if e:
            # Ref-counted enable: only sends MCU command on first enable
            if self._enable_count == 0:
                self._enable_cs1237(1)
            self._enable_count += 1
            self.start_reporting()
            # Instruct MCU to report cs1237_state every T seconds
            self._check_start(t, s, sens, 0)
            gcmd.respond_info(f"CS_ADC reporting enabled state={s} sens={sens} period={t}")
        else:
            # First tell MCU to stop cs1237_state messages
            self._check_stop(s)
            # Then stop host-side reporting
            self.stop_reporting()
            # Ref-counted disable: only sends MCU command on last disable
            self._enable_count -= 1
            if self._enable_count <= 0:
                self._enable_count = 0
                self._enable_cs1237(0)
            gcmd.respond_info("CS_ADC reporting disabled")

    def cmd_g9121(self, gcmd):
        e = gcmd.get_int('E', 0, minval=0, maxval=1)
        r = gcmd.get_int('R', 0, minval=0, maxval=1)
        self._reporting = r > 0
        t = gcmd.get_float('T', 1.0, minval=0.1, maxval=1.0)
        
        if e:
            self._enable_cs1237(1)
            self._check_start(t, SCRATCH_STATE, self.scratch_sensitivity, 0)
        else:
            self._check_stop(SCRATCH_STATE)
            self._enable_cs1237(0)
    
    def cmd_g9122(self, gcmd):
        e = gcmd.get_int('E', 0, minval=0, maxval=1)
        r = gcmd.get_int('R', 0, minval=0, maxval=1)
        self._reporting = r > 0
        t = gcmd.get_float('T', 1.0, minval=0.1, maxval=1.0)
        
        if e:
            self._enable_cs1237(1)
            self._check_start(t, SELF_CHECK_STATE, self.self_check_sensitivity, 0)
        else:
            self._check_stop(SELF_CHECK_STATE)
            self._enable_cs1237(0)

    def cmd_g9123(self, gcmd):
        reactor = self.printer.get_reactor()
        params = None
        try:
            w = gcmd.get_int('W', 0, minval=0, maxval=3)
            self._query_complete = reactor.completion()
            self.cmd_checkself.send([self._oid, w])

            params = self._query_complete.wait(reactor.monotonic() + 2.0)
            if params:
                self.checkself_flag = int(params.get('flag', 0))
                gcode = self.printer.lookup_object('gcode')
                gcode.respond_info(f"NozzleTestFlag:{self.checkself_flag}")
        finally:
            self._query_complete = None


    # ---- Internal Helpers ----
    def _check_start(self, check_period, check_type, sensitivity, delay_send_time=0):
        ticks = self.mcu.seconds_to_clock(check_period)
        delay_t = self.mcu.seconds_to_clock(delay_send_time) if delay_send_time else 0
        
        self.cmd_start_report.send([
            self._oid,
            1,
            ticks,
            check_type,
            sensitivity
        ], minclock=delay_t)

    def _check_stop(self, check_type):
        self.cmd_start_report.send([
            self._oid,
            0,
            0,
            check_type,
            0
        ])

    # ---- Startup Self-Check ----
    def _self_check_sequence(self, _args):
        # Reset before self-check
        self._cmd_reset.send([self._oid, 3])
        
        # Small delay after reset
        self.printer.get_reactor().pause(
            self.printer.get_reactor().monotonic() + 0.1
        )
        
        # Enable sensor for self-check
        self._enable_cs1237(1)
        try:
            self._check_start(1.0, SELF_CHECK_STATE,
                              self.self_check_sensitivity, 0)
            
            self._self_check_resonances()
            
            # Wait for moves to complete
            toolhead = self.printer.lookup_object('toolhead')
            toolhead.wait_moves()
            
            # Pause for 3 seconds to collect data
            self.printer.get_reactor().pause(
                self.printer.get_reactor().monotonic() + 3.0
            )
            
            # Stop the check
            self._check_stop(SELF_CHECK_STATE)
        finally:
            # Always disable sensor after self-check
            self._enable_cs1237(0)
        
        # Check result and raise error if needed
        if self.sensor_state == SELF_CHECK_ERR:
            raise Exception("CS1237 boot up self-check failed")

    def _self_check_resonances(self):
        try:
            gcode = self.printer.lookup_object('gcode')
            toolhead = self.printer.lookup_object('toolhead')
            pos = toolhead.get_position()
            
            cmd = f"SPECIALIZED_TEST_RESONANCES axis=x FREQ_START=45 FREQ_END=55 HZ_PER_SEC=10 POINT={pos[0]},{pos[1]},{pos[2]}"
            gcode.run_script_from_command(cmd)
        except Exception:
            logging.exception("CS1237: Error running resonance test")

    def _maybe_enable_stock_calibration(self):
        phase_cmd = self.mcu.try_lookup_command(
            "cs1237_calibration_phase oid=%c cali_state=%c speed_state=%c"
        )
        data_cmd = self.mcu.try_lookup_command(
            "cs1237_calibration_DataProcess oid=%c"
        )
        has_response = self.mcu.check_valid_response(
            'cs1237_calibration_Val oid=%c BlockPreVal=%i TargetVal=%i RealVal=%i'
        )
        if phase_cmd is None or data_cmd is None or not has_response:
            return

        self._cmd_calibration_phase = phase_cmd
        self._cmd_calibration_data = data_cmd
        self.mcu.register_serial_response(
            self._handle_cs1237_calibration_val,
            'cs1237_calibration_Val oid=%c BlockPreVal=%i TargetVal=%i RealVal=%i',
            self._oid,
        )
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command(
            'CS_ADC_CAL_PHASE', self.cmd_cs1237_calibration_phase, False,
            'Trigger cs1237_calibration_phase MCU command')
        gcode.register_command(
            'CS_ADC_CAL_DATA', self.cmd_cs1237_calibration_data, False,
            'Trigger cs1237_calibration_DataProcess MCU command')
        self.cs1237_calibration_phase = self._stock_cs1237_calibration_phase
        self.cs1237_calibration_data_process = (
            self._stock_cs1237_calibration_data_process)
        self.capabilities.add('stock_calibration')
        self.capabilities.add('stock_compat')

def load_config(config):
    return CS1237(config)
