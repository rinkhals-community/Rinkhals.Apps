# shaper-calibrate

Input shaper calibration (`SHAPER_CALIBRATE`, `TEST_RESONANCES`,
`MEASURE_AXES_NOISE`) for Klipper on printers that have no numpy.

Klipper does the calibration math in `klippy/extras/shaper_calibrate.py` with
numpy. This folder replaces only the numpy parts. Everything else (running the
test, the console messages, saving to the config) is still Klipper's own code.

## How the pieces fit

```
resonance_tester.py            Klipper, unchanged. Runs the test, collects
        |                      accelerometer samples.
        v
shaper_calibrate.py            Klipper, plus 5 lines from klippy.patch: if numpy
        |                      can't be imported, use the classes below instead.
        v
shaper_calibrate_native.py     This folder. Same classes and methods as
        |                      Klipper's, but with plain Python lists. Hands the
        |                      two heavy calculations to the program below.
        v
shaper-calibrate               This folder (Go). Does the two calculations and
                               exits. Knows nothing about Klipper.
```

The two calculations:

| Step | Command | Go file | Ports from `shaper_calibrate.py` |
|---|---|---|---|
| Frequency response of the samples | `shaper-calibrate psd` | `psd.go` | `_psd`, `calc_freq_response` |
| Choosing the shaper | `shaper-calibrate fit` | `fit.go` | `fit_shaper`, `find_best_shaper` and helpers |

`main.go` only parses the command line.

Each Go function says which Klipper function it ports, and keeps Klipper's
structure and comments, so the two can be read side by side.

## What is deliberately not ported

**The shaper definitions.** What a `zv` or `mzv` shaper is stays in Klipper's
`shaper_defs.py`. For every shaper and every frequency to test, the Python
module asks `shaper_defs.py` for the shaper's pulses (amplitudes `A`, times `T`)
and passes them to the program. So a new or changed shaper in Klipper needs no
change here.

**The constants** (`MIN_FREQ`, `TEST_DAMPING_RATIOS`, `WINDOW_T_SEC`, ...). The
Python module reads them from Klipper's `shaper_calibrate.py` and passes them
along.

## The interface between the module and the program

Files, not pipes, so that neither side can block the other.

`shaper-calibrate psd WINDOW_SEC SAMPLES.bin OUT.json`

- `SAMPLES.bin`: the samples as 64-bit little-endian floats, four per sample:
  time (s), x, y, z.
- `OUT.json`: `{"freq_bins": [...], "psd_sum": [...], "psd_x": [...], "psd_y":
  [...], "psd_z": [...]}`, or `null` when there are too few samples (Klipper
  reports that itself).

`shaper-calibrate fit REQUEST.json OUT.json`

- `REQUEST.json`: see `FitRequest` in `fit.go`.
- `OUT.json`: see `FitResponse` in `fit.go`: the result for each shaper type and
  which one is best.

On failure the program exits with a non-zero status and one line on stderr,
which the module shows in the Klipper console.

## Building

```
sh build.sh
```

This cross-compiles for the printers (32-bit ARM, static, no libc needed) with a
pinned Go version, so the same source always gives the same file. The built
`shaper-calibrate` is committed. **After changing any `.go` file, run `build.sh`
and commit the new binary**; CI fails if they don't match.

`shaper_calibrate_native.py` lives here and is copied into `klippy/extras/` by
`../get-klipper.sh`. After changing it, copy it there too (CI checks that as
well).

## Testing

```
go test ./...                       # unit tests of the Go code

go build -o /tmp/shaper-calibrate . # the comparison with Klipper + numpy
pip install numpy
SHAPER_CALIBRATE_BIN=/tmp/shaper-calibrate python test_against_numpy.py
```

`test_against_numpy.py` is the important one. It runs Klipper's numpy code and
this code on the same simulated accelerometer data and requires every number to
agree to 1e-9 and the CSV files to be identical. Run it on a PC; it needs numpy.

## When Klipper is updated

- If `klippy.patch` no longer applies to `shaper_calibrate.py`, re-create the
  last hunk: it only appends the `try: import numpy / except ImportError:` block
  at the end of the file.
- If upstream changed the math in `shaper_calibrate.py`,
  `test_against_numpy.py` fails and names the first number that differs. Port
  the change to `psd.go` or `fit.go`.
- If upstream changed what `resonance_tester.py` calls on `ShaperCalibrate` or
  `CalibrationData`, mirror it in `shaper_calibrate_native.py`.

## License

Ported from Klipper's `shaper_calibrate.py`, Copyright (C) 2020-2024 Dmitry
Butyugin, and distributed under the same GNU GPLv3 license.
