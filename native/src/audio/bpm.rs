//! Energy-onset + IOI-median tempo estimator - a port of `BpmAnalyzer` in termstats/audio.py
//! (which mirrors inspector-rust's bpm.ts and disco-controller). Everything here is in
//! seconds already, except the SuperFlux lag window, which counts blocks as in Python.

use std::collections::VecDeque;

pub const THRESHOLD: f64 = 1.4;
pub const AVG_WINDOW_S: f64 = 3.0;
pub const ONSET_REFRACTORY_S: f64 = 0.30;
pub const IOI_WINDOW_S: f64 = 6.0;
pub const BPM_MIN: f64 = 60.0;
pub const BPM_MAX: f64 = 200.0;
pub const MIN_ONSETS: usize = 4;
pub const STALE_RESET_S: f64 = 4.0;
pub const OCTAVE_SNAP: f64 = 8.0;
pub const DISPLAY_AVG_S: f64 = 4.0;
pub const SF_LAG: usize = 4;
pub const SF_WIN: usize = 4;
pub const SF_MARGIN: f64 = 1.04;

pub struct BpmAnalyzer {
    onsets: VecDeque<f64>,
    energy_hist: VecDeque<(f64, f64)>,
    ef: VecDeque<f64>,
    raw_bpm_hist: VecDeque<(f64, f64)>,
    first_t: Option<f64>,
    pub last_onset: f64,
    pub display_bpm: f64,
    pub confidence: f64,
    pub onset_strength: f64,
}

impl Default for BpmAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

impl BpmAnalyzer {
    pub fn new() -> Self {
        BpmAnalyzer { onsets: VecDeque::new(), energy_hist: VecDeque::new(),
                      ef: VecDeque::with_capacity(SF_LAG + SF_WIN), raw_bpm_hist: VecDeque::new(), first_t: None,
                      last_onset: -1e9, display_bpm: 0.0, confidence: 0.0, onset_strength: 0.0 }
    }

    /// One block's kick-band energy. True when an onset (a beat) fired.
    pub fn push(&mut self, energy: f64, now: f64, allow: bool) -> bool {
        let first = *self.first_t.get_or_insert(now);
        self.energy_hist.push_back((now, energy));
        while self.energy_hist.front().is_some_and(|&(t, _)| now - t > AVG_WINDOW_S) {
            self.energy_hist.pop_front();
        }
        let reference = if self.ef.len() >= SF_LAG + SF_WIN {
            self.ef.iter().take(SF_WIN).cloned().fold(f64::MIN, f64::max)
        } else {
            0.0
        };
        if self.ef.len() == SF_LAG + SF_WIN {
            self.ef.pop_front();
        }
        self.ef.push_back(energy);
        if now - first < AVG_WINDOW_S {
            return false; // baseline calibration
        }
        let avg = self.energy_hist.iter().map(|e| e.1).sum::<f64>() / self.energy_hist.len() as f64;
        if avg <= 1e-9 {
            return false;
        }
        let fired = allow
            && energy > avg * THRESHOLD
            && energy > reference * SF_MARGIN // a NEW attack, not sustain
            && now - self.last_onset >= ONSET_REFRACTORY_S;
        if fired {
            self.last_onset = now;
            self.onsets.push_back(now);
            self.onset_strength += (energy / avg - self.onset_strength) * 0.25;
            self.trim_onsets(now);
        }
        fired
    }

    fn trim_onsets(&mut self, now: f64) {
        while self.onsets.front().is_some_and(|&t| now - t > IOI_WINDOW_S) {
            self.onsets.pop_front();
        }
    }

    pub fn estimate(&mut self, now: f64) {
        self.trim_onsets(now);
        if self.onsets.len() < MIN_ONSETS {
            // Stale = no BEAT for a while once the window has thinned out.
            if self.display_bpm > 0.0 && now - self.last_onset > STALE_RESET_S {
                self.display_bpm = 0.0;
                self.confidence = 0.0;
                self.raw_bpm_hist.clear();
            }
            return;
        }
        let intervals: Vec<f64> = self.onsets.iter().zip(self.onsets.iter().skip(1)).map(|(a, b)| b - a).collect();
        let mut sorted = intervals.clone();
        sorted.sort_by(f64::total_cmp);
        let median = sorted[sorted.len() / 2];
        if median <= 0.0 {
            return;
        }
        let mut raw = 60.0 / median;
        while raw < BPM_MIN {
            raw *= 2.0;
        }
        while raw > BPM_MAX {
            raw /= 2.0;
        }
        if self.display_bpm > 0.0 {
            // octave snap onto the locked tempo
            if (raw * 2.0 - self.display_bpm).abs() < OCTAVE_SNAP {
                raw *= 2.0;
            } else if (raw / 2.0 - self.display_bpm).abs() < OCTAVE_SNAP {
                raw /= 2.0;
            }
        }
        if self.raw_bpm_hist.back().is_none_or(|&(_, b)| (b - raw).abs() > 0.01) {
            self.raw_bpm_hist.push_back((now, raw));
        }
        while self.raw_bpm_hist.front().is_some_and(|&(t, _)| now - t > DISPLAY_AVG_S) {
            self.raw_bpm_hist.pop_front();
        }
        if !self.raw_bpm_hist.is_empty() {
            self.display_bpm = self.raw_bpm_hist.iter().map(|b| b.1).sum::<f64>() / self.raw_bpm_hist.len() as f64;
        }
        let var = intervals.iter().map(|i| (i - median).powi(2)).sum::<f64>() / intervals.len() as f64;
        self.confidence = (1.0 - var.sqrt() / median).clamp(0.0, 1.0);
    }

    /// `int(round(display_bpm))` - Python rounds half to even, so does this.
    pub fn bpm(&self) -> u32 {
        self.display_bpm.round_ties_even() as u32
    }

    pub fn onset_rate(&self) -> f64 {
        if self.onsets.len() < 2 {
            return 0.0;
        }
        let span = self.onsets.back().unwrap() - self.onsets.front().unwrap();
        if span > 0.5 { (self.onsets.len() - 1) as f64 / span } else { 0.0 }
    }
}
