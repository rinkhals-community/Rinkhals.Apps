# Input shaper calibration without numpy
#
# Klipper's shaper_calibrate.py needs numpy, which isn't available on the
# printer. This does the same job by handing the two heavy steps (the frequency
# response of the accelerometer data, and the shaper fit) to the compiled
# shaper-calibrate program that ships with the app. shaper_calibrate.py uses
# these classes in place of its own when numpy can't be imported.
#
# Derived from shaper_calibrate.py:
# Copyright (C) 2020-2024  Dmitry Butyugin <dmbutyugin@google.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import array, importlib, itertools, json, math, os, shutil, subprocess, tempfile
from . import shaper_calibrate as upstream
shaper_defs = importlib.import_module('.shaper_defs', 'extras')

# <app>/shaper-calibrate/shaper-calibrate, next to <app>/klippy
BINARY = os.environ.get('SHAPER_CALIBRATE_BIN') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
        'shaper-calibrate', 'shaper-calibrate')

# Accelerometer samples are written out in chunks, so that a long test doesn't
# hold up Klipper's reactor in one go
WRITE_CHUNK = 20000

class Series(list):
    # A list with the one numpy method resonance_tester.py uses on results
    def mean(self):
        return sum(self) / len(self)

def arange(start, stop, step):
    # numpy.arange() for float arguments
    count = max(0, int(math.ceil((stop - start) / step)))
    delta = (start + step) - start
    return [start + i * delta for i in range(count)]

def interp(x, xp, fp):
    # numpy.interp() for increasing x and xp
    res = []
    j = 0
    last = len(xp) - 1
    for v in x:
        if v <= xp[0]:
            res.append(fp[0])
        elif v >= xp[last]:
            res.append(fp[last])
        else:
            while j < last - 1 and xp[j+1] <= v:
                j += 1
            slope = (fp[j+1] - fp[j]) / (xp[j+1] - xp[j])
            res.append(slope * (v - xp[j]) + fp[j])
    return res

class CalibrationData(upstream.CalibrationData):
    def normalize_to_frequencies(self):
        if not self._normalized:
            min_freq = upstream.MIN_FREQ
            for psd in self._psd_list:
                if psd is None:
                    continue
                for i, freq in enumerate(self.freq_bins):
                    # Avoid division by zero errors
                    val = psd[i] / (freq + .1)
                    # Remove low-frequency noise
                    if freq < 2. * min_freq:
                        val *= math.exp(-(2. * min_freq / (freq + .1))**2 + 1.)
                    psd[i] = val
            self._normalized = True
        for other in self.data_sets:
            other.normalize_to_frequencies()

class ShaperCalibrate(upstream.ShaperCalibrate):
    def __init__(self, printer):
        self.printer = printer
        self.error = printer.command_error if printer else Exception
        self.numpy = None
        if not os.path.isfile(BINARY):
            raise self.error(
                    "Neither the `numpy` module nor the shaper-calibrate"
                    " program (%s) is available" % (BINARY,))
        if not os.access(BINARY, os.X_OK):
            try:
                os.chmod(BINARY, 0o755)
            except OSError:
                pass

    def _pause(self):
        if self.printer is not None:
            reactor = self.printer.get_reactor()
            reactor.pause(reactor.NOW)

    def _run(self, workdir, args):
        cmd = [BINARY] + args
        nice = shutil.which('nice')
        if nice:
            # Klippy runs at a high priority, the calculation shouldn't
            cmd = [nice, '-n', '20'] + cmd
        errors = os.path.join(workdir, 'errors.txt')
        with open(errors, 'w') as stderr:
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL,
                                        stderr=stderr)
            except OSError as e:
                raise self.error("Unable to run %s: %s" % (BINARY, str(e)))
            if self.printer is None:
                proc.wait()
            else:
                reactor = self.printer.get_reactor()
                gcode = self.printer.lookup_object("gcode")
                eventtime = last_report_time = reactor.monotonic()
                while proc.poll() is None:
                    if eventtime > last_report_time + 5.:
                        last_report_time = eventtime
                        gcode.respond_info("Wait for calculations..",
                                           log=False)
                    eventtime = reactor.pause(eventtime + .1)
        if proc.returncode:
            with open(errors) as f:
                msg = f.read().strip()
            raise self.error("Error in remote calculation: %s" % (
                msg or "shaper-calibrate exited with status %d"
                % (proc.returncode,),))

    def _call(self, args, write_input):
        # Runs shaper-calibrate with an input file written by write_input()
        # and returns its decoded output
        workdir = tempfile.mkdtemp(prefix='shaper-calibrate-')
        try:
            infile = os.path.join(workdir, 'in')
            outfile = os.path.join(workdir, 'out.json')
            write_input(infile)
            self._run(workdir, args + [infile, outfile])
            with open(outfile) as f:
                return json.load(f)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def calc_freq_response(self, name, raw_values):
        if raw_values is None:
            return None
        if isinstance(raw_values, (list, tuple)):
            samples = raw_values
        else:
            samples = raw_values.get_samples()
        if not samples:
            return None
        def write_samples(filename):
            # 64-bit floats in native (little-endian) byte order:
            # time, accel_x, accel_y, accel_z
            with open(filename, 'wb') as f:
                for pos in range(0, len(samples), WRITE_CHUNK):
                    chunk = array.array('d', itertools.chain.from_iterable(
                        s[:4] for s in samples[pos:pos+WRITE_CHUNK]))
                    chunk.tofile(f)
                    self._pause()
        res = self._call(['psd', repr(upstream.WINDOW_T_SEC)], write_samples)
        if res is None:
            return None
        return CalibrationData(
                name, Series(res['freq_bins']), Series(res['psd_sum']),
                Series(res['psd_x']), Series(res['psd_y']),
                Series(res['psd_z']))

    def process_accelerometer_data(self, name, data):
        calibration_data = self.calc_freq_response(name, data)
        if calibration_data is None:
            raise self.error(
                    "Internal error processing accelerometer data %s" % (data,))
        return calibration_data

    def _get_test_freqs(self, shaper_name, shaper_freqs):
        if not shaper_freqs:
            shaper_freqs = (None, None, None)
        if isinstance(shaper_freqs, tuple):
            freq_end = shaper_freqs[1] or upstream.MAX_SHAPER_FREQ
            freq_start = min(shaper_freqs[0] or
                             shaper_defs.get_shaper_cfg(shaper_name).min_freq,
                             freq_end - 1e-7)
            freq_step = shaper_freqs[2] or .2
            return arange(freq_start, freq_end, freq_step)
        return [float(f) for f in shaper_freqs]

    def find_best_shaper(self, calibration_data, shapers=None,
                         damping_ratio=None, scv=None, shaper_freqs=None,
                         max_smoothing=None, max_vibrations=None,
                         test_damping_ratios=None, max_freq=None, logger=None):
        shapers = shapers or upstream.AUTOTUNE_SHAPERS
        damping_ratio = damping_ratio or shaper_defs.DEFAULT_DAMPING_RATIO
        request = {
            'datasets': [{'freq_bins': data.freq_bins, 'psd': data.psd_sum}
                         for data in calibration_data.get_datasets()],
            'shapers': [],
            'scv': scv,
            'max_smoothing': max_smoothing,
            'max_vibrations': max_vibrations,
            'test_damping_ratios': (test_damping_ratios
                                    or upstream.TEST_DAMPING_RATIOS),
            'max_freq': max_freq or upstream.MAX_FREQ,
            'vibration_reduction': shaper_defs.SHAPER_VIBRATION_REDUCTION,
        }
        for shaper_name in shapers:
            shaper_cfg = shaper_defs.get_shaper_cfg(shaper_name)
            if shaper_cfg is None:
                if logger is not None:
                    logger("Unknown shaper %s" % shaper_name)
                continue
            try:
                shaper_defs.init_shaper(shaper_name, shaper_cfg.min_freq,
                                        shaper_defs.DEFAULT_DAMPING_RATIO)
            except Exception as e:
                if logger is not None:
                    logger("Cannot initialize shaper %s: %s" % (shaper_name,
                                                                str(e)))
                    continue
            # The shapers themselves stay defined in shaper_defs.py:
            # shaper-calibrate gets the pulses of each one to test
            candidates = []
            for test_freq in self._get_test_freqs(shaper_name, shaper_freqs):
                A, T = shaper_defs.init_shaper(shaper_name, test_freq,
                                               damping_ratio)
                candidates.append({'freq': test_freq, 'a': list(A),
                                   't': list(T)})
                if not len(candidates) % 50:
                    self._pause()
            request['shapers'].append({'name': shaper_name,
                                       'candidates': candidates})
        if not request['shapers']:
            raise self.error("No input shapers to test")
        def write_request(filename):
            with open(filename, 'w') as f:
                json.dump(request, f)
        res = self._call(['fit'], write_request)
        all_shapers = [
                upstream.CalibrationResult(
                    name=s['name'], freq=s['freq'],
                    freq_bins=Series(s['freq_bins']), vals=Series(s['vals']),
                    vibrs=s['vibrs'], smoothing=s['smoothing'],
                    score=s['score'], max_accel=s['max_accel'])
                for s in res['shapers']]
        if logger is not None:
            for shaper in all_shapers:
                logger("Fitted shaper '%s' frequency = %.1f Hz "
                       "(vibrations = %.1f%%, smoothing ~= %.3f)" % (
                           shaper.name, shaper.freq, shaper.vibrs * 100.,
                           shaper.smoothing))
                logger("To avoid too much smoothing with '%s', suggested "
                       "max_accel <= %.0f mm/sec^2" % (
                           shaper.name, round(shaper.max_accel / 100.) * 100.))
        return all_shapers[res['best']], all_shapers

    def save_calibration_data(self, output, calibration_data, shapers=None,
                              max_freq=None):
        try:
            datasets = calibration_data.get_datasets()
            max_freq = max_freq or upstream.MAX_FREQ
            if len(datasets) > 1:
                if shapers:
                    freq_bins = shapers[0].freq_bins
                else:
                    min_freq = max_freq
                    for data in datasets:
                        min_freq = min(min_freq, min(data.freq_bins))
                    freq_bins = arange(min_freq, max_freq, 0.2)
                psd_data_to_write = []
                for data in datasets:
                    psd_data_to_write.append(interp(
                        freq_bins, data.freq_bins, data.psd_sum))
            else:
                freq_bins = calibration_data.freq_bins
                psd_data_to_write = [
                        calibration_data.psd_x, calibration_data.psd_y,
                        calibration_data.psd_z, calibration_data.psd_sum]
            with open(output, "w") as csvfile:
                csvfile.write("freq,")
                if len(datasets) > 1:
                    csvfile.write(','.join([self._escape_for_csv(d.name)
                                            for d in datasets]))
                else:
                    csvfile.write("psd_x,psd_y,psd_z,psd_xyz")
                if shapers:
                    csvfile.write(',shapers:')
                    for shaper in shapers:
                        csvfile.write(",%s(%.1f)" % (shaper.name, shaper.freq))
                csvfile.write("\n")
                for i in range(len(freq_bins)):
                    if freq_bins[i] >= max_freq:
                        break
                    csvfile.write("%.1f" % freq_bins[i])
                    for psd in psd_data_to_write:
                        csvfile.write(",%.3e" % psd[i])
                    if shapers:
                        csvfile.write(',')
                        for shaper in shapers:
                            csvfile.write(",%.3f" % (shaper.vals[i],))
                    csvfile.write("\n")
        except IOError as e:
            raise self.error("Error writing to file '%s': %s" % (
                output, str(e)))
