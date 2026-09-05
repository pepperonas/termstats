"""Pure DSP for the microphone modes: -eq (spectrum), -bpm (tempo), -db (level).

Nothing here touches a device or a screen. Blocks of float32 samples come in through
`Analyzer.feed()`, numbers come out; `capture.py` owns the microphone and `cli.py` the
drawing. numpy is the audio extra's one hard dependency (the FFT); the system dashboard
never imports this module, so it stays installable without numpy.

Beat and tempo detection is the energy-onset + inter-onset-interval-median estimator that
inspector-rust (`bpm.ts`) and disco-controller (`audio_engine.py`) share - band-limited
kick energy, onsets where a block beats the recent average by a factor AND rises above the
peak of a lagged window (the SuperFlux gate against sustain), IOI median folded into
[60, 200] BPM, shown as a 4 s rolling mean. The spectrum is 28 log-spaced bands from 40 Hz
to 16 kHz with a slow global ceiling, so the bars fill the panel for a whisper and a club
alike while the bands keep their proportions to each other.
"""
import math
from collections import deque
from typing import List, Optional

import numpy as np

SAMPLE_RATE = 44100
BLOCK = 1024                  # samples per block: ~23 ms at 44.1 kHz, ~43 blocks a second

DB_FLOOR = -80.0              # dBFS shown as "silent"; 0 dBFS is full scale. A quiet room on a laptop
                              # microphone measures around -67, so -60 pinned the meter at the floor
LOUD_DB = -45.0               # above this (smoothed) the room counts as music: onsets may fire

# What the level screen SHOWS. dBFS is negative by definition (0 is the converter's ceiling),
# which reads as "something is wrong" to anyone who has seen a phone's sound-level app. The
# shown number is dBFS + SPL_OFFSET, so a quiet room lands near 33 and loud music near 80 -
# the range those apps show. It is an ESTIMATE, not a measurement: without a calibrated
# microphone there is no reference to sound pressure, and every device differs. The offset
# is the one disco-controller uses, so numbers are comparable across the house.
# Nothing but the display is shifted: the analysis, the history and the tempo gate all stay
# in dBFS, because a shifted value would no longer be dBFS at all.
SPL_OFFSET = 100.0


def spl(db):
    """A dBFS value on the positive scale the screens show."""
    return db + SPL_OFFSET

BANDS = 28                    # inspector-rust's analyser count
F_LO, F_HI = 40.0, 16000.0
ATTACK, RELEASE = 0.55, 0.18  # per-block smoothing of a band level (rise fast, fall slow)
RANGE_DB = 60.0               # the dynamic window below the ceiling that the bars span
CEIL_ATTACK, CEIL_DECAY = 0.35, 0.02   # the global ceiling follows a louder band quickly, forgets slowly
CEIL_HEADROOM_DB = 3.0        # the loudest band sits just under the top, not on it
PEAK_HOLD_BLOCKS = 6          # a peak marker holds ~140 ms ...
PEAK_FALL = 0.012             # ... then falls this much per block (about 2 s from top to bottom)

FFT = 4096                    # analysis window: a rolling 4096 samples hopped by one block. 1024 samples
                              # give 43 Hz bins, and below 200 Hz several bands would share one bin.
BASS_LO, BASS_HI = 30.0, 110.0

# --- tempo (mirrors bpm.ts BPM_CONFIG) ---
THRESHOLD = 1.4               # onset: block energy over the moving average by this factor
AVG_WINDOW_S = 3.0
ONSET_REFRACTORY_S = 0.30     # => at most 200 BPM
IOI_WINDOW_S = 6.0
BPM_MIN, BPM_MAX = 60.0, 200.0
MIN_ONSETS = 4
STALE_RESET_S = 4.0
OCTAVE_SNAP = 8.0
DISPLAY_AVG_S = 4.0
SF_LAG, SF_WIN, SF_MARGIN = 4, 4, 1.04
DB_HISTORY_S = 120.0          # what -db's chart may look back on


def dbfs(block) -> float:
    """RMS level of a block in dBFS, clamped to [DB_FLOOR, 0]. A full-scale sine is -3.0."""
    x = np.asarray(block, dtype=np.float32)
    if x.size == 0:
        return DB_FLOOR
    rms = float(np.sqrt(np.mean(x * x))) + 1e-12
    return max(DB_FLOOR, min(0.0, 20.0 * math.log10(rms)))


class Spectrum:
    """Log-spaced band levels in 0..1 with peak-hold markers, from a rolling FFT window."""

    def __init__(self, samplerate=SAMPLE_RATE, blocksize=BLOCK, bands=BANDS, lo=F_LO, hi=F_HI, fft=FFT):
        self.samplerate, self.blocksize, self.bands, self.fft = samplerate, blocksize, bands, fft
        self._buf = np.zeros(fft, dtype=np.float32)
        self._window = np.hanning(fft).astype(np.float32)
        self.freqs = np.fft.rfftfreq(fft, 1.0 / samplerate)
        freqs = self.freqs
        self.magnitude = np.zeros(len(freqs), dtype=np.float64)   # |rfft| of the last window
        hi = min(hi, samplerate / 2)
        self.edges = [float(e) for e in np.logspace(math.log10(lo), math.log10(hi), bands + 1)]
        self.bins: List[np.ndarray] = []
        for k in range(bands):
            idx = np.where((freqs >= self.edges[k]) & (freqs < self.edges[k + 1]))[0]
            if len(idx) == 0:
                centre = (self.edges[k] + self.edges[k + 1]) / 2
                idx = np.array([int(np.argmin(np.abs(freqs - centre)))])
            self.bins.append(idx)
        self._full_scale = fft / 4.0                # |rfft| of a full-scale sine under a Hann window
        self._ceiling = DB_FLOOR + RANGE_DB          # dB; the top of the shown window
        self._levels = np.zeros(bands, dtype=np.float64)
        self._peaks = np.zeros(bands, dtype=np.float64)
        self._hold = np.zeros(bands, dtype=np.int64)
        self.levels: List[float] = [0.0] * bands
        self.peaks: List[float] = [0.0] * bands

    def push(self, block) -> List[float]:
        x = np.asarray(block, dtype=np.float32)
        n = min(x.size, self.fft)
        if n:
            self._buf = np.roll(self._buf, -n)
            self._buf[-n:] = x[-n:]
        mag = np.abs(np.fft.rfft(self._buf * self._window))
        self.magnitude = mag
        band_db = np.array([20.0 * math.log10(float(mag[idx].max()) / self._full_scale + 1e-12)
                            for idx in self.bins])
        loudest = float(band_db.max())
        target = max(DB_FLOOR + RANGE_DB, loudest + CEIL_HEADROOM_DB)
        rate = CEIL_ATTACK if target > self._ceiling else CEIL_DECAY
        self._ceiling += (target - self._ceiling) * rate
        raw = np.clip((band_db - (self._ceiling - RANGE_DB)) / RANGE_DB, 0.0, 1.0)
        rising = raw > self._levels
        self._levels += (raw - self._levels) * np.where(rising, ATTACK, RELEASE)
        # peak hold: a new maximum resets the hold; after the hold the marker falls
        above = self._levels >= self._peaks
        self._peaks = np.where(above, self._levels, self._peaks)
        self._hold = np.where(above, PEAK_HOLD_BLOCKS, np.maximum(self._hold - 1, 0))
        falling = (~above) & (self._hold == 0)
        self._peaks = np.where(falling, np.maximum(self._peaks - PEAK_FALL, self._levels), self._peaks)
        self.levels = [float(v) for v in self._levels]
        self.peaks = [float(v) for v in self._peaks]
        return self.levels


class BpmAnalyzer:
    """Energy-onset + IOI-median tempo estimator. Feed it band-limited energy per block."""

    def __init__(self, threshold=THRESHOLD):
        self.threshold = threshold
        self.reset()

    def reset(self):
        self._onsets = deque()
        self._energy_hist = deque()
        self._ef = deque(maxlen=SF_LAG + SF_WIN)
        self._raw_bpm_hist = deque()
        self._first_t: Optional[float] = None
        self._last_onset = -1e9
        self._last_valid = -1e9
        self.display_bpm = 0.0
        self.confidence = 0.0
        self.onset_strength = 0.0

    def push(self, energy, now, allow=True) -> bool:
        """One block's kick-band energy. True when an onset (a beat) fired."""
        if self._first_t is None:
            self._first_t = now
        self._energy_hist.append((now, energy))
        while self._energy_hist and now - self._energy_hist[0][0] > AVG_WINDOW_S:
            self._energy_hist.popleft()
        ref = max(list(self._ef)[:SF_WIN]) if len(self._ef) >= SF_LAG + SF_WIN else 0.0
        self._ef.append(energy)
        if now - self._first_t < AVG_WINDOW_S:          # baseline calibration
            return False
        avg = sum(e for _, e in self._energy_hist) / len(self._energy_hist)
        if avg <= 1e-9:
            return False
        fired = (allow
                 and energy > avg * self.threshold
                 and energy > ref * SF_MARGIN               # a NEW attack, not sustain
                 and now - self._last_onset >= ONSET_REFRACTORY_S)
        if fired:
            self._last_onset = now
            self._onsets.append(now)
            self.onset_strength += (energy / avg - self.onset_strength) * 0.25
            while self._onsets and now - self._onsets[0] > IOI_WINDOW_S:
                self._onsets.popleft()
        return fired

    def estimate(self, now):
        while self._onsets and now - self._onsets[0] > IOI_WINDOW_S:
            self._onsets.popleft()
        if len(self._onsets) < MIN_ONSETS:
            # Stale = no BEAT for a while once the window has thinned out - counting from the
            # last estimate instead kept a stopped track's tempo on screen for 8 s and more.
            if self.display_bpm > 0 and now - self._last_onset > STALE_RESET_S:
                self.display_bpm = 0.0
                self.confidence = 0.0
                self._raw_bpm_hist.clear()
            return
        onsets = list(self._onsets)
        intervals = [b - a for a, b in zip(onsets, onsets[1:])]
        median = sorted(intervals)[len(intervals) // 2]
        if median <= 0:
            return
        raw = 60.0 / median
        while raw < BPM_MIN:
            raw *= 2
        while raw > BPM_MAX:
            raw /= 2
        if self.display_bpm > 0:                          # octave snap onto the locked tempo
            if abs(raw * 2 - self.display_bpm) < OCTAVE_SNAP:
                raw *= 2
            elif abs(raw / 2 - self.display_bpm) < OCTAVE_SNAP:
                raw /= 2
        if not self._raw_bpm_hist or abs(self._raw_bpm_hist[-1][1] - raw) > 0.01:
            self._raw_bpm_hist.append((now, raw))
        while self._raw_bpm_hist and now - self._raw_bpm_hist[0][0] > DISPLAY_AVG_S:
            self._raw_bpm_hist.popleft()
        if self._raw_bpm_hist:
            self.display_bpm = sum(b for _, b in self._raw_bpm_hist) / len(self._raw_bpm_hist)
        self._last_valid = now
        var = sum((i - median) ** 2 for i in intervals) / len(intervals)
        self.confidence = max(0.0, min(1.0, 1.0 - math.sqrt(var) / median))

    @property
    def bpm(self) -> int:
        return int(round(self.display_bpm))

    def onset_rate(self) -> float:
        if len(self._onsets) < 2:
            return 0.0
        span = self._onsets[-1] - self._onsets[0]
        return (len(self._onsets) - 1) / span if span > 0.5 else 0.0


class Analyzer:
    """One object per session: feed() a block, read levels / peaks / db / bpm / beats."""

    def __init__(self, samplerate=SAMPLE_RATE, blocksize=BLOCK):
        self.samplerate, self.blocksize = samplerate, blocksize
        self.spectrum = Spectrum(samplerate, blocksize)
        self.tempo = BpmAnalyzer()
        freqs = self.spectrum.freqs
        self._bass_bins = np.where((freqs >= BASS_LO) & (freqs < BASS_HI))[0]
        self.db = DB_FLOOR
        self.db_smooth = DB_FLOOR
        self.db_min, self.db_max = 0.0, DB_FLOOR
        self.db_history = deque(maxlen=int(DB_HISTORY_S * samplerate / blocksize) + 1)  # (t, db)
        self.bpm_history = deque(maxlen=self.db_history.maxlen)                           # (t, bpm)
        self.bass = 0.0                    # 0..1 kick-band meter
        self._bass_peak = 1e-9
        self.levels = [0.0] * BANDS
        self.peaks = [0.0] * BANDS
        self.beats = 0
        self.last_beat: Optional[float] = None
        self.blocks = 0

    @property
    def music(self) -> bool:
        return self.db_smooth > LOUD_DB

    @property
    def bpm(self) -> int:
        return self.tempo.bpm

    @property
    def confidence(self) -> float:
        return self.tempo.confidence

    def beat_age(self, now) -> float:
        return math.inf if self.last_beat is None else max(0.0, now - self.last_beat)

    def feed(self, block, now) -> bool:
        """One block of samples at time `now` (seconds). Returns True when a beat fired."""
        x = np.asarray(block, dtype=np.float32)
        self.blocks += 1
        self.db = dbfs(x)
        self.db_smooth += (self.db - self.db_smooth) * 0.25
        if self.blocks == 1:
            self.db_smooth = self.db
        self.db_min, self.db_max = min(self.db_min, self.db), max(self.db_max, self.db)
        self.db_history.append((now, self.db))
        self.levels = self.spectrum.push(x)          # one FFT serves the bands AND the kick band
        self.peaks = self.spectrum.peaks
        mag = self.spectrum.magnitude
        energy = float(mag[self._bass_bins].mean()) if len(self._bass_bins) else 0.0
        self._bass_peak = max(energy, self._bass_peak * 0.9988)
        target = min(1.0, energy / (self._bass_peak * 1.6 + 1e-9))
        self.bass += (target - self.bass) * (0.45 if target > self.bass else 0.16)
        fired = self.tempo.push(energy, now, allow=self.music)
        self.tempo.estimate(now)
        self.bpm_history.append((now, self.tempo.display_bpm))
        if fired:
            self.beats += 1
            self.last_beat = now
        return fired


class DemoAudio:
    """A deterministic stand-in for the microphone: a four-on-the-floor kick at `bpm`,
    a bass line, hi-hats on the off-beats, a soft pad and a little room noise. `read(n)`
    returns the next n samples; `now()` is the synth's own clock."""

    def __init__(self, seed=7, bpm=126.0, samplerate=SAMPLE_RATE, blocksize=BLOCK):
        self.seed, self.bpm, self.samplerate = seed, bpm, samplerate
        self._rng = np.random.default_rng(seed)
        self._pos = 0                                    # samples rendered so far
        self._offsets = [0.0, 0.0]                       # unused hook for phase jitter (kept 0: honest metronome)

    def now(self) -> float:
        return self._pos / self.samplerate

    def read(self, n=BLOCK):
        sr, beat_s = self.samplerate, 60.0 / self.bpm
        t = (self._pos + np.arange(n)) / sr
        since_beat = np.mod(t, beat_s)                                  # seconds into the current beat
        kick = 0.85 * np.exp(-since_beat * 14.0) * np.sin(2 * np.pi * 55.0 * since_beat)
        since_off = np.mod(t + beat_s / 2, beat_s)                     # off-beat hi-hat
        hat = 0.10 * np.exp(-since_off * 55.0) * self._rng.standard_normal(n)
        bar = np.floor(t / (4 * beat_s)) % 2                           # a two-bar bass figure
        bass_hz = np.where(bar == 0, 82.4, 73.4)
        bass = 0.14 * np.sin(2 * np.pi * np.cumsum(np.full(n, 1.0 / sr)) * 0 + 2 * np.pi * bass_hz * t)
        pad = 0.035 * (np.sin(2 * np.pi * 220.0 * t) + np.sin(2 * np.pi * 329.6 * t) * 0.7
                       + np.sin(2 * np.pi * 440.0 * t) * 0.5 + np.sin(2 * np.pi * 1760.0 * t) * 0.2)
        noise = 0.004 * self._rng.standard_normal(n)
        y = np.tanh(kick + hat + bass + pad + noise)                    # soft clip: never past full scale
        self._pos += n
        return y.astype(np.float32)
