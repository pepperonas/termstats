"""termstats.motion - what moves between two frames of a microphone screen.

Everything is a function of elapsed time, never of the frame count, so the picture looks the
same at 20 and at 30 frames a second and does not explode after a pause."""
import math

import pytest

np = pytest.importorskip("numpy")

from termstats import audio, motion  # noqa: E402


class Fake:
    """The slice of an Analyzer the motion layer reads."""
    def __init__(self, levels=None, peaks=None, beats=0, last_beat=None, bpm=0, db=-30.0, music=True):
        self.levels = levels if levels is not None else [0.0] * audio.BANDS
        self.peaks = peaks if peaks is not None else list(self.levels)
        self.beats, self.last_beat, self.bpm, self.db, self.music = beats, last_beat, bpm, db, music


def run(m, an, seconds, fps, t0=0.0):
    for i in range(1, int(round(seconds * fps)) + 1):
        m.update(an, t0 + i / fps)
    return t0 + int(round(seconds * fps)) / fps


def test_the_default_floor_is_the_analyzers():
    assert motion.DEFAULT_DB_FLOOR == audio.DB_FLOOR


# --- beat envelope ---------------------------------------------------------------------------

def test_a_new_beat_fires_the_envelope_to_one():
    m = motion.Motion(); an = Fake(beats=0)
    m.update(an, 0.0)
    an.beats = 1
    m.update(an, 0.03)
    assert m.beat_env == pytest.approx(1.0)


def test_the_envelope_decays_with_time_not_with_frames():
    m30, m15 = motion.Motion(), motion.Motion()
    an = Fake(beats=1)
    m30.update(an, 0.0); m15.update(an, 0.0)          # both fire on the first sight of beat 1
    run(m30, an, 0.5, 30); run(m15, an, 0.5, 15)
    assert 0.05 < m30.beat_env < 0.25
    assert m30.beat_env == pytest.approx(m15.beat_env, abs=0.02), "frame rate must not change the fade"


def test_no_beat_no_envelope():
    m = motion.Motion(); an = Fake(beats=0)
    run(m, an, 1.0, 30)
    assert m.beat_env == 0.0


# --- bars ---------------------------------------------------------------------------------

def test_bars_rise_fast_and_fall_slowly():
    m = motion.Motion(); an = Fake(levels=[0.0] * audio.BANDS)
    m.update(an, 0.0)
    an.levels = [1.0] * audio.BANDS
    run(m, an, 0.1, 30)
    risen = m.levels[0]
    an.levels = [0.0] * audio.BANDS
    run(m, an, 0.1, 30, t0=0.1)
    fallen = m.levels[0]
    assert risen > 0.9, risen
    assert 0.25 < fallen < 0.8, fallen


def test_bar_motion_is_frame_rate_independent():
    an = Fake(levels=[0.0] * audio.BANDS)
    m30, m15 = motion.Motion(), motion.Motion()
    m30.update(an, 0.0); m15.update(an, 0.0)
    an.levels = [1.0] * audio.BANDS
    run(m30, an, 0.2, 30); run(m15, an, 0.2, 15)
    assert m30.levels[3] == pytest.approx(m15.levels[3], abs=0.05)


def test_a_beat_punches_the_bars_a_little():
    m = motion.Motion(); an = Fake(levels=[0.5] * audio.BANDS, beats=0)
    run(m, an, 1.0, 30)
    calm = m.shown_levels()[0]
    an.beats = 1
    m.update(an, 1.03)
    punched = m.shown_levels()[0]
    assert calm == pytest.approx(0.5, abs=0.02)
    assert 0.5 < punched <= 1.0 and punched - calm < 0.25, "a nudge, not a jump"


# --- trails and peaks ---------------------------------------------------------------------

def test_trails_snap_up_with_the_bar_and_fall_with_gravity():
    m = motion.Motion(); an = Fake(levels=[1.0] * audio.BANDS)
    run(m, an, 0.3, 30)
    assert m.trails[0] == pytest.approx(m.levels[0], abs=0.02)
    an.levels = [0.0] * audio.BANDS
    t = run(m, an, 0.2, 30, t0=0.3)
    early_drop = 1.0 - m.trails[0]
    run(m, an, 0.2, 30, t0=t)
    late_drop = (1.0 - m.trails[0]) - early_drop
    assert late_drop > early_drop * 1.5, "gravity: the trail falls faster the longer it falls"
    assert m.trails[0] >= m.levels[0]


def test_trails_never_sit_below_the_bar():
    m = motion.Motion(); an = Fake(levels=[0.2] * audio.BANDS)
    rng = np.random.default_rng(1)
    for i in range(120):
        an.levels = [float(rng.uniform(0, 1))] * audio.BANDS
        m.update(an, i / 30)
        assert all(t >= l - 1e-9 for t, l in zip(m.trails, m.levels))


def test_peaks_hold_then_fall_with_gravity():
    m = motion.Motion(); an = Fake(levels=[0.9] * audio.BANDS)
    run(m, an, 0.3, 30)
    an.levels = [0.1] * audio.BANDS
    run(m, an, motion.PEAK_HOLD_S * 0.8, 30, t0=0.3)
    assert m.peaks[0] > 0.85, "still holding"
    run(m, an, 1.5, 30, t0=0.3 + motion.PEAK_HOLD_S * 0.8)
    assert m.peaks[0] < 0.3, "and gone"


# --- the level meter ------------------------------------------------------------------------

def test_meter_has_vu_ballistics():
    m = motion.Motion(); an = Fake(db=audio.DB_FLOOR)
    m.update(an, 0.0)
    an.db = -20.0
    run(m, an, 0.1, 30)
    up = m.meter
    an.db = audio.DB_FLOOR
    run(m, an, 0.1, 30, t0=0.1)
    down = m.meter
    target = (-20.0 - audio.DB_FLOOR) / -audio.DB_FLOOR * 100
    assert up > target * 0.9
    assert down > target * 0.4, "release is the slow side"


# --- beat phase ---------------------------------------------------------------------------------

def test_phase_sweeps_from_the_last_beat_across_one_period():
    m = motion.Motion(); an = Fake(bpm=120, last_beat=10.0, beats=4)
    m.update(an, 10.0)
    assert m.phase == pytest.approx(0.0, abs=0.01)
    m.update(an, 10.25)
    assert m.phase == pytest.approx(0.5, abs=0.02)
    m.update(an, 10.75)
    assert m.phase == pytest.approx(0.5, abs=0.02), "wraps every period"


def test_phase_is_none_without_a_tempo():
    m = motion.Motion(); an = Fake(bpm=0, last_beat=None)
    m.update(an, 1.0)
    assert m.phase is None


# --- robustness -----------------------------------------------------------------------------------

def test_a_long_pause_counts_as_a_short_step():
    m = motion.Motion(); an = Fake(levels=[1.0] * audio.BANDS, beats=1)
    m.update(an, 0.0)
    m.update(an, 60.0)                                # the laptop lid was closed
    assert m.beat_env > 0.2, "a minute away must not zero everything in one step"
    assert all(math.isfinite(v) for v in m.levels + m.trails + m.peaks)


def test_reset_forgets_everything():
    m = motion.Motion(); an = Fake(levels=[1.0] * audio.BANDS, beats=3)
    run(m, an, 0.5, 30)
    m.reset()
    assert m.beat_env == 0.0 and m.levels == [] and m.phase is None
