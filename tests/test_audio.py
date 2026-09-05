"""termstats.audio - the pure DSP behind -eq / -bpm / -db.

Everything here is fed synthetic blocks; no microphone, no sounddevice. numpy is the audio
extra's one hard dependency, so the module is skipped where it is not installed."""
import math

import pytest

np = pytest.importorskip("numpy")

from termstats import audio  # noqa: E402

SR, N = audio.SAMPLE_RATE, audio.BLOCK


class Tone:
    """A phase-CONTINUOUS sine, block after block. Restarting the phase at every block puts
    a click at each boundary - broadband energy that lights every band and hides the tone."""

    def __init__(self, freq, amp=1.0, sr=SR):
        self.freq, self.amp, self.sr, self._n = freq, amp, sr, 0

    def block(self, n=N):
        t = (self._n + np.arange(n)) / self.sr
        self._n += n
        return (self.amp * np.sin(2 * math.pi * self.freq * t)).astype(np.float32)


def sine(freq, n=N, amp=1.0, sr=SR, phase=0.0):
    """ONE block of a sine (fine on its own; use Tone for consecutive blocks)."""
    t = np.arange(n) / sr
    return (amp * np.sin(2 * math.pi * freq * t + phase)).astype(np.float32)


def silence(n=N):
    return np.zeros(n, dtype=np.float32)


# --- dBFS ---------------------------------------------------------------------------------

def test_dbfs_of_silence_is_the_floor():
    assert audio.dbfs(silence()) == audio.DB_FLOOR


def test_dbfs_of_a_full_scale_sine_is_minus_three():
    assert audio.dbfs(sine(1000)) == pytest.approx(-3.01, abs=0.1)


def test_dbfs_is_capped_at_zero():
    assert audio.dbfs(np.full(N, 4.0, dtype=np.float32)) == 0.0


def test_dbfs_of_a_quiet_sine_is_forty_db_below_a_loud_one():
    loud, quiet = audio.dbfs(sine(440, amp=1.0)), audio.dbfs(sine(440, amp=0.01))
    assert loud - quiet == pytest.approx(40.0, abs=0.2)


# --- spectrum bands -----------------------------------------------------------------------

def test_band_edges_are_log_spaced_from_40_hz_to_16_khz():
    sp = audio.Spectrum(SR, N)
    assert len(sp.edges) == audio.BANDS + 1
    assert sp.edges[0] == pytest.approx(audio.F_LO) and sp.edges[-1] == pytest.approx(audio.F_HI)
    ratios = [sp.edges[i + 1] / sp.edges[i] for i in range(audio.BANDS)]
    assert max(ratios) - min(ratios) < 1e-6, "not log-spaced"


def test_every_band_owns_at_least_one_fft_bin():
    sp = audio.Spectrum(SR, N)
    assert all(len(b) >= 1 for b in sp.bins)


def band_of(sp, freq):
    return next(k for k in range(audio.BANDS) if sp.edges[k] <= freq < sp.edges[k + 1])


@pytest.mark.parametrize("freq", [100.0, 1000.0, 8000.0])
def test_a_pure_tone_lights_its_own_band_most(freq):
    sp = audio.Spectrum(SR, N)
    tone = Tone(freq)
    for _ in range(20):
        levels = sp.push(tone.block())
    assert int(np.argmax(levels)) == band_of(sp, freq)
    others = [v for k, v in enumerate(levels) if abs(k - band_of(sp, freq)) > 1]
    assert max(others) < levels[band_of(sp, freq)] - 0.3, "a pure tone must not light distant bands"


def test_levels_stay_in_the_unit_interval():
    sp = audio.Spectrum(SR, N)
    for amp in (0.0, 0.01, 0.5, 1.0, 4.0):
        levels = sp.push(sine(440, amp=amp) + sine(3000, amp=amp / 2))
        assert all(0.0 <= v <= 1.0 for v in levels), levels


def test_silence_settles_to_zero():
    sp = audio.Spectrum(SR, N)
    tone = Tone(1000)
    for _ in range(10):
        sp.push(tone.block())
    for _ in range(200):
        levels = sp.push(silence())
    assert max(levels) < 0.02


def test_release_is_slower_than_attack():
    sp = audio.Spectrum(SR, N)
    k = band_of(sp, 1000.0)
    tone = Tone(1000)
    first = sp.push(tone.block())[k]
    for _ in range(10):
        settled = sp.push(tone.block())[k]
    for _ in range(sp.fft // N):                  # flush the rolling window: the raw level is 0 now
        after = sp.push(silence())[k]
    assert first > 0.3 * settled, "attack should be fast"
    assert 0.15 < after < settled, ("release should be gradual: the smoothing must still remember the tone "
                                    "once the window is empty - an instant release drops to 0 here")


# --- peak hold ------------------------------------------------------------------------------

def test_peaks_hold_above_the_level_then_fall():
    sp = audio.Spectrum(SR, N)
    k = band_of(sp, 1000.0)
    tone = Tone(1000)
    for _ in range(10):
        sp.push(tone.block())
    held = sp.peaks[k]
    assert held >= sp.levels[k] > 0.5
    for _ in range(3):
        sp.push(silence())
    assert sp.peaks[k] == pytest.approx(held, abs=0.05), "a peak holds for a moment"
    for _ in range(300):
        sp.push(silence())
    assert sp.peaks[k] < 0.05, "and falls away afterwards"


def test_peaks_never_sit_below_the_level():
    sp = audio.Spectrum(SR, N)
    rng = np.random.default_rng(1)
    for _ in range(50):
        sp.push(rng.standard_normal(N).astype(np.float32) * rng.uniform(0.0, 0.5))
        assert all(p >= l - 1e-6 for p, l in zip(sp.peaks, sp.levels))


# --- BPM --------------------------------------------------------------------------------

def pulse_train(bpm, seconds, block_s=N / SR, strong=8.0, base=1.0):
    """Band energy per block for a metronome: `strong` on the beat, `base` in between."""
    period = 60.0 / bpm
    t, out, next_beat = 0.0, [], 0.0
    while t < seconds:
        if t >= next_beat:
            out.append((t, strong)); next_beat += period
        else:
            out.append((t, base))
        t += block_s
    return out


def test_bpm_analyzer_locks_onto_a_metronome():
    a = audio.BpmAnalyzer()
    for t, e in pulse_train(128.0, 10.0):
        a.push(e, t)
        a.estimate(t)
    assert abs(a.bpm - 128) <= 2, a.bpm
    assert a.confidence > 0.8


def test_a_sustained_note_is_one_onset_not_twenty():
    """The SuperFlux rule: energy must RISE above the peak of a lagged window, so a bass note
    that is held for two seconds fires once when it starts, not on every block it stays loud."""
    a = audio.BpmAnalyzer()
    t, fired = 0.0, 0
    for _ in range(int(4.0 * SR / N)):           # calibration + quiet
        a.push(1.0, t); t += N / SR
    for _ in range(int(2.0 * SR / N)):           # a held note
        fired += a.push(8.0, t); t += N / SR
    assert fired == 1, fired


def test_bpm_analyzer_folds_slow_and_fast_pulses_into_range():
    slow = audio.BpmAnalyzer()
    for t, e in pulse_train(40.0, 15.0):
        slow.push(e, t); slow.estimate(t)
    assert audio.BPM_MIN <= slow.bpm <= audio.BPM_MAX
    assert slow.bpm in (80, 160), slow.bpm


def test_bpm_analyzer_reports_nothing_for_steady_noise():
    a = audio.BpmAnalyzer()
    rng = np.random.default_rng(3)
    t = 0.0
    for _ in range(400):
        a.push(1.0 + 0.05 * rng.standard_normal(), t); a.estimate(t); t += N / SR
    assert a.bpm == 0


def test_bpm_analyzer_forgets_after_silence():
    a = audio.BpmAnalyzer()
    last = 0.0
    for t, e in pulse_train(120.0, 8.0):
        a.push(e, t); a.estimate(t); last = t
    assert a.bpm > 0
    for i in range(300):
        t = last + i * N / SR
        a.push(1.0, t, allow=False); a.estimate(t)
    assert a.bpm == 0


def test_bpm_analyzer_ignores_onsets_while_the_room_is_quiet():
    a = audio.BpmAnalyzer()
    for t, e in pulse_train(128.0, 10.0):
        a.push(e, t, allow=False); a.estimate(t)
    assert a.bpm == 0


# --- the analyzer: one object, one feed() per block -------------------------------------------

def test_analyzer_exposes_db_levels_peaks_bpm_and_beats():
    an = audio.Analyzer(SR, N)
    t = 0.0
    fired = 0
    kick, hat = Tone(55), Tone(4000, amp=0.05)
    for t, e in pulse_train(120.0, 4.0):
        kick.amp = 0.8 if e > 1 else 0.05
        fired += an.feed(kick.block() + hat.block(), t)
    assert an.db <= 0.0 and an.db > audio.DB_FLOOR
    assert len(an.levels) == audio.BANDS and len(an.peaks) == audio.BANDS
    assert fired >= 1, "kicks at 55 Hz must register as beats"


def test_analyzer_beat_age_grows_between_beats():
    an = audio.Analyzer(SR, N)
    an.feed(sine(55, amp=0.9), 0.0)
    assert an.beat_age(0.0) >= 0.0
    for t in (0.5, 1.0, 1.5):
        an.feed(sine(55, amp=0.9) * 0.0, t)
    assert an.beat_age(1.5) >= an.beat_age(0.5) or an.last_beat is None


def test_analyzer_keeps_a_tempo_history_one_entry_per_block():
    an = audio.Analyzer(SR, N)
    for i in range(5):
        an.feed(silence(), i * N / SR)
    assert len(an.bpm_history) == 5 and all(b == 0 for _, b in an.bpm_history)


def test_analyzer_keeps_session_extremes_and_a_db_history():
    an = audio.Analyzer(SR, N)
    for i, amp in enumerate((0.01, 0.5, 0.1)):
        an.feed(sine(440, amp=amp), i * 0.1)
    assert an.db_max > an.db_min
    assert len(an.db_history) >= 3


# --- the demo synth ---------------------------------------------------------------------------

def test_demo_audio_is_deterministic_per_seed():
    a, b = audio.DemoAudio(seed=7), audio.DemoAudio(seed=7)
    assert np.array_equal(a.read(N), b.read(N))
    assert not np.array_equal(audio.DemoAudio(seed=8).read(N), audio.DemoAudio(seed=7).read(N))


def test_demo_audio_stays_within_full_scale():
    d = audio.DemoAudio(seed=7)
    for _ in range(200):
        block = d.read(N)
        assert block.dtype == np.float32 and len(block) == N
        assert float(np.abs(block).max()) <= 1.0


def test_demo_audio_has_the_tempo_it_claims():
    d = audio.DemoAudio(seed=7, bpm=126.0)
    an = audio.Analyzer(SR, N)
    for i in range(int(12 * SR / N)):
        an.feed(d.read(N), i * N / SR)
    assert abs(an.bpm - 126) <= 3, an.bpm
    assert an.confidence > 0.6


def test_demo_audio_has_a_clock():
    d = audio.DemoAudio(seed=7)
    t0 = d.now()
    d.read(N)
    assert d.now() == pytest.approx(t0 + N / SR)


def test_demo_audio_is_loud_enough_to_count_as_music():
    d = audio.DemoAudio(seed=7)
    an = audio.Analyzer(SR, N)
    for i in range(40):
        an.feed(d.read(N), i * N / SR)
    assert an.music


# --- the shown scale is positive ----------------------------------------------------------

def test_spl_turns_full_scale_into_a_readable_number():
    assert audio.spl(0.0) == pytest.approx(audio.SPL_OFFSET)
    assert audio.spl(audio.DB_FLOOR) == pytest.approx(audio.DB_FLOOR + audio.SPL_OFFSET)
    assert audio.spl(-60.0) == pytest.approx(audio.SPL_OFFSET - 60.0)


def test_the_offset_puts_a_quiet_room_and_loud_music_where_a_reader_expects_them():
    # Measured on this machine's built-in microphone: a quiet room sits near -67 dBFS,
    # music at a normal listening level near -20. The offset is chosen so those land on
    # the numbers a phone app would show, and it matches the house's disco-controller.
    assert 25 <= audio.spl(-67.0) <= 45
    assert 70 <= audio.spl(-20.0) <= 90


def test_the_analyzer_keeps_dbfs_not_the_shown_scale():
    """The conversion happens when a number is drawn, never in what is measured or stored:
    0 dBFS is a defined ceiling, and a shifted value would no longer be dBFS at all."""
    an = audio.Analyzer(SR, N)
    an.feed(silence(), 0.0)
    assert an.db == audio.DB_FLOOR < 0
    assert an.db_history[-1][1] == an.db
