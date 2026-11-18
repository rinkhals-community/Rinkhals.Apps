// Input shaper fitting, ported from ShaperCalibrate.fit_shaper,
// find_best_shaper and their helpers in Klipper's shaper_calibrate.py.
//
//	Copyright (C) 2020-2024  Dmitry Butyugin <dmbutyugin@google.com>
//
// This file may be distributed under the terms of the GNU GPLv3 license.
//
// The shaper definitions themselves are not ported: Klipper's shaper_defs.py
// computes the pulses (A, T) of every shaper at every frequency to test, and
// sends them in the request, so a new or changed shaper needs no change here.
package main

import (
	"fmt"
	"math"
)

// Dataset is one normalized frequency response (CalibrationData.psd_sum).
type Dataset struct {
	FreqBins []float64 `json:"freq_bins"`
	Psd      []float64 `json:"psd"`
}

// Candidate is one shaper at one frequency.
type Candidate struct {
	Freq float64   `json:"freq"`
	A    []float64 `json:"a"`
	T    []float64 `json:"t"`
}

// ShaperRequest lists the candidates of one shaper type, lowest frequency
// first (the order of Klipper's test_freqs).
type ShaperRequest struct {
	Name       string      `json:"name"`
	Candidates []Candidate `json:"candidates"`
}

type FitRequest struct {
	Datasets          []Dataset       `json:"datasets"`
	Shapers           []ShaperRequest `json:"shapers"`
	Scv               float64         `json:"scv"`
	MaxSmoothing      *float64        `json:"max_smoothing"`
	MaxVibrations     *float64        `json:"max_vibrations"`
	TestDampingRatios []float64       `json:"test_damping_ratios"`
	MaxFreq           float64         `json:"max_freq"`
	// SHAPER_VIBRATION_REDUCTION from shaper_defs.py
	VibrationReduction float64 `json:"vibration_reduction"`
}

// ShaperResult is Klipper's CalibrationResult.
type ShaperResult struct {
	Name      string    `json:"name"`
	Freq      float64   `json:"freq"`
	FreqBins  []float64 `json:"freq_bins"`
	Vals      []float64 `json:"vals"`
	Vibrs     float64   `json:"vibrs"`
	Smoothing float64   `json:"smoothing"`
	Score     float64   `json:"score"`
	MaxAccel  float64   `json:"max_accel"`

	shaper    *ShaperRequest
	candidate *Candidate
	maxFreq   float64
}

type FitResponse struct {
	// Best is an index into Shapers
	Best    int             `json:"best"`
	Shapers []*ShaperResult `json:"shapers"`
}

// arange is numpy.arange(start, stop, step) for float arguments.
func arange(start, stop, step float64) []float64 {
	n := int(math.Ceil((stop - start) / step))
	if n <= 0 {
		return nil
	}
	delta := (start + step) - start
	out := make([]float64, n)
	for i := range out {
		out[i] = start + float64(i)*delta
	}
	return out
}

// interp is numpy.interp(x, xp, fp) for increasing xp.
func interp(x, xp, fp []float64) []float64 {
	out := make([]float64, len(x))
	j := 0
	last := len(xp) - 1
	for i, v := range x {
		switch {
		case v <= xp[0]:
			out[i] = fp[0]
		case v >= xp[last]:
			out[i] = fp[last]
		default:
			for j < last-1 && xp[j+1] <= v {
				j++
			}
			for j > 0 && xp[j] > v {
				j--
			}
			slope := (fp[j+1] - fp[j]) / (xp[j+1] - xp[j])
			out[i] = slope*(v-xp[j]) + fp[j]
		}
	}
	return out
}

// estimateShaper is how much of a vibration at each frequency is left by
// the shaper, for a printer with the given damping ratio.
func estimateShaper(c *Candidate, dampingRatio float64, freqBins, out []float64) {
	invD := 0.0
	for _, a := range c.A {
		invD += a
	}
	invD = 1.0 / invD
	tLast := c.T[len(c.T)-1]
	for i, f := range freqBins {
		omega := 2.0 * math.Pi * f
		damping := dampingRatio * omega
		omegaD := omega * math.Sqrt(1.0-dampingRatio*dampingRatio)
		s, co := 0.0, 0.0
		for k, a := range c.A {
			w := a * math.Exp(-damping*(tLast-c.T[k]))
			sin, cos := math.Sincos(omegaD * c.T[k])
			s += w * sin
			co += w * cos
		}
		out[i] = math.Sqrt(s*s+co*co) * invD
	}
}

func remainingVibrations(vals, psd []float64, reduction float64) float64 {
	peak := psd[0]
	for _, p := range psd {
		peak = math.Max(peak, p)
	}
	// The input shaper can only reduce the amplitude of vibrations by
	// SHAPER_VIBRATION_REDUCTION times, so all vibrations below that
	// threshold can be ignored
	threshold := peak / reduction
	remaining, all := 0.0, 0.0
	for i, p := range psd {
		remaining += math.Max(vals[i]*p-threshold, 0)
		all += math.Max(p-threshold, 0)
	}
	return remaining / all
}

func shaperSmoothing(c *Candidate, accel, scv float64) float64 {
	halfAccel := accel * .5
	invD, ts := 0.0, 0.0
	for i, a := range c.A {
		invD += a
		ts += a * c.T[i]
	}
	invD = 1.0 / invD
	// Input shaper shift
	ts *= invD
	// Offset for 90 and 180 degrees turn
	offset90, offset180 := 0.0, 0.0
	for i, a := range c.A {
		d := c.T[i] - ts
		if c.T[i] >= ts {
			offset90 += a * (scv + halfAccel*d) * d
		}
		offset180 += a * halfAccel * d * d
	}
	offset90 *= invD * math.Sqrt2
	offset180 *= invD
	return math.Max(offset90, offset180)
}

func bisect(f func(float64) bool) float64 {
	left, right := 1.0, 1.0
	if !f(1e-9) {
		return 0
	}
	for !f(left) {
		right = left
		left *= .5
	}
	if right == left {
		for f(right) {
			right *= 2
		}
	}
	for right-left > 1e-8 {
		middle := (left + right) * .5
		if f(middle) {
			left = middle
		} else {
			right = middle
		}
	}
	return left
}

func shaperMaxAccel(c *Candidate, scv float64) float64 {
	// Just some empirically chosen value which produces good projections
	// for max_accel without much smoothing
	const targetSmoothing = 0.12
	return bisect(func(accel float64) bool {
		return shaperSmoothing(c, accel, scv) <= targetSmoothing
	})
}

// trimmed is a dataset cut to the frequencies at or under max_freq.
type trimmed struct {
	freqBins, psd []float64
}

func trim(datasets []Dataset, maxFreq float64) ([]trimmed, error) {
	out := make([]trimmed, len(datasets))
	for i, d := range datasets {
		if len(d.FreqBins) != len(d.Psd) {
			return nil, fmt.Errorf("dataset %d: %d frequencies but %d values", i, len(d.FreqBins), len(d.Psd))
		}
		for k, f := range d.FreqBins {
			if f <= maxFreq {
				out[i].freqBins = append(out[i].freqBins, f)
				out[i].psd = append(out[i].psd, d.Psd[k])
			}
		}
		if len(out[i].psd) == 0 {
			return nil, fmt.Errorf("dataset %d has no data at or under %.1f Hz", i, maxFreq)
		}
	}
	return out, nil
}

// shaperVals is the worst case over the test damping ratios of what the
// shaper leaves at each frequency, and the worst remaining vibrations.
func shaperVals(c *Candidate, data trimmed, req *FitRequest, scratch []float64) ([]float64, float64) {
	vals := make([]float64, len(data.freqBins))
	vibrations := 0.0
	for _, dr := range req.TestDampingRatios {
		estimateShaper(c, dr, data.freqBins, scratch)
		v := remainingVibrations(scratch, data.psd, req.VibrationReduction)
		for i := range vals {
			vals[i] = math.Max(vals[i], scratch[i])
		}
		if v > vibrations {
			vibrations = v
		}
	}
	return vals, vibrations
}

// fill computes the response curve of a result. Klipper computes it for
// every candidate; only the ones that get reported need it.
func (r *ShaperResult) fill(req *FitRequest) error {
	if r.Vals != nil {
		return nil
	}
	data, err := trim(req.Datasets, r.maxFreq)
	if err != nil {
		return err
	}
	minFreq := r.maxFreq
	for _, d := range req.Datasets {
		for _, f := range d.FreqBins {
			minFreq = math.Min(minFreq, f)
		}
	}
	r.FreqBins = arange(minFreq, r.maxFreq, 0.2)
	r.Vals = make([]float64, len(r.FreqBins))
	for _, d := range data {
		scratch := make([]float64, len(d.freqBins))
		vals, _ := shaperVals(r.candidate, d, req, scratch)
		for i, v := range interp(r.FreqBins, d.freqBins, vals) {
			r.Vals[i] = math.Max(r.Vals[i], v)
		}
	}
	return nil
}

// fitShaper returns the selected result and all results, in the order
// Klipper builds them (highest frequency first).
func fitShaper(s *ShaperRequest, req *FitRequest) (*ShaperResult, []*ShaperResult, error) {
	if len(s.Candidates) == 0 {
		return nil, nil, fmt.Errorf("shaper %s: no frequencies to test", s.Name)
	}
	maxFreq := req.MaxFreq
	for i := range s.Candidates {
		c := &s.Candidates[i]
		if len(c.A) == 0 || len(c.A) != len(c.T) {
			return nil, nil, fmt.Errorf("shaper %s at %.1f Hz: malformed pulses", s.Name, c.Freq)
		}
		maxFreq = math.Max(maxFreq, c.Freq)
	}
	data, err := trim(req.Datasets, maxFreq)
	if err != nil {
		return nil, nil, err
	}
	longest := 0
	for _, d := range data {
		longest = max(longest, len(d.freqBins))
	}
	scratch := make([]float64, longest)

	var best *ShaperResult
	var results []*ShaperResult
	for i := len(s.Candidates) - 1; i >= 0; i-- {
		c := &s.Candidates[i]
		smoothing := shaperSmoothing(c, 5000, req.Scv)
		if req.MaxSmoothing != nil && *req.MaxSmoothing != 0 && smoothing > *req.MaxSmoothing && best != nil {
			return best, results, nil
		}
		vibrations := 0.0
		for _, d := range data {
			_, v := shaperVals(c, d, req, scratch[:len(d.freqBins)])
			if v > vibrations {
				vibrations = v
			}
		}
		// The score trying to minimize vibrations, but also accounting
		// the growth of smoothing. The formula itself does not have any
		// special meaning, it simply shows good results on real user data
		res := &ShaperResult{
			Name: s.Name, Freq: c.Freq, Vibrs: vibrations, Smoothing: smoothing,
			Score:    smoothing * (math.Pow(vibrations, 1.5) + vibrations*.2 + .01),
			MaxAccel: shaperMaxAccel(c, req.Scv),
			shaper:   s, candidate: c, maxFreq: maxFreq,
		}
		results = append(results, res)
		if best == nil || best.Vibrs > res.Vibrs {
			// The current frequency is better for the shaper.
			best = res
		}
	}
	// Try to find an 'optimal' shaper configuration: the one that is not
	// much worse than the 'best' one, but gives much less smoothing
	selected := best
	for i := len(results) - 1; i >= 0; i-- {
		res := results[i]
		if res.Vibrs < best.Vibrs*1.1+.0005 && (res.Score < selected.Score ||
			(req.MaxVibrations != nil && best.Vibrs > *req.MaxVibrations)) {
			selected = res
		}
	}
	return selected, results, nil
}

func findBestShaper(req *FitRequest) (*FitResponse, error) {
	if len(req.Datasets) == 0 || len(req.Shapers) == 0 {
		return nil, fmt.Errorf("nothing to fit: %d datasets, %d shapers", len(req.Datasets), len(req.Shapers))
	}
	if len(req.TestDampingRatios) == 0 || !(req.VibrationReduction > 0) || !(req.MaxFreq > 0) {
		return nil, fmt.Errorf("bad request: damping ratios, vibration reduction and max frequency are required")
	}
	var best *ShaperResult
	var all []*ShaperResult
	for i := range req.Shapers {
		shaper, results, err := fitShaper(&req.Shapers[i], req)
		if err != nil {
			return nil, err
		}
		mv := req.MaxVibrations
		if best == nil || shaper.Score*1.2 < best.Score ||
			(shaper.Score*1.05 < best.Score && shaper.Smoothing*1.1 < best.Smoothing) ||
			(mv != nil && best.Vibrs > *mv && shaper.Vibrs < best.Vibrs) {
			// Either the shaper significantly improves the score (by 20%),
			// or it improves the score and smoothing (by 5% and 10% resp.),
			// or the previous shaper was giving more remaining vibrations
			// and the current one improves them.
			best = shaper
		}
		for k := len(results) - 1; k >= 0; k-- {
			s := results[k]
			if s.Vibrs < best.Vibrs && s.Smoothing < best.Smoothing {
				best, shaper = s, s
			}
		}
		all = append(all, shaper)
	}
	if best.shaper.Name == "zv" {
		for _, tuned := range all {
			if tuned.shaper.Name != "zv" && tuned.Vibrs*1.1 < best.Vibrs {
				best = tuned
				break
			}
		}
	}
	resp := &FitResponse{Best: -1, Shapers: all}
	for i, s := range all {
		if err := s.fill(req); err != nil {
			return nil, err
		}
		if s == best {
			resp.Best = i
		}
	}
	if resp.Best < 0 {
		return nil, fmt.Errorf("internal error: the best shaper is not among the results")
	}
	return resp, nil
}
