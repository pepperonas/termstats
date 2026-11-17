//! The microphone screens' analysis - a port of termstats/audio.py without numpy.
//!
//! Samples come in through `Analyzer::feed()` in blocks of any length at any sample rate;
//! they are re-chunked to `BLOCK` samples, as the Python program reads them. Numbers come
//! out: the level in dBFS, 28 band levels with peaks, the tempo. Nothing here draws.
//!
//! The Python program's per-block constants are tuned for 44.1 kHz. A block of 1024 samples
//! at 48 kHz is shorter, so here every per-block constant - the smoothing rates, the peak
//! hold and fall, the SuperFlux lag window of the tempo - is scaled by the block's real
//! duration against that reference (`Scale`); at 44.1 kHz it is the identity.

pub mod bpm;
pub mod fft;
pub mod spectrum;

use bpm::BpmAnalyzer;
use spectrum::Spectrum;
use std::collections::VecDeque;

/// The rate the Python program's per-block constants were tuned at.
pub const SAMPLE_RATE: f64 = 44100.0;
pub const BLOCK: usize = 1024;
pub const DB_FLOOR: f64 = -80.0;
pub const LOUD_DB: f64 = -45.0;
/// What the level screen SHOWS is dBFS + SPL_OFFSET: an estimate on a phone app's scale.
/// Only the display is shifted - the analysis and the histories stay in dBFS.
pub const SPL_OFFSET: f64 = 100.0;
pub const BANDS: usize = 28;
pub const F_LO: f64 = 40.0;
pub const F_HI: f64 = 16000.0;
pub const BASS_LO: f64 = 30.0;
pub const BASS_HI: f64 = 110.0;
pub const DB_HISTORY_S: f64 = 120.0;
/// Sample rates a caller may feed; anything else is ignored rather than trusted.
const RATE_MIN: f64 = 4000.0;
const RATE_MAX: f64 = 768_000.0;

pub fn spl(db: f64) -> f64 {
    db + SPL_OFFSET
}

/// RMS level of a block in dBFS, clamped to [DB_FLOOR, 0]. A full-scale sine is -3.0.
pub fn dbfs(block: &[f32]) -> f64 {
    if block.is_empty() {
        return DB_FLOOR;
    }
    let mean = block.iter().map(|&x| (x as f64) * (x as f64)).sum::<f64>() / block.len() as f64;
    let rms = mean.sqrt() + 1e-12;
    (20.0 * rms.log10()).clamp(DB_FLOOR, 0.0)
}

/// How a per-block constant tuned for a 1024-sample block at 44.1 kHz becomes one for a
/// 1024-sample block at the real rate: `k` is the block's duration in reference blocks.
#[derive(Clone, Copy, Debug)]
pub struct Scale {
    pub k: f64,
}

impl Scale {
    pub fn for_rate(sample_rate: f64) -> Scale {
        Scale { k: (BLOCK as f64 / sample_rate) / (BLOCK as f64 / SAMPLE_RATE) }
    }

    /// A per-block smoothing rate (`x += (target - x) * rate`).
    pub fn rate(self, rate: f64) -> f64 {
        1.0 - (1.0 - rate).powf(self.k)
    }

    /// A per-block decay factor (`x *= decay`).
    pub fn decay(self, decay: f64) -> f64 {
        decay.powf(self.k)
    }

    /// A count of blocks.
    pub fn blocks(self, blocks: f64) -> i64 {
        (blocks / self.k).round().max(1.0) as i64
    }

    /// A step per block.
    pub fn step(self, step: f64) -> f64 {
        step * self.k
    }
}

/// One object per audio source: feed() samples, read the numbers.
pub struct Analyzer {
    sample_rate: f64,
    scale: Scale,
    pub spectrum: Spectrum,
    pub tempo: BpmAnalyzer,
    /// The FFT and the 28 bands are only computed while this is on (the equalizer is drawn);
    /// without it the kick band is measured on its own, which is all the tempo needs.
    pub spectrum_on: bool,
    pending: Vec<f32>,
    pub db: f64,
    pub db_smooth: f64,
    pub db_min: f64,
    pub db_max: f64,
    pub db_history: VecDeque<(f64, f64)>,
    pub bpm_history: VecDeque<(f64, f64)>,
    history_len: usize,
    pub bass: f64,
    bass_peak: f64,
    pub bands: [f32; BANDS],
    pub peaks: [f32; BANDS],
    pub beats: u64,
    pub last_beat: Option<f64>,
    pub last_onset: Option<f64>,
    pub blocks: u64,
    pub bpm: Option<f32>,
    pub confidence: f64,
    pub music: bool,
}

impl Default for Analyzer {
    fn default() -> Self {
        Self::new()
    }
}

impl Analyzer {
    pub fn new() -> Self {
        Self::with_rate(SAMPLE_RATE)
    }

    pub fn with_rate(sample_rate: f64) -> Self {
        let scale = Scale::for_rate(sample_rate);
        let history_len = (DB_HISTORY_S * sample_rate / BLOCK as f64) as usize + 1;
        Analyzer {
            sample_rate,
            scale,
            spectrum: Spectrum::new(sample_rate, scale),
            tempo: BpmAnalyzer::with_scale(scale),
            spectrum_on: true,
            pending: Vec::with_capacity(2 * BLOCK),
            db: DB_FLOOR,
            db_smooth: DB_FLOOR,
            db_min: 0.0,
            db_max: DB_FLOOR,
            db_history: VecDeque::with_capacity(history_len),
            bpm_history: VecDeque::with_capacity(history_len),
            history_len,
            bass: 0.0,
            bass_peak: 1e-9,
            bands: [0.0; BANDS],
            peaks: [0.0; BANDS],
            beats: 0,
            last_beat: None,
            last_onset: None,
            blocks: 0,
            bpm: None,
            confidence: 0.0,
            music: false,
        }
    }

    pub fn sample_rate(&self) -> f64 {
        self.sample_rate
    }

    pub fn onset_rate(&self) -> f64 {
        self.tempo.onset_rate()
    }

    /// Samples of any count at any rate. Full blocks are analysed at once, each stamped with
    /// the time its last sample arrived (the last full block ends `remainder` samples
    /// before `now`); the remainder waits for the next call. A new rate starts afresh.
    pub fn feed(&mut self, block: &[f32], sample_rate: f32, now: f64) {
        let rate = sample_rate as f64;
        if !(rate.is_finite() && (RATE_MIN..=RATE_MAX).contains(&rate)) || !now.is_finite() {
            return;
        }
        if rate != self.sample_rate {
            let on = self.spectrum_on;
            *self = Analyzer::with_rate(rate);
            self.spectrum_on = on;
        }
        self.pending.extend(block.iter().map(|&x| if x.is_finite() { x } else { 0.0 }));
        let full = self.pending.len() / BLOCK;
        if full == 0 {
            return;
        }
        let total = self.pending.len();
        let chunk: Vec<f32> = self.pending.drain(..full * BLOCK).collect();
        for (j, block) in chunk.chunks_exact(BLOCK).enumerate() {
            let after = total - (j + 1) * BLOCK;
            self.block(block, now - after as f64 / rate);
        }
    }

    /// One block of BLOCK samples at time `now` - `Analyzer.feed` in audio.py.
    fn block(&mut self, x: &[f32], now: f64) {
        self.blocks += 1;
        self.db = dbfs(x);
        self.db_smooth += (self.db - self.db_smooth) * self.scale.rate(0.25);
        if self.blocks == 1 {
            self.db_smooth = self.db;
        }
        self.db_min = self.db_min.min(self.db);
        self.db_max = self.db_max.max(self.db);
        push_bounded(&mut self.db_history, (now, self.db), self.history_len);
        if self.spectrum_on {
            self.spectrum.push(x); // one FFT serves the bands AND the kick band
            for k in 0..BANDS {
                self.bands[k] = self.spectrum.levels[k] as f32;
                self.peaks[k] = self.spectrum.peaks[k] as f32;
            }
        } else {
            self.spectrum.push_bass_only(x);
        }
        let energy = self.spectrum.bass_energy();
        self.bass_peak = energy.max(self.bass_peak * self.scale.decay(0.9988));
        let target = (energy / (self.bass_peak * 1.6 + 1e-9)).min(1.0);
        let rate = if target > self.bass { self.scale.rate(0.45) } else { self.scale.rate(0.16) };
        self.bass += (target - self.bass) * rate;
        self.music = self.db_smooth > LOUD_DB;
        let fired = self.tempo.push(energy, now, self.music);
        self.tempo.estimate(now);
        push_bounded(&mut self.bpm_history, (now, self.tempo.display_bpm), self.history_len);
        if fired {
            self.beats += 1;
            self.last_beat = Some(now);
            self.last_onset = Some(now);
        }
        let bpm = self.tempo.bpm();
        self.bpm = (bpm > 0).then_some(bpm as f32);
        self.confidence = self.tempo.confidence;
    }
}

fn push_bounded(q: &mut VecDeque<(f64, f64)>, v: (f64, f64), cap: usize) {
    if q.len() == cap {
        q.pop_front();
    }
    q.push_back(v);
}

/// A deterministic stand-in for the microphone (DemoAudio in audio.py, minus the noise
/// generator's exact sequence): a kick at `bpm`, off-beat hats, a bass line, a soft pad.
pub struct DemoAudio {
    pub bpm: f64,
    pub sample_rate: f64,
    pos: u64,
    seed: u64,
}

impl DemoAudio {
    pub fn new(bpm: f64, sample_rate: f64) -> Self {
        DemoAudio { bpm, sample_rate, pos: 0, seed: 7 }
    }

    pub fn now(&self) -> f64 {
        self.pos as f64 / self.sample_rate
    }

    fn noise(&mut self) -> f64 {
        // xorshift: reproducible, no dependency, roughly uniform in -1..1
        self.seed ^= self.seed << 13;
        self.seed ^= self.seed >> 7;
        self.seed ^= self.seed << 17;
        (self.seed >> 11) as f64 / (1u64 << 52) as f64 - 1.0
    }

    pub fn read(&mut self, n: usize) -> Vec<f32> {
        use std::f64::consts::PI;
        let (sr, beat) = (self.sample_rate, 60.0 / self.bpm);
        let mut out = Vec::with_capacity(n);
        for i in 0..n {
            let t = (self.pos + i as u64) as f64 / sr;
            let since = t.rem_euclid(beat);
            let kick = 0.85 * (-since * 14.0).exp() * (2.0 * PI * 55.0 * since).sin();
            let since_off = (t + beat / 2.0).rem_euclid(beat);
            let hat = 0.10 * (-since_off * 55.0).exp() * self.noise() * 1.7;
            let bar = (t / (4.0 * beat)).floor() as i64 % 2;
            let bass_hz = if bar == 0 { 82.4 } else { 73.4 };
            let bass = 0.14 * (2.0 * PI * bass_hz * t).sin();
            let pad = 0.035 * ((2.0 * PI * 220.0 * t).sin() + (2.0 * PI * 329.6 * t).sin() * 0.7
                + (2.0 * PI * 440.0 * t).sin() * 0.5 + (2.0 * PI * 1760.0 * t).sin() * 0.2);
            let noise = 0.004 * self.noise() * 1.7;
            out.push((kick + hat + bass + pad + noise).tanh() as f32);
        }
        self.pos += n as u64;
        out
    }
}

#[cfg(test)]
mod tests;
