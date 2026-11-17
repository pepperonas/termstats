//! Log-spaced band levels in 0..1 with peak-hold markers, from a rolling FFT window.
//! A port of `Spectrum` in termstats/audio.py; the per-block constants there are tuned for
//! 44.1 kHz and are scaled here by the block's real duration (see `Scale`).

use super::fft::Fft;
use super::{Scale, BANDS, BASS_HI, BASS_LO, DB_FLOOR, F_HI, F_LO};

pub const FFT: usize = 4096;
pub const ATTACK: f64 = 0.55;
pub const RELEASE: f64 = 0.18;
pub const RANGE_DB: f64 = 60.0;
pub const CEIL_ATTACK: f64 = 0.35;
pub const CEIL_DECAY: f64 = 0.02;
pub const CEIL_HEADROOM_DB: f64 = 3.0;
pub const PEAK_HOLD_BLOCKS: f64 = 6.0;
pub const PEAK_FALL: f64 = 0.012;

/// `np.logspace(log10(lo), log10(hi), bands + 1)`, including numpy's exact last point.
pub fn band_edges(sample_rate: f64) -> Vec<f64> {
    let hi = F_HI.min(sample_rate / 2.0);
    let (a, b) = (F_LO.log10(), hi.log10());
    let step = (b - a) / BANDS as f64;
    (0..=BANDS).map(|i| 10f64.powf(if i == BANDS { b } else { a + i as f64 * step })).collect()
}

/// The frequency of rfft bin `k`.
fn bin_freq(k: usize, sample_rate: f64) -> f64 {
    k as f64 * sample_rate / FFT as f64
}

pub struct Spectrum {
    sample_rate: f64,
    fft: Fft,
    buf: Vec<f32>,
    pub(crate) window: Vec<f32>,
    windowed: Vec<f32>,
    pub magnitude: Vec<f32>,
    pub edges: Vec<f64>,
    bins: Vec<Vec<usize>>,
    pub bass_bins: Vec<usize>,
    full_scale: f64,
    ceiling: f64,
    pub levels: [f64; BANDS],
    pub peaks: [f64; BANDS],
    hold: [i64; BANDS],
    attack: f64,
    release: f64,
    ceil_attack: f64,
    ceil_decay: f64,
    hold_blocks: i64,
    fall: f64,
}

impl Spectrum {
    pub fn new(sample_rate: f64, scale: Scale) -> Self {
        let nbins = FFT / 2 + 1;
        let edges = band_edges(sample_rate);
        let mut bins = Vec::with_capacity(BANDS);
        for k in 0..BANDS {
            let mut idx: Vec<usize> =
                (0..nbins).filter(|&b| { let f = bin_freq(b, sample_rate); f >= edges[k] && f < edges[k + 1] }).collect();
            if idx.is_empty() {
                let centre = (edges[k] + edges[k + 1]) / 2.0;
                let nearest = (0..nbins)
                    .min_by(|&x, &y| (bin_freq(x, sample_rate) - centre).abs().total_cmp(&(bin_freq(y, sample_rate) - centre).abs()))
                    .unwrap_or(0);
                idx.push(nearest);
            }
            bins.push(idx);
        }
        let bass_bins = (0..nbins).filter(|&b| (BASS_LO..BASS_HI).contains(&bin_freq(b, sample_rate))).collect();
        // np.hanning: the symmetric Hann window
        let window = (0..FFT)
            .map(|i| (0.5 - 0.5 * (2.0 * std::f64::consts::PI * i as f64 / (FFT - 1) as f64).cos()) as f32)
            .collect();
        Spectrum {
            sample_rate,
            fft: Fft::new(FFT),
            buf: vec![0.0; FFT],
            window,
            windowed: vec![0.0; FFT],
            magnitude: vec![0.0; nbins],
            edges,
            bins,
            bass_bins,
            full_scale: FFT as f64 / 4.0,
            ceiling: DB_FLOOR + RANGE_DB,
            levels: [0.0; BANDS],
            peaks: [0.0; BANDS],
            hold: [0; BANDS],
            attack: scale.rate(ATTACK),
            release: scale.rate(RELEASE),
            ceil_attack: scale.rate(CEIL_ATTACK),
            ceil_decay: scale.rate(CEIL_DECAY),
            hold_blocks: scale.blocks(PEAK_HOLD_BLOCKS),
            fall: scale.step(PEAK_FALL),
        }
    }

    pub fn sample_rate(&self) -> f64 {
        self.sample_rate
    }

    fn roll(&mut self, block: &[f32]) {
        let n = block.len().min(FFT);
        if n > 0 {
            self.buf.copy_within(n.., 0);
            self.buf[FFT - n..].copy_from_slice(&block[block.len() - n..]);
        }
        for ((w, x), h) in self.windowed.iter_mut().zip(&self.buf).zip(&self.window) {
            *w = x * h;
        }
    }

    /// The whole analysis: the window moves, the FFT runs, bands and peaks follow.
    pub fn push(&mut self, block: &[f32]) {
        self.roll(block);
        self.fft.magnitudes(&self.windowed, &mut self.magnitude);
        let mut band_db = [0.0f64; BANDS];
        for (k, idx) in self.bins.iter().enumerate() {
            let m = idx.iter().map(|&b| self.magnitude[b] as f64).fold(f64::MIN, f64::max);
            band_db[k] = 20.0 * (m / self.full_scale + 1e-12).log10();
        }
        let loudest = band_db.iter().cloned().fold(f64::MIN, f64::max);
        let target = (DB_FLOOR + RANGE_DB).max(loudest + CEIL_HEADROOM_DB);
        let rate = if target > self.ceiling { self.ceil_attack } else { self.ceil_decay };
        self.ceiling += (target - self.ceiling) * rate;
        for k in 0..BANDS {
            let raw = ((band_db[k] - (self.ceiling - RANGE_DB)) / RANGE_DB).clamp(0.0, 1.0);
            let rate = if raw > self.levels[k] { self.attack } else { self.release };
            self.levels[k] += (raw - self.levels[k]) * rate;
            // peak hold: a new maximum resets the hold; after the hold the marker falls
            if self.levels[k] >= self.peaks[k] {
                self.peaks[k] = self.levels[k];
                self.hold[k] = self.hold_blocks;
            } else {
                self.hold[k] = (self.hold[k] - 1).max(0);
                if self.hold[k] == 0 {
                    self.peaks[k] = (self.peaks[k] - self.fall).max(self.levels[k]);
                }
            }
        }
    }

    /// Only the window moves and only the kick band is measured - for when no equalizer is
    /// drawn. Goertzel on the bass bins gives the same magnitudes the FFT would.
    pub fn push_bass_only(&mut self, block: &[f32]) {
        self.roll(block);
        // The bins in passes of up to 16 over the window: their recurrences are independent,
        // so the processor runs them side by side instead of one long dependency chain each.
        const LANES: usize = 16;
        let windowed = &self.windowed;
        for bins in self.bass_bins.chunks(LANES) {
            let n = bins.len();
            let mut coeff = [0.0f64; LANES];
            for (c, &b) in coeff.iter_mut().zip(bins) {
                *c = 2.0 * (2.0 * std::f64::consts::PI * b as f64 / FFT as f64).cos();
            }
            let (mut s1, mut s2) = ([0.0f64; LANES], [0.0f64; LANES]);
            for &x in windowed {
                let x = x as f64;
                for i in 0..n {
                    let s = x + coeff[i] * s1[i] - s2[i];
                    s2[i] = s1[i];
                    s1[i] = s;
                }
            }
            for (i, &b) in bins.iter().enumerate() {
                let power = (s1[i] * s1[i] + s2[i] * s2[i] - coeff[i] * s1[i] * s2[i]).max(0.0);
                self.magnitude[b] = power.sqrt() as f32;
            }
        }
    }

    /// The mean magnitude over the kick band, the tempo's input.
    pub fn bass_energy(&self) -> f64 {
        if self.bass_bins.is_empty() {
            return 0.0;
        }
        self.bass_bins.iter().map(|&b| self.magnitude[b] as f64).sum::<f64>() / self.bass_bins.len() as f64
    }
}
