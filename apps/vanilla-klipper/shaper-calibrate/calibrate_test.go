package main

import (
	"math"
	"testing"
)

func near(a, b, tol float64) bool {
	return math.Abs(a-b) <= tol*math.Max(1, math.Abs(b))
}

func TestKaiserMatchesNumpy(t *testing.T) {
	// numpy.kaiser(8, 6.)
	want := []float64{0.014873337104763207, 0.19978195130647008, 0.591312182347899,
		0.9454284905890261, 0.9454284905890261, 0.591312182347899,
		0.19978195130647008, 0.014873337104763207}
	for i, w := range kaiser(8, 6) {
		if !near(w, want[i], 1e-13) {
			t.Errorf("kaiser[%d] = %v, want %v", i, w, want[i])
		}
	}
}

func TestFFTMatchesDFT(t *testing.T) {
	const n = 64
	re, im := make([]float64, n), make([]float64, n)
	x := make([]float64, n)
	for i := range x {
		x[i] = math.Sin(float64(i)*0.7) + 0.3*math.Cos(float64(i)*2.1) + float64(i%5)
		re[i] = x[i]
	}
	newFFT(n).transform(re, im)
	for k := 0; k < n; k++ {
		wr, wi := 0.0, 0.0
		for i, v := range x {
			a := -2 * math.Pi * float64(k*i) / n
			wr += v * math.Cos(a)
			wi += v * math.Sin(a)
		}
		if !near(re[k], wr, 1e-10) || !near(im[k], wi, 1e-10) {
			t.Fatalf("bin %d = (%v, %v), want (%v, %v)", k, re[k], im[k], wr, wi)
		}
	}
}

func TestPsdFindsTone(t *testing.T) {
	const fs, tone = 400.0, 50.0
	s := &Samples{}
	for i := 0; i < 4000; i++ {
		ts := float64(i) / fs
		s.Time = append(s.Time, ts)
		s.X = append(s.X, 100*math.Sin(2*math.Pi*tone*ts))
		s.Y = append(s.Y, 0)
		s.Z = append(s.Z, 9800)
	}
	res, err := calcFreqResponse(s, 0.5)
	if err != nil || res == nil {
		t.Fatalf("calcFreqResponse: %v, %v", res, err)
	}
	peak := 0
	for k, p := range res.PsdX {
		if p > res.PsdX[peak] {
			peak = k
		}
	}
	if math.Abs(res.FreqBins[peak]-tone) > 1.6 {
		t.Errorf("peak at %.2f Hz, want %.0f", res.FreqBins[peak], tone)
	}
	// A constant (gravity) is removed by the detrend
	for k, p := range res.PsdZ {
		if p > 1e-12 {
			t.Fatalf("psd_z[%d] = %v for a constant signal", k, p)
		}
	}
}

func TestTooFewSamples(t *testing.T) {
	s := &Samples{Time: []float64{0, 0.0025, 0.005}, X: make([]float64, 3), Y: make([]float64, 3), Z: make([]float64, 3)}
	if res, err := calcFreqResponse(s, 0.5); res != nil || err != nil {
		t.Errorf("got %v, %v, want nil, nil", res, err)
	}
}

func TestArangeAndInterp(t *testing.T) {
	a := arange(21, 150, .2)
	if len(a) != 645 || a[0] != 21 || !near(a[644], 149.8, 1e-12) {
		t.Errorf("arange: %d values, %v .. %v", len(a), a[0], a[len(a)-1])
	}
	got := interp([]float64{-1, 0, 0.5, 1, 2.5, 3, 9}, []float64{0, 1, 3}, []float64{10, 20, 0})
	want := []float64{10, 10, 15, 20, 5, 0, 0}
	for i := range want {
		if !near(got[i], want[i], 1e-12) {
			t.Errorf("interp[%d] = %v, want %v", i, got[i], want[i])
		}
	}
}

func TestZVShaperCancelsItsFrequency(t *testing.T) {
	// A ZV shaper for 50 Hz with no damping: pulses [1, 1] at [0, 1/100 s]
	c := &Candidate{Freq: 50, A: []float64{1, 1}, T: []float64{0, 0.01}}
	out := make([]float64, 2)
	estimateShaper(c, 0, []float64{50, 100}, out)
	if out[0] > 1e-12 || !near(out[1], 1, 1e-12) {
		t.Errorf("remaining at 50 and 100 Hz = %v, want 0 and 1", out)
	}
	if accel := shaperMaxAccel(c, 5); accel < 1000 || accel > 100000 {
		t.Errorf("max accel = %v", accel)
	}
}

func TestBadRequests(t *testing.T) {
	if _, err := findBestShaper(&FitRequest{}); err == nil {
		t.Error("empty request: no error")
	}
	req := &FitRequest{
		Datasets:          []Dataset{{FreqBins: []float64{300, 400}, Psd: []float64{1, 1}}},
		Shapers:           []ShaperRequest{{Name: "zv", Candidates: []Candidate{{Freq: 50, A: []float64{1, 1}, T: []float64{0, 0.01}}}}},
		Scv:               5,
		TestDampingRatios: []float64{0.1}, MaxFreq: 200, VibrationReduction: 20,
	}
	if _, err := findBestShaper(req); err == nil {
		t.Error("no data under max_freq: no error")
	}
}
