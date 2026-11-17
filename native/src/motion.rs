//! What moves between two frames of a microphone screen - a port of termstats/motion.py.
//!
//! The analyzer yields numbers per audio block, the screen draws at its own rate. This layer
//! makes every drawn quantity a function of ELAPSED TIME: bars ease (fast attack, slow
//! release), a trail falls under gravity, peaks hold and then fall, a beat is an impulse
//! that decays, the level meter has VU ballistics, and the beat phase sweeps 0..1 between
//! two beats. A frame gap longer than MAX_DT counts as MAX_DT.

use crate::audio::{Analyzer, DB_FLOOR};

pub const MAX_DT: f64 = 0.25;
pub const BEAT_TAU: f64 = 0.25;
pub const ATTACK_TAU: f64 = 0.02;
pub const RELEASE_TAU: f64 = 0.12;
pub const PUNCH: f64 = 0.12;
pub const TRAIL_G: f64 = 4.0;
pub const PEAK_HOLD_S: f64 = 0.4;
pub const PEAK_G: f64 = 1.6;
pub const METER_ATTACK_TAU: f64 = 0.03;
pub const METER_RELEASE_TAU: f64 = 0.35;
pub const ENV_FLOOR: f64 = 0.01;
pub const DEFAULT_DB_FLOOR: f64 = DB_FLOOR;

/// Move `current` towards `target` as an exponential with time constant `tau`.
pub fn ease(current: f64, target: f64, dt: f64, tau: f64) -> f64 {
    if tau <= 0.0 {
        return target;
    }
    current + (target - current) * (1.0 - (-dt / tau).exp())
}

/// What one step reads from the analyzer.
pub struct Input<'a> {
    pub levels: &'a [f32],
    pub beats: u64,
    pub db: f64,
    pub bpm: Option<f32>,
    pub last_beat: Option<f64>,
}

impl<'a> Input<'a> {
    pub fn of(an: &'a Analyzer) -> Self {
        Input { levels: &an.bands, beats: an.beats, db: an.db, bpm: an.bpm, last_beat: an.last_beat }
    }
}

#[derive(Default)]
pub struct Motion {
    pub db_floor: f64,
    t: Option<f64>,
    beats_seen: u64,
    pub beat_env: f64,
    pub levels: Vec<f64>,
    pub trails: Vec<f64>,
    trail_v: Vec<f64>,
    pub peaks: Vec<f64>,
    peak_v: Vec<f64>,
    peak_hold: Vec<f64>,
    pub meter: f64,
    pub phase: Option<f64>,
}

impl Motion {
    pub fn new() -> Self {
        Motion { db_floor: DEFAULT_DB_FLOOR, ..Default::default() }
    }

    pub fn reset(&mut self) {
        *self = Motion { db_floor: self.db_floor, ..Default::default() };
    }

    /// Advance to `now` with the analyzer's current numbers.
    pub fn step(&mut self, an: &Analyzer, now: f64) {
        self.update(&Input::of(an), now);
    }

    pub fn update(&mut self, input: &Input, now: f64) {
        let dt = match self.t {
            None => 0.0,
            Some(t) => (now - t).clamp(0.0, MAX_DT),
        };
        let dt = if dt.is_finite() { dt } else { 0.0 };
        self.t = Some(now);
        let targets: Vec<f64> = input.levels.iter().map(|&v| v as f64).collect();
        if self.levels.len() != targets.len() {
            self.levels = targets.clone();
            self.trails = targets.clone();
            self.trail_v = vec![0.0; targets.len()];
            self.peaks = targets.clone();
            self.peak_v = vec![0.0; targets.len()];
            self.peak_hold = vec![PEAK_HOLD_S; targets.len()];
        }

        // the beat: an impulse on every new onset, then an exponential fade
        if input.beats > self.beats_seen {
            self.beat_env = 1.0;
            self.beats_seen = input.beats;
        } else {
            self.beat_env *= (-dt / BEAT_TAU).exp();
            if self.beat_env < ENV_FLOOR {
                self.beat_env = 0.0;
            }
        }

        for (i, &target) in targets.iter().enumerate() {
            let cur = self.levels[i];
            let tau = if target > cur { ATTACK_TAU } else { RELEASE_TAU };
            let level = ease(cur, target, dt, tau);
            self.levels[i] = level;

            // trail: rides up with the bar, falls away under gravity
            if level >= self.trails[i] {
                self.trails[i] = level;
                self.trail_v[i] = 0.0;
            } else {
                self.trail_v[i] += TRAIL_G * dt;
                self.trails[i] = level.max(self.trails[i] - self.trail_v[i] * dt);
            }

            // peak marker: rides up, holds, then falls under gravity
            if level >= self.peaks[i] {
                self.peaks[i] = level;
                self.peak_v[i] = 0.0;
                self.peak_hold[i] = PEAK_HOLD_S;
            } else if self.peak_hold[i] > 0.0 {
                self.peak_hold[i] -= dt;
            } else {
                self.peak_v[i] += PEAK_G * dt;
                self.peaks[i] = level.max(self.peaks[i] - self.peak_v[i] * dt);
            }
        }

        // the level meter, in percent of the shown scale, with VU ballistics
        let floor = self.db_floor;
        let target = ((input.db - floor) / -floor * 100.0).clamp(0.0, 100.0);
        let tau = if target > self.meter { METER_ATTACK_TAU } else { METER_RELEASE_TAU };
        self.meter = ease(self.meter, target, dt, tau);

        // where we are between two beats
        self.phase = match (input.bpm, input.last_beat) {
            (Some(bpm), Some(last)) if bpm > 0.0 => Some(((now - last) / (60.0 / bpm as f64)).rem_euclid(1.0)),
            _ => None,
        };
    }

    /// The bar heights to draw: the eased levels, nudged up by the beat.
    pub fn shown_levels(&self) -> Vec<f64> {
        let gain = 1.0 + PUNCH * self.beat_env;
        self.levels.iter().map(|v| (v * gain).min(1.0)).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(levels: &[f32], beats: u64, db: f64) -> Input<'_> {
        Input { levels, beats, db, bpm: None, last_beat: None }
    }

    /// Steps at a realistic 30 frames a second - one big jump would hit the MAX_DT clamp.
    fn run(m: &mut Motion, levels: &[f32], beats: u64, db: f64, from: f64, to: f64) -> f64 {
        let mut t = from;
        while t < to {
            t += 1.0 / 30.0;
            m.update(&input(levels, beats, db), t);
        }
        t
    }

    #[test]
    fn the_first_step_takes_the_targets_as_they_are() {
        let mut m = Motion::new();
        m.update(&input(&[0.5, 0.2], 0, -80.0), 1.0);
        assert_eq!(m.levels, vec![0.5, 0.2_f32 as f64]);
        assert_eq!(m.trails, m.levels);
        assert_eq!(m.peaks, m.levels);
    }

    #[test]
    fn rise_is_fast_and_fall_is_slow() {
        let mut m = Motion::new();
        m.update(&input(&[0.0], 0, -80.0), 0.0);
        m.update(&input(&[1.0], 0, -80.0), 0.03);
        let up = m.levels[0];
        let mut d = Motion::new();
        d.update(&input(&[1.0], 0, -80.0), 0.0);
        d.update(&input(&[0.0], 0, -80.0), 0.03);
        let down = 1.0 - d.levels[0];
        assert!(up > 0.7, "{up}");
        assert!(down < 0.25, "{down}");
        assert!((up - (1.0 - (-0.03f64 / ATTACK_TAU).exp())).abs() < 1e-12);
    }

    #[test]
    fn a_trail_stays_above_a_falling_bar_and_never_below_it() {
        let mut m = Motion::new();
        m.update(&input(&[1.0], 0, -80.0), 0.0);
        let mut t = 0.0;
        for _ in 0..20 {
            t += 1.0 / 30.0;
            m.update(&input(&[0.0], 0, -80.0), t);
            assert!(m.trails[0] >= m.levels[0]);
        }
        assert!(m.trails[0] > m.levels[0], "the trail lingers above the bar for a while");
    }

    #[test]
    fn a_peak_holds_then_falls_with_gravity() {
        let mut m = Motion::new();
        m.update(&input(&[1.0], 0, -80.0), 0.0);
        let t = run(&mut m, &[0.0], 0, -80.0, 0.0, PEAK_HOLD_S - 0.05);
        assert_eq!(m.peaks[0], 1.0, "held");
        let before = m.peaks[0];
        run(&mut m, &[0.0], 0, -80.0, t, t + 0.3);
        assert!(m.peaks[0] < before);
    }

    #[test]
    fn a_beat_is_an_impulse_that_decays_and_snaps_to_zero() {
        let mut m = Motion::new();
        m.update(&input(&[0.0], 0, -80.0), 0.0);
        m.update(&input(&[0.0], 1, -80.0), 0.033);
        assert_eq!(m.beat_env, 1.0);
        let t = run(&mut m, &[0.0], 1, -80.0, 0.033, 0.033 + BEAT_TAU);
        assert!((m.beat_env - (-(t - 0.033) / BEAT_TAU).exp()).abs() < 0.02, "{}", m.beat_env);
        run(&mut m, &[0.0], 1, -80.0, t, t + 2.0);
        assert_eq!(m.beat_env, 0.0, "below ENV_FLOOR it is over");
    }

    #[test]
    fn a_long_pause_is_one_short_step() {
        let mut m = Motion::new();
        m.update(&input(&[1.0], 1, -80.0), 0.0);
        m.update(&input(&[1.0], 1, -80.0), 3600.0);
        assert!((m.beat_env - (-MAX_DT / BEAT_TAU).exp()).abs() < 1e-12);
    }

    #[test]
    fn the_meter_rises_fast_and_falls_slowly_in_percent() {
        let mut m = Motion::new();
        m.update(&input(&[], 0, -80.0), 0.0);
        run(&mut m, &[], 0, -40.0, 0.0, 0.2);
        assert!((m.meter - 50.0).abs() < 1.0, "{}", m.meter);
        let t = run(&mut m, &[], 0, -80.0, 0.2, 0.3);
        assert!(m.meter > 30.0, "{}", m.meter);
        run(&mut m, &[], 0, 10.0, t, t + 1.0);
        assert!(m.meter <= 100.0);
    }

    #[test]
    fn the_phase_sweeps_between_beats() {
        let mut m = Motion::new();
        let i = Input { levels: &[], beats: 1, db: -80.0, bpm: Some(120.0), last_beat: Some(10.0) };
        m.update(&i, 10.25);
        assert!((m.phase.unwrap() - 0.5).abs() < 1e-9);
        m.update(&Input { bpm: None, ..i }, 10.3);
        assert_eq!(m.phase, None);
    }

    #[test]
    fn the_beat_punches_the_bars_but_never_past_full() {
        let mut m = Motion::new();
        m.update(&input(&[0.5, 0.99], 0, -80.0), 0.0);
        m.update(&input(&[0.5, 0.99], 1, -80.0), 0.0);
        let shown = m.shown_levels();
        assert!((shown[0] - 0.5 * (1.0 + PUNCH)).abs() < 1e-9);
        assert_eq!(shown[1], 1.0);
    }
}
