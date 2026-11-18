"""Check shaper-calibrate against Klipper's own numpy implementation.

Runs both on the same simulated accelerometer data and compares every number
SHAPER_CALIBRATE, TEST_RESONANCES and MEASURE_AXES_NOISE use, and the CSV files
they write. Needs numpy (this runs on a PC, not on the printer) and a
shaper-calibrate built for this machine:

    go build -o /tmp/shaper-calibrate . && \
        SHAPER_CALIBRATE_BIN=/tmp/shaper-calibrate python test_against_numpy.py
"""
import filecmp, importlib, os, sys, tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "klippy"))
reference = importlib.import_module("extras.shaper_calibrate")
native = importlib.import_module("extras.shaper_calibrate_native")

RTOL = 1e-9
checks = 0


def close(what, got, want):
    global checks
    checks += 1
    got, want = np.asarray(got, dtype=float), np.asarray(want, dtype=float)
    if got.shape != want.shape:
        raise SystemExit("FAIL %s: shape %s, expected %s" % (what, got.shape, want.shape))
    if not np.allclose(got, want, rtol=RTOL, atol=1e-300):
        worst = np.max(np.abs(got - want) / np.maximum(np.abs(want), 1e-300))
        raise SystemExit("FAIL %s: off by up to %.3g (relative)" % (what, worst))


def simulate(rng, rate, seconds, resonances, noise=30.0, jitter=0.0):
    """A frequency sweep through a toolhead with the given (freq, damping,
    gain) resonances, as (time, x, y, z) accelerometer samples."""
    n = int(rate * seconds)
    t = np.arange(n) / rate
    freq = 5.0 + (135.0 / seconds) * t
    phase = 2 * np.pi * np.cumsum(freq) / rate
    out = np.zeros((n, 4))
    out[:, 0] = 100.0 + t + rng.normal(0, jitter / rate, n).cumsum() * 0.01
    for axis, share in ((1, 1.0), (2, 0.25), (3, 0.1)):
        signal = np.zeros(n)
        for f0, zeta, gain in resonances:
            r = freq / f0
            amp = gain / np.sqrt((1 - r**2) ** 2 + (2 * zeta * r) ** 2)
            lag = np.arctan2(2 * zeta * r, 1 - r**2)
            signal += amp * np.sin(phase - lag)
        out[:, axis] = 500.0 * share * signal + rng.normal(0, noise, n)
    out[:, 3] += 9800.0
    return out


class Samples:
    # Stands in for Klipper's AccelQueryHelper
    def __init__(self, data):
        self.data = data

    def get_samples(self):
        return [tuple(row) for row in self.data.tolist()]


def compare_data(what, got, want):
    close(what + " freq_bins", got.freq_bins, want.freq_bins)
    for axis in ("x", "y", "z", "all"):
        close("%s psd %s" % (what, axis), got.get_psd(axis), want.get_psd(axis))


def compare_files(what, write_got, write_want):
    global checks
    checks += 1
    with tempfile.TemporaryDirectory() as tmp:
        got, want = os.path.join(tmp, "got.csv"), os.path.join(tmp, "want.csv")
        write_got(got)
        write_want(want)
        if not filecmp.cmp(got, want, shallow=False):
            raise SystemExit("FAIL %s: the CSV files differ" % what)


def run_case(name, datasets, fit_variants):
    ref, nat = reference.ShaperCalibrate(None), native.ShaperCalibrate(None)
    ref_data = nat_data = None
    for i, raw in enumerate(datasets):
        label = "probe, point %d" % i  # a comma, to exercise CSV escaping
        r = ref.process_accelerometer_data(label, raw)
        r.set_numpy(np)
        n = nat.process_accelerometer_data(label, Samples(raw))
        compare_data("%s: dataset %d" % (name, i), n, r)
        close("%s: noise" % name, [n.psd_x.mean(), n.psd_y.mean(), n.psd_z.mean()],
              [r.psd_x.mean(), r.psd_y.mean(), r.psd_z.mean()])
        if ref_data is None:
            ref_data, nat_data = r, n
            # TEST_RESONANCES writes the data before normalizing it
            compare_files("%s: resonances csv" % name,
                          lambda f: nat.save_calibration_data(f, nat_data, max_freq=186.7),
                          lambda f: ref.save_calibration_data(f, ref_data, max_freq=186.7))
        else:
            ref_data.add_data(r)
            nat_data.add_data(n)
    ref_data.normalize_to_frequencies()
    nat_data.normalize_to_frequencies()
    for i, (r, n) in enumerate(zip(ref_data.get_datasets(), nat_data.get_datasets())):
        compare_data("%s: normalized dataset %d" % (name, i), n, r)
    compare_files("%s: csv without shapers" % name,
                  lambda f: nat.save_calibration_data(f, nat_data),
                  lambda f: ref.save_calibration_data(f, ref_data))

    for variant in fit_variants:
        what = "%s %s" % (name, variant)
        ref_log, nat_log = [], []
        ref_best, ref_all = ref.find_best_shaper(ref_data, logger=ref_log.append, **variant)
        nat_best, nat_all = nat.find_best_shaper(nat_data, logger=nat_log.append, **variant)
        global checks
        checks += 1
        if (nat_best.name, len(nat_all)) != (ref_best.name, len(ref_all)):
            raise SystemExit("FAIL %s: best %s of %d, expected %s of %d" % (
                what, nat_best.name, len(nat_all), ref_best.name, len(ref_all)))
        if sorted(nat_log) != sorted(ref_log):
            raise SystemExit("FAIL %s: console messages differ:\n%s\n%s" % (what, nat_log, ref_log))
        for n, r in zip([nat_best] + nat_all, [ref_best] + ref_all):
            if n.name != r.name:
                raise SystemExit("FAIL %s: shaper %s, expected %s" % (what, n.name, r.name))
            close("%s %s scalars" % (what, r.name),
                  [n.freq, n.vibrs, n.smoothing, n.score, n.max_accel],
                  [r.freq, r.vibrs, r.smoothing, r.score, r.max_accel])
            close("%s %s freq_bins" % (what, r.name), n.freq_bins, r.freq_bins)
            close("%s %s vals" % (what, r.name), n.vals, r.vals)
        compare_files("%s: calibration csv" % what,
                      lambda f: nat.save_calibration_data(f, nat_data, nat_all, variant.get("max_freq")),
                      lambda f: ref.save_calibration_data(f, ref_data, ref_all, variant.get("max_freq")))
        print("ok  %-58s %s @ %.1f Hz" % (what[:58], ref_best.name, ref_best.freq))


def main():
    rng = np.random.default_rng(20261117)
    default = dict(scv=5.0, max_freq=186.7)
    variants = [
        default,
        dict(scv=12.0, max_freq=186.7, max_smoothing=0.08),
        dict(scv=5.0, max_smoothing=0.05, max_vibrations=0.02),
        dict(scv=5.0, max_freq=250.0, shaper_freqs=(30.0, 180.0, 0.5),
             shapers=["zv", "mzv", "zvd", "ei", "nosuchshaper"],
             damping_ratio=0.08, test_damping_ratios=[0.05, 0.1, 0.2]),
        dict(scv=5.0, shaper_freqs=[35.0, 42.5, 60.0, 81.2]),
    ]
    # The LIS2DW12 as the Kobra configs set it up (400 samples a second)
    run_case("400Hz one peak", [simulate(rng, 400.0, 68, [(81.0, 0.06, 1.0)])], variants)
    run_case("400Hz two peaks", [simulate(rng, 400.0, 68, [(39.0, 0.08, 1.0), (95.0, 0.05, 0.4)], jitter=0.3)], variants)
    # Several probe points, and the rates of other accelerometers
    run_case("1600Hz two points", [
        simulate(rng, 1600.0, 68, [(52.0, 0.1, 1.0), (118.0, 0.04, 0.6)]),
        simulate(rng, 1600.0, 68, [(57.0, 0.09, 1.0), (121.0, 0.05, 0.3)], jitter=0.2),
    ], variants)
    run_case("3200Hz low peak", [simulate(rng, 3200.0, 40, [(24.0, 0.15, 1.0)], noise=5.0)], [default])
    run_case("noise only", [simulate(rng, 400.0, 20, [], noise=80.0)], [default])

    # Too little data for one window is an error in both
    global checks
    checks += 1
    for module in (reference, native):
        short = simulate(rng, 400.0, 0.5, [(60.0, 0.1, 1.0)])
        try:
            module.ShaperCalibrate(None).process_accelerometer_data(
                "short", short if module is reference else Samples(short))
        except Exception as e:
            if "Internal error processing accelerometer data" not in str(e):
                raise SystemExit("FAIL short data: unexpected error %s" % e)
        else:
            raise SystemExit("FAIL short data: no error from %s" % module.__name__)
    print("All %d checks passed" % checks)


if __name__ == "__main__":
    main()
