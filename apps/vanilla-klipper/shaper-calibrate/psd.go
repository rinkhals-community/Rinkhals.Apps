// Frequency response of accelerometer data (Welch's method), ported from
// ShaperCalibrate._psd and calc_freq_response in Klipper's shaper_calibrate.py.
//
//	Copyright (C) 2020-2024  Dmitry Butyugin <dmbutyugin@google.com>
//
// This file may be distributed under the terms of the GNU GPLv3 license.
package main

import (
	"encoding/binary"
	"fmt"
	"math"
	"math/bits"
	"os"
)

// Samples holds the accelerometer readings, one slice per column.
type Samples struct {
	Time, X, Y, Z []float64
}

// FreqResponse is Klipper's CalibrationData without the name.
type FreqResponse struct {
	FreqBins []float64 `json:"freq_bins"`
	PsdSum   []float64 `json:"psd_sum"`
	PsdX     []float64 `json:"psd_x"`
	PsdY     []float64 `json:"psd_y"`
	PsdZ     []float64 `json:"psd_z"`
}

// readSamples reads little-endian float64 (time, x, y, z) records.
func readSamples(path string) (*Samples, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(raw)%32 != 0 {
		return nil, fmt.Errorf("%s: size %d is not a whole number of samples", path, len(raw))
	}
	n := len(raw) / 32
	s := &Samples{
		Time: make([]float64, n), X: make([]float64, n),
		Y: make([]float64, n), Z: make([]float64, n),
	}
	for i := 0; i < n; i++ {
		rec := raw[i*32:]
		s.Time[i] = math.Float64frombits(binary.LittleEndian.Uint64(rec[0:]))
		s.X[i] = math.Float64frombits(binary.LittleEndian.Uint64(rec[8:]))
		s.Y[i] = math.Float64frombits(binary.LittleEndian.Uint64(rec[16:]))
		s.Z[i] = math.Float64frombits(binary.LittleEndian.Uint64(rec[24:]))
	}
	return s, nil
}

// besselI0 is the modified Bessel function of the first kind, order zero.
func besselI0(x float64) float64 {
	q := x * x / 4
	sum, term := 1.0, 1.0
	for k := 1; k < 500; k++ {
		term *= q / float64(k*k)
		sum += term
		if term < sum*1e-17 {
			break
		}
	}
	return sum
}

// kaiser is numpy.kaiser(n, beta).
func kaiser(n int, beta float64) []float64 {
	w := make([]float64, n)
	if n == 1 {
		w[0] = 1
		return w
	}
	alpha := float64(n-1) / 2
	denom := besselI0(beta)
	for i := range w {
		r := (float64(i) - alpha) / alpha
		w[i] = besselI0(beta*math.Sqrt(math.Max(0, 1-r*r))) / denom
	}
	return w
}

// fft is an in-place radix-2 (Cooley-Tukey) FFT of n complex numbers, n a
// power of two. The sines, cosines and the bit-reversed index order are
// worked out once in newFFT and reused for every window.
type fft struct {
	n        int
	cos, sin []float64
	rev      []int
}

func newFFT(n int) *fft {
	f := &fft{n: n, cos: make([]float64, n/2), sin: make([]float64, n/2), rev: make([]int, n)}
	for i := 0; i < n/2; i++ {
		a := -2 * math.Pi * float64(i) / float64(n)
		f.cos[i], f.sin[i] = math.Cos(a), math.Sin(a)
	}
	shift := bits.UintSize - uint(bits.Len(uint(n))-1)
	for i := range f.rev {
		if n > 1 {
			f.rev[i] = int(bits.Reverse(uint(i)) >> shift)
		}
	}
	return f
}

// transform replaces the complex numbers re[i] + im[i]*i by their discrete
// Fourier transform.
func (f *fft) transform(re, im []float64) {
	// Reorder the input so that the butterflies below can work in place
	for i, j := range f.rev {
		if i < j {
			re[i], re[j] = re[j], re[i]
			im[i], im[j] = im[j], im[i]
		}
	}
	// Combine transforms of 1 element into transforms of 2, then 4, ...
	for size := 2; size <= f.n; size <<= 1 {
		half, step := size/2, f.n/size
		for start := 0; start < f.n; start += size {
			for k := 0; k < half; k++ {
				wr, wi := f.cos[k*step], f.sin[k*step]
				a, b := start+k, start+k+half
				tr := re[b]*wr - im[b]*wi
				ti := re[b]*wi + im[b]*wr
				re[b], im[b] = re[a]-tr, im[a]-ti
				re[a], im[a] = re[a]+tr, im[a]+ti
			}
		}
	}
}

// psd is the power spectral density of x: how the signal's power is spread
// over frequencies. It uses Welch's algorithm: cut x into half-overlapping
// windows of nfft samples, take the spectrum of each, and average them.
// The result has nfft/2+1 values, for 0 Hz up to fs/2.
//
// Port of ShaperCalibrate._psd.
func psd(x []float64, fs float64, nfft int, window []float64, f *fft) []float64 {
	scale := 0.0
	for _, w := range window {
		scale += w * w
	}
	scale = 1.0 / scale

	overlap := nfft / 2
	step := nfft - overlap
	nWindows := (len(x) - overlap) / step

	out := make([]float64, nfft/2+1)
	re := make([]float64, nfft)
	im := make([]float64, nfft)
	for w := 0; w < nWindows; w++ {
		seg := x[w*step : w*step+nfft]
		mean := 0.0
		for _, v := range seg {
			mean += v
		}
		mean /= float64(nfft)
		// First detrend (remove the mean, which is mostly gravity), then
		// apply the windowing function
		for i, v := range seg {
			re[i] = window[i] * (v - mean)
			im[i] = 0
		}
		f.transform(re, im)
		for k := range out {
			p := (re[k]*re[k] + im[k]*im[k]) * (scale / fs)
			// One-sided output: double everything except the 0 Hz term
			// and the unpaired Nyquist term
			if k > 0 && k < nfft/2 {
				p *= 2
			}
			out[k] += p
		}
	}
	for k := range out {
		out[k] /= float64(nWindows)
	}
	return out
}

// calcFreqResponse is the frequency response of a set of accelerometer
// samples, for each axis and summed.
//
// It returns nil, nil when there are too few samples for one window, which
// Klipper reports as an error of its own.
//
// Port of ShaperCalibrate.calc_freq_response.
func calcFreqResponse(s *Samples, windowSec float64) (*FreqResponse, error) {
	n := len(s.Time)
	if n == 0 {
		return nil, nil
	}
	t := s.Time[n-1] - s.Time[0]
	if !(t > 0) {
		return nil, fmt.Errorf("accelerometer samples span no time (%d samples)", n)
	}
	// The accelerometer's real sample rate, from the timestamps
	fs := float64(n) / t
	// The window is windowSec long. Round up to the nearest power of 2 for faster FFT
	m := int(fs*windowSec - 1)
	if m < 0 {
		m = -m
	}
	nfft := 1 << bits.Len(uint(m))
	if n <= nfft {
		return nil, nil
	}

	window := kaiser(nfft, 6)
	f := newFFT(nfft)
	res := &FreqResponse{
		PsdX: psd(s.X, fs, nfft, window, f),
		PsdY: psd(s.Y, fs, nfft, window, f),
		PsdZ: psd(s.Z, fs, nfft, window, f),
	}
	// numpy.fft.rfftfreq(nfft, 1/fs)
	val := 1.0 / (float64(nfft) * (1.0 / fs))
	res.FreqBins = make([]float64, nfft/2+1)
	res.PsdSum = make([]float64, nfft/2+1)
	for k := range res.FreqBins {
		res.FreqBins[k] = float64(k) * val
		res.PsdSum[k] = res.PsdX[k] + res.PsdY[k] + res.PsdZ[k]
	}
	for _, p := range res.PsdSum {
		if math.IsNaN(p) || math.IsInf(p, 0) {
			return nil, fmt.Errorf("accelerometer data has values that are not numbers")
		}
	}
	return res, nil
}
