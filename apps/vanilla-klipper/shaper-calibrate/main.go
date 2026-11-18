// shaper-calibrate does the number crunching for Klipper's SHAPER_CALIBRATE,
// TEST_RESONANCES and MEASURE_AXES_NOISE on printers that have no numpy.
//
// It is a port of the numpy parts of klippy/extras/shaper_calibrate.py:
//
//	Copyright (C) 2020-2024  Dmitry Butyugin <dmbutyugin@google.com>
//
// This file may be distributed under the terms of the GNU GPLv3 license.
//
// Klipper's shaper_calibrate_native.py runs it as:
//
//	shaper-calibrate psd WINDOW_SEC SAMPLES.bin OUT.json
//	shaper-calibrate fit REQUEST.json OUT.json
//
// A failure is a non-zero exit status and one line on stderr.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
)

func writeJSON(path string, v any) error {
	data, err := json.Marshal(v)
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o644)
}

func run(args []string) error {
	if len(args) == 4 && args[0] == "psd" {
		window, err := strconv.ParseFloat(args[1], 64)
		if err != nil || window <= 0 {
			return fmt.Errorf("bad window length %q", args[1])
		}
		samples, err := readSamples(args[2])
		if err != nil {
			return err
		}
		res, err := calcFreqResponse(samples, window)
		if err != nil {
			return err
		}
		// res is nil (JSON null) when there are too few samples, as in Klipper
		return writeJSON(args[3], res)
	}
	if len(args) == 3 && args[0] == "fit" {
		data, err := os.ReadFile(args[1])
		if err != nil {
			return err
		}
		var req FitRequest
		if err := json.Unmarshal(data, &req); err != nil {
			return fmt.Errorf("bad request: %v", err)
		}
		res, err := findBestShaper(&req)
		if err != nil {
			return err
		}
		return writeJSON(args[2], res)
	}
	return fmt.Errorf("usage: shaper-calibrate psd WINDOW_SEC SAMPLES.bin OUT.json | fit REQUEST.json OUT.json")
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
