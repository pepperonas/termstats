//! The analysis against what the Python program predicts. Every test tone is phase-
//! CONTINUOUS across blocks: a sine restarted at phase 0 every block clicks at each
//! boundary, and the click's broadband energy lights every band (termstats CLAUDE.md).

use super::fft::Fft;
use super::spectrum::band_edges;
use super::*;

/// A sine that keeps its phase from block to block.
struct Tone {
    freq: f64,
    amp: f64,
    sr: f64,
    n: u64,
}

impl Tone {
    fn new(freq: f64, sr: f64) -> Self {
        Tone { freq, amp: 1.0, sr, n: 0 }
    }
    fn block(&mut self, len: usize) -> Vec<f32> {
        let out = (0..len)
            .map(|i| (self.amp * (2.0 * std::f64::consts::PI * self.freq * (self.n + i as u64) as f64 / self.sr).sin()) as f32)
            .collect();
        self.n += len as u64;
        out
    }
}

/// Kicks at `bpm`: a 55 Hz burst decaying like the demo's kick, from absolute sample time.
struct Clicks {
    bpm: f64,
    sr: f64,
    n: u64,
    until: f64,
}

impl Clicks {
    fn block(&mut self, len: usize) -> Vec<f32> {
        let beat = 60.0 / self.bpm;
        let out = (0..len)
            .map(|i| {
                let t = (self.n + i as u64) as f64 / self.sr;
                if t >= self.until {
                    return 0.0;
                }
                let since = t.rem_euclid(beat);
                (0.85 * (-since * 14.0).exp() * (2.0 * std::f64::consts::PI * 55.0 * since).sin()) as f32
            })
            .collect();
        self.n += len as u64;
        out
    }
    fn now(&self) -> f64 {
        self.n as f64 / self.sr
    }
}

/// A small reproducible noise source for the FFT test.
fn noise(len: usize, mut seed: u64) -> Vec<f32> {
    (0..len)
        .map(|_| {
            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            ((seed >> 33) as f64 / (1u64 << 31) as f64 - 1.0) as f32
        })
        .collect()
}

#[test]
fn fft_equals_a_naive_dft() {
    let n = 64;
    let x = noise(n, 11);
    let mut out = vec![0.0f32; n / 2 + 1];
    Fft::new(n).magnitudes(&x, &mut out);
    for (k, &got) in out.iter().enumerate() {
        let (mut re, mut im) = (0.0f64, 0.0f64);
        for (i, &v) in x.iter().enumerate() {
            let a = -2.0 * std::f64::consts::PI * (k * i) as f64 / n as f64;
            re += v as f64 * a.cos();
            im += v as f64 * a.sin();
        }
        let want = re.hypot(im);
        assert!((got as f64 - want).abs() < 1e-3, "bin {k}: {got} vs {want}");
    }
}

#[test]
#[should_panic]
fn fft_size_must_be_a_power_of_two() {
    Fft::new(100);
}

#[test]
fn the_band_edges_are_numpys() {
    // Printed by termstats/audio.py: edges[0], edges[1], edges[27], edges[28] at 44.1 kHz.
    let e = band_edges(44100.0);
    assert_eq!(e.len(), BANDS + 1);
    for (got, want) in [(e[0], 39.99999999999999), (e[1], 49.543958737822166), (e[27], 12917.821189597826),
                        (e[28], 16000.00000000001)] {
        assert!((got - want).abs() < 1e-9, "{got} vs {want}");
    }
}

/// 1 kHz lies in band 15 at both rates (Python: band_of(Spectrum(sr), 1000.0) == 15).
#[test]
fn a_one_kilohertz_sine_lights_band_fifteen_at_both_rates() {
    for sr in [44100.0, 48000.0] {
        let edges = band_edges(sr);
        let predicted = (0..BANDS).find(|&k| edges[k] <= 1000.0 && 1000.0 < edges[k + 1]).unwrap();
        assert_eq!(predicted, 15);
        let mut an = Analyzer::new();
        let mut tone = Tone::new(1000.0, sr);
        for i in 0..30 {
            an.feed(&tone.block(BLOCK), sr as f32, i as f64 * BLOCK as f64 / sr);
        }
        let (top, _) = an.bands.iter().enumerate().fold((0, f32::MIN), |b, (k, &v)| if v > b.1 { (k, v) } else { b });
        assert_eq!(top, predicted, "{sr}: {:?}", an.bands);
        let far = an.bands.iter().enumerate().filter(|(k, _)| k.abs_diff(predicted) > 1).map(|p| *p.1).fold(0.0, f32::max);
        assert!(far < an.bands[predicted] - 0.3, "a pure tone must not light distant bands");
        assert_eq!(an.sample_rate(), sr);
    }
}

#[test]
fn silence_is_the_floor_without_music_or_tempo() {
    let mut an = Analyzer::new();
    for i in 0..300 {
        an.feed(&[0.0; BLOCK], 44100.0, i as f64 * 0.0232);
    }
    assert_eq!(an.db, DB_FLOOR);
    assert!(!an.music);
    assert_eq!(an.bpm, None);
    assert_eq!(an.beats, 0);
}

#[test]
fn a_full_scale_sine_is_minus_three_dbfs() {
    let mut tone = Tone::new(1000.0, 44100.0);
    assert!((dbfs(&tone.block(BLOCK)) - -3.01).abs() < 0.1);
    assert_eq!(dbfs(&[]), DB_FLOOR);
}

fn click_train(sr: f64, chunk: usize, until: f64, total: f64) -> (Analyzer, Vec<(f64, Option<f32>)>) {
    let mut an = Analyzer::new();
    let mut clicks = Clicks { bpm: 126.0, sr, n: 0, until };
    let mut seen = Vec::new();
    while clicks.now() < total {
        let block = clicks.block(chunk);
        an.feed(&block, sr as f32, clicks.now());
        seen.push((clicks.now(), an.bpm));
    }
    (an, seen)
}

#[test]
fn a_126_bpm_click_train_is_found() {
    for (sr, chunk) in [(44100.0, BLOCK), (48000.0, 700), (44100.0, 3000)] {
        let (an, _) = click_train(sr, chunk, 12.0, 12.0);
        let bpm = an.bpm.unwrap_or(0.0);
        assert!((124.0..=128.0).contains(&bpm), "{sr} Hz in chunks of {chunk}: {bpm}");
        assert!(an.beats > 10 && an.confidence > 0.5, "{} beats, conf {}", an.beats, an.confidence);
    }
}

#[test]
fn the_tempo_is_dropped_four_seconds_after_the_last_onset() {
    let (an, seen) = click_train(44100.0, BLOCK, 12.0, 20.0);
    let last = an.last_onset.expect("onsets");
    assert!(last < 12.0);
    let dropped = seen.iter().find(|(t, b)| *t > last && b.is_none()).map(|p| p.0).expect("never dropped");
    // Python: stale once fewer than four onsets are in the 6 s window AND 4 s have passed.
    assert!(dropped > last + STALE_S && dropped < last + STALE_S + 1.5, "dropped at {dropped}, last onset {last}");
    assert!(seen.iter().filter(|(t, _)| *t > last && *t < last + STALE_S).all(|(_, b)| b.is_some()));
}

const STALE_S: f64 = bpm::STALE_RESET_S;

#[test]
fn a_long_silence_then_one_block_stays_finite() {
    let mut an = Analyzer::new();
    let mut tone = Tone::new(220.0, 44100.0);
    for i in 0..50 {
        an.feed(&tone.block(BLOCK), 44100.0, i as f64 * 0.0232);
    }
    an.feed(&tone.block(BLOCK), 44100.0, 50.0 * 0.0232 + 10.0);
    for v in [an.db, an.db_smooth, an.bass, an.confidence, an.onset_rate()] {
        assert!(v.is_finite(), "{v}");
    }
    assert!(an.bands.iter().chain(an.peaks.iter()).all(|v| v.is_finite() && (0.0..=1.0).contains(v)));
}

#[test]
fn rechunking_stamps_blocks_with_increasing_times_ending_at_now() {
    let mut an = Analyzer::new();
    an.feed(&vec![0.1; 3 * BLOCK + 100], 44100.0, 10.0);
    let times: Vec<f64> = an.db_history.iter().map(|p| p.0).collect();
    assert_eq!(times.len(), 3);
    assert!(times.windows(2).all(|w| w[1] > w[0]));
    assert!((times[2] - (10.0 - 100.0 / 44100.0)).abs() < 1e-9);
    an.feed(&vec![0.1; BLOCK - 100], 44100.0, 10.5); // the remainder completes a fourth block
    assert_eq!(an.db_history.len(), 4);
}

#[test]
fn garbage_is_ignored() {
    let mut an = Analyzer::new();
    an.feed(&[f32::NAN; BLOCK], 44100.0, 1.0);
    assert!(an.db.is_finite());
    for rate in [0.0, -1.0, f32::NAN, f32::INFINITY, 1e9] {
        an.feed(&[0.5; BLOCK], rate, 2.0);
    }
    an.feed(&[0.5; BLOCK], 44100.0, f64::NAN);
    assert_eq!(an.blocks, 1);
}

#[test]
fn a_new_rate_starts_afresh() {
    let mut an = Analyzer::new();
    an.feed(&[0.5; BLOCK], 44100.0, 1.0);
    an.spectrum_on = false;
    an.feed(&[0.5; 100], 48000.0, 2.0);
    assert_eq!(an.blocks, 0);
    assert_eq!(an.sample_rate(), 48000.0);
    assert!(!an.spectrum_on, "the switch belongs to the caller and survives");
}

#[test]
fn scale_is_the_identity_at_the_reference_rate() {
    let s = Scale::for_rate(44100.0);
    assert_eq!(s.rate(0.55), 0.55);
    assert_eq!(s.decay(0.9988), 0.9988);
    assert_eq!(s.blocks(6.0), 6);
    assert_eq!(s.step(0.012), 0.012);
    // Twice the rate: a block is half as long, a rate applied twice gives the reference rate.
    let s = Scale::for_rate(88200.0);
    assert!(((1.0 - (1.0 - s.rate(0.55)).powi(2)) - 0.55).abs() < 1e-12);
    assert_eq!(s.blocks(6.0), 12);
}

#[test]
fn without_the_spectrum_the_kick_band_is_the_same() {
    let mut a = Analyzer::new();
    let mut b = Analyzer::new();
    b.spectrum_on = false;
    let mut clicks = Clicks { bpm: 126.0, sr: 44100.0, n: 0, until: 1e9 };
    for _ in 0..200 {
        let block = clicks.block(BLOCK);
        a.feed(&block, 44100.0, clicks.now());
        b.feed(&block, 44100.0, clicks.now());
        assert!((a.spectrum.bass_energy() - b.spectrum.bass_energy()).abs() <= 1e-3 * a.spectrum.bass_energy().max(1.0));
    }
    assert_eq!(a.beats, b.beats);
    assert_eq!(b.bands, [0.0; BANDS], "no FFT, no bands");
}

#[test]
fn the_demo_has_its_tempo() {
    let mut demo = DemoAudio::new(126.0, 44100.0);
    let mut an = Analyzer::new();
    while demo.now() < 12.0 {
        let block = demo.read(BLOCK);
        an.feed(&block, 44100.0, demo.now());
    }
    let bpm = an.bpm.unwrap_or(0.0);
    assert!((124.0..=128.0).contains(&bpm), "{bpm}");
    assert!(an.music);
}

/// The same signal through termstats/audio.py (numpy) printed these numbers: at the
/// reference rate the port must give them back, band for band.
#[test]
fn at_44_1_khz_the_numbers_are_pythons() {
    let sr = 44100.0;
    let mut an = Analyzer::new();
    for b in 0..400u64 {
        let x: Vec<f32> = (b * 1024..(b + 1) * 1024)
            .map(|n| {
                let t = n as f64 / sr;
                let s = t.rem_euclid(60.0 / 126.0);
                (0.85 * (-s * 14.0).exp() * (2.0 * std::f64::consts::PI * 55.0 * s).sin()
                    + 0.2 * (2.0 * std::f64::consts::PI * 1000.0 * t).sin()) as f32
            })
            .collect();
        an.feed(&x, sr as f32, (b + 1) as f64 * 1024.0 / sr);
    }
    let close = |got: f64, want: f64, what: &str| assert!((got - want).abs() < 2e-3, "{what}: {got} vs {want}");
    close(an.db, -16.8797, "db");
    close(an.db_smooth, -15.598, "db_smooth");
    close(an.confidence, 0.9663, "confidence");
    close(an.bass, 0.2118, "bass");
    assert_eq!((an.bpm, an.beats), (Some(123.0), 13));
    let python = [0.697, 0.81, 0.737, 0.237, 0.155, 0.114, 0.094, 0.08, 0.064, 0.05, 0.037, 0.026, 0.016, 0.006,
                  0.775, 0.85, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0];
    for (k, (&got, want)) in an.bands.iter().zip(python).enumerate() {
        close(got as f64, want, &format!("band {k}"));
    }
}

#[test]
fn at_a_low_rate_every_kick_bin_is_measured_without_the_fft() {
    // 8 kHz: 2 Hz bins, about forty of them in the kick band - more than one pass holds.
    let sr = 8000.0;
    let (mut a, mut b) = (Analyzer::with_rate(sr), Analyzer::with_rate(sr));
    b.spectrum_on = false;
    assert!(a.spectrum.bass_bins.len() > 32, "{}", a.spectrum.bass_bins.len());
    let mut clicks = Clicks { bpm: 126.0, sr, n: 0, until: 1e9 };
    for _ in 0..40 {
        let block = clicks.block(BLOCK);
        a.feed(&block, sr as f32, clicks.now());
        b.feed(&block, sr as f32, clicks.now());
    }
    for &bin in &a.spectrum.bass_bins {
        let (x, y) = (a.spectrum.magnitude[bin], b.spectrum.magnitude[bin]);
        assert!((x - y).abs() <= 1e-3 * x.max(1.0), "bin {bin}: {x} vs {y}");
    }
}
