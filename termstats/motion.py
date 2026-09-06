"""What moves between two frames of a microphone screen.

The analyzer (audio.py) produces one set of numbers per audio block, about 43 times a second.
The screen draws about 30 times a second. Reading the latest numbers on every frame gives a
picture that steps rather than moves: a bar that jumps to its new height, a peak marker that
falls in a straight line, a beat that is an on/off dot. This layer sits between the two and
makes every quantity a function of ELAPSED TIME:

- bars ease towards their target with a fast attack and a slow release,
- a "trail" is left where a bar was, falling away under gravity,
- peak markers hold, then fall with gravity - not at a constant rate,
- a beat is an impulse that decays exponentially, and everything that "reacts to the beat"
  reads that envelope instead of a boolean,
- the level meter has VU-like ballistics,
- the beat phase sweeps from 0 to 1 between two beats, for a metronome.

Time constants are seconds, so the look is the same at 20 and at 30 frames a second, and a
long pause (a suspended laptop) is clamped to one short step rather than zeroing everything.
Nothing here is used in a snapshot: `--once` draws the analyzer's numbers as they are.
"""
import math
from typing import List, Optional

DEFAULT_DB_FLOOR = -80.0   # audio.DB_FLOOR; passed in by cli so this module needs no numpy

MAX_DT = 0.25           # a frame gap longer than this counts as this: no jumps after a pause
BEAT_TAU = 0.25         # the beat envelope falls to 1/e in this many seconds
ATTACK_TAU = 0.02       # bars: time to cover ~63 % of a rise ...
RELEASE_TAU = 0.12      # ... and of a fall - the fall is what the eye follows
PUNCH = 0.12            # bars grow by up to this fraction on a beat (a nudge, not a jump)
TRAIL_G = 4.0           # trails fall under this gravity, in bar heights per second squared
PEAK_HOLD_S = 0.4       # a peak marker stays put this long ...
PEAK_G = 1.6            # ... then falls under this gravity
METER_ATTACK_TAU = 0.03 # the level meter: fast up ...
METER_RELEASE_TAU = 0.35  # ... slow down, like a VU needle
ENV_FLOOR = 0.01        # below this the beat envelope is simply over - an e^-8 tail is not a colour


def _ease(current, target, dt, tau):
    """Move `current` towards `target` as an exponential with time constant `tau`."""
    if tau <= 0:
        return target
    return current + (target - current) * (1.0 - math.exp(-dt / tau))


class Motion:
    def __init__(self, db_floor=DEFAULT_DB_FLOOR):
        self.db_floor = db_floor
        self.reset()

    def reset(self):
        self._t: Optional[float] = None
        self._beats_seen = 0
        self.beat_env = 0.0
        self.levels: List[float] = []
        self.trails: List[float] = []
        self._trail_v: List[float] = []
        self.peaks: List[float] = []
        self._peak_v: List[float] = []
        self._peak_hold: List[float] = []
        self.meter = 0.0
        self.phase: Optional[float] = None

    def update(self, an, now):
        """Advance to `now` using the analyzer's current numbers."""
        dt = 0.0 if self._t is None else max(0.0, min(now - self._t, MAX_DT))
        self._t = now
        targets = list(an.levels)
        if len(self.levels) != len(targets):
            self.levels = list(targets)
            self.trails = list(targets)
            self._trail_v = [0.0] * len(targets)
            self.peaks = list(targets)
            self._peak_v = [0.0] * len(targets)
            self._peak_hold = [PEAK_HOLD_S] * len(targets)

        # the beat: an impulse on every new onset, then an exponential fade
        beats = getattr(an, "beats", 0)
        if beats > self._beats_seen:
            self.beat_env = 1.0
            self._beats_seen = beats
        else:
            self.beat_env *= math.exp(-dt / BEAT_TAU)
            if self.beat_env < ENV_FLOOR:
                self.beat_env = 0.0

        for i, target in enumerate(targets):
            cur = self.levels[i]
            tau = ATTACK_TAU if target > cur else RELEASE_TAU
            level = _ease(cur, target, dt, tau)
            self.levels[i] = level

            # trail: rides up with the bar, falls away under gravity
            if level >= self.trails[i]:
                self.trails[i], self._trail_v[i] = level, 0.0
            else:
                self._trail_v[i] += TRAIL_G * dt
                self.trails[i] = max(level, self.trails[i] - self._trail_v[i] * dt)

            # peak marker: rides up, holds, then falls under gravity
            if level >= self.peaks[i]:
                self.peaks[i], self._peak_v[i], self._peak_hold[i] = level, 0.0, PEAK_HOLD_S
            elif self._peak_hold[i] > 0:
                self._peak_hold[i] -= dt
            else:
                self._peak_v[i] += PEAK_G * dt
                self.peaks[i] = max(level, self.peaks[i] - self._peak_v[i] * dt)

        # the level meter, in percent of the shown scale, with VU ballistics
        floor = self.db_floor
        db = getattr(an, "db", floor)
        target = max(0.0, min(100.0, (db - floor) / -floor * 100.0))
        tau = METER_ATTACK_TAU if target > self.meter else METER_RELEASE_TAU
        self.meter = _ease(self.meter, target, dt, tau)

        # where we are between two beats
        bpm, last_beat = getattr(an, "bpm", 0), getattr(an, "last_beat", None)
        if bpm and last_beat is not None:
            self.phase = ((now - last_beat) / (60.0 / bpm)) % 1.0
        else:
            self.phase = None

    def shown_levels(self) -> List[float]:
        """The bar heights to draw: the eased levels, nudged up by the beat."""
        gain = 1.0 + PUNCH * self.beat_env
        return [min(1.0, v * gain) for v in self.levels]
