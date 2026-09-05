"""Design tokens - the single source of truth for how termstats looks.

Nothing in here touches the operating system or draws anything. It answers three
questions for the renderer: what can this terminal show (Capabilities), which glyphs and
colours to use for it (GlyphSet, Theme, Ramp), and how wide every field is (spacing and
the fixed-width number formats). cli.py imports from here and holds no hex value and no
decorative glyph of its own.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from typing import NamedTuple, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------------
# Capabilities
#
# Colour depth and glyph level are decided ONCE at start-up, from the environment and the
# stream, and never re-probed per frame. Precedence for colour: NO_COLOR wins over
# everything (it is a request from the user, not a hint); then TERM=dumb; then FORCE_COLOR
# / COLORTERM / TERM say how deep. Glyphs: TERMSTATS_GLYPHS overrides, TERM=dumb forces
# ASCII, and a stream that cannot encode the probe forces ASCII too.
# ---------------------------------------------------------------------------------

COLOR_LEVELS = ("truecolor", "256", "16", "mono")
GLYPH_LEVELS = ("braille", "block", "ascii")

# Every non-ASCII character the dashboard can draw with. The stream must encode all of it,
# or the glyph level drops to ASCII. Add to this when you add a glyph.
GLYPH_PROBE = "█░▒╭╰│─▏▎▍▌▋▊▉╌╵⠀⠒⣿▁▂▃▄▅▆▇━·…Σ"


class Capabilities(NamedTuple):
    color: str      # one of COLOR_LEVELS
    glyphs: str     # one of GLYPH_LEVELS
    nerd: bool      # TERMSTATS_NERD_FONT=1 - icon glyphs in panel titles


def stream_can_draw(stream, probe: str = GLYPH_PROBE) -> bool:
    try:
        probe.encode(getattr(stream, "encoding", None) or "")
    except (AttributeError, LookupError, TypeError, UnicodeEncodeError):
        return False
    return True


def _color_from_env(env) -> str:
    if env.get("NO_COLOR", "") != "":
        return "mono"
    term = env.get("TERM", "")
    if term == "dumb":
        return "mono"
    colorterm = env.get("COLORTERM", "").lower()
    if colorterm in ("truecolor", "24bit"):
        return "truecolor"
    if "256" in term:
        return "256"
    if term in ("", "xterm", "screen", "tmux", "linux", "vt100", "ansi"):
        return "16"
    return "256"


def _glyphs_from_env(env, stream) -> str:
    wanted = env.get("TERMSTATS_GLYPHS", "").strip().lower()
    if wanted in GLYPH_LEVELS:
        return wanted
    if env.get("TERM", "") == "dumb":
        return "ascii"
    if not stream_can_draw(stream):
        return "ascii"
    return "braille"


def detect(env=None, stream=None) -> Capabilities:
    env = os.environ if env is None else env
    stream = sys.stdout if stream is None else stream
    return Capabilities(
        color=_color_from_env(env),
        glyphs=_glyphs_from_env(env, stream),
        nerd=env.get("TERMSTATS_NERD_FONT", "") in ("1", "true", "yes"),
    )


# ---------------------------------------------------------------------------------
# Glyph sets
#
# Three levels, degrading whole: braille (charts in 2x4 dots), block (charts in quadrant
# blocks, everything else identical), ascii (nothing outside 7-bit). A level is a complete
# vocabulary - the renderer never mixes two.
# ---------------------------------------------------------------------------------

class GlyphSet(NamedTuple):
    name: str
    bar_full: str
    bar_partials: str      # 1/8 .. 7/8 of a cell, may be empty
    bar_empty: str
    bar_secondary: str
    peak: str              # hairline at the recent maximum of a meter
    spark: str             # eight heights, may be empty (no sparkline at this level)
    sep: str               # joins subtitle parts
    legend_fill: str       # legend glyph for a filled chart series
    legend_line: str       # legend glyph for a line series
    ellipsis: str
    sigma: str             # "total transferred" prefix
    collecting: str        # empty-state baseline character
    chart_marker: Optional[str]   # plotext marker, None = hand-drawn ASCII chart
    chart_full: str        # hand-drawn chart: a full cell
    chart_half: str        # hand-drawn chart: a partially covered cell
    box: str               # rich box style name


GLYPH_SETS = {
    "braille": GlyphSet(
        name="braille", bar_full="█", bar_partials="▏▎▍▌▋▊▉", bar_empty="╌", bar_secondary="▒",
        peak="╵", spark="▁▂▃▄▅▆▇█", sep="·", legend_fill="▇", legend_line="━", ellipsis="…",
        sigma="Σ", collecting="⠒", chart_marker="braille", chart_full="#", chart_half="=",
        box="ROUNDED",
    ),
    "block": GlyphSet(
        name="block", bar_full="█", bar_partials="▏▎▍▌▋▊▉", bar_empty="╌", bar_secondary="▒",
        peak="╵", spark="▁▂▃▄▅▆▇█", sep="·", legend_fill="▇", legend_line="━", ellipsis="…",
        sigma="Σ", collecting="╌", chart_marker="hd", chart_full="#", chart_half="=",
        box="ROUNDED",
    ),
    "ascii": GlyphSet(
        name="ascii", bar_full="#", bar_partials="", bar_empty="-", bar_secondary="=",
        peak="|", spark="", sep="-", legend_fill="#", legend_line="-", ellipsis="~",
        sigma="tot", collecting="-", chart_marker=None, chart_full="#", chart_half="=",
        box="ASCII",
    ),
}

# Nerd-font icons for panel titles, opt-in only (TERMSTATS_NERD_FONT=1). Private-use
# code points render as tofu without the font, so they are never on by default.
NERD_ICONS = {"cpu": "", "memory": "", "network": "", "disk": "",
              "processes": ""}


# ---------------------------------------------------------------------------------
# Spacing - one grid for every panel
# ---------------------------------------------------------------------------------

PANEL_PADDING = (0, 1)     # rich Panel padding (vertical, horizontal)
PANEL_CHROME_W = 4         # 2 border + 2 padding columns a panel spends per side pair
PANEL_CHROME_H = 2         # top and bottom border rows

LABEL_W = 9                # "    cpu0 " - right-aligned label plus one space
VALUE_W = 7                # "  62.5%" - percentage field
RATE_W = 9                 # " 45.2K/s" - fixed-width transfer rate
DISK_LABEL_W = 12          # mountpoints get more room than "cpu0"
MIN_BAR_W = 6              # below this the annotation is dropped, never sliced
NOTE_GB_PAIR_W = 12        # " 6.2G/ 16.0G"
NOTE_MEM_W = 24            # " 6.2G/ 16.0G +5.7G cache"
NOTE_TOTAL_W = 10          # "Σ  1.92G"
SPARK_W = 16               # header sparkline cells
PEAK_WINDOW = 30           # samples the peak marker remembers
SMOOTH_ALPHA = 0.5         # EMA weight of the newest sample for bar positions (live only)

RIGHT_COL_MIN, RIGHT_COL_MAX, RIGHT_COL_SHARE = 36, 52, 0.4
NARROW_BELOW = 92          # single-column layout under this width
CHART_MIN_H, CHART_MAX_H = 13, 12   # spare lines needed to show charts / chart row height
PROC_MIN_H = 5             # a process panel below this is a stump

AXIS_W_PCT = 3             # "100"
AXIS_W_RATE = 5            # "99999" - fixed so the plot never shifts when the top changes


# ---------------------------------------------------------------------------------
# Fixed-width number formats
#
# Every number that can change between frames renders in a field whose width does NOT
# depend on the value. "9.8G" and "10.2G" occupy the same cells; so do "0 B/s" and
# "999.9 MB/s". Width jitter is the single biggest enemy of a live dashboard: the eye
# tracks the movement instead of the value.
# ---------------------------------------------------------------------------------

def fmt_pct(x: float) -> str:
    """'  5.0%' .. '100.0%' - six cells always."""
    x = 0.0 if x != x else max(0.0, min(999.9, x))
    return f"{x:5.1f}%"


def fmt_gb(n: float) -> str:
    """' 6.2G' .. '999.9G' - five cells, then 'T' above a terabyte."""
    gb = n / 1024 ** 3
    if gb >= 1000:
        return f"{gb / 1024:4.1f}T"
    return f"{gb:5.1f}G"


def fmt_gb_pair(used: float, total: float) -> str:
    """' 6.2G/ 16.0G' - NOTE_GB_PAIR_W cells."""
    return f"{fmt_gb(used)}/{fmt_gb(total)}"


def fmt_rate(bytes_per_s: float) -> str:
    """'  0.0B/s' .. '999.9G/s' - RATE_W - 1 cells, single-letter unit."""
    v = 0.0 if bytes_per_s != bytes_per_s else max(0.0, bytes_per_s)
    for unit in ("B", "K", "M", "G"):
        if v < 999.95 or unit == "G":
            return f"{v:5.1f}{unit}/s"
        v /= 1024.0
    return f"{v:5.1f}G/s"


def fmt_mem(b: float) -> str:
    """' 482M' / ' 1.2G' - five cells."""
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:4.1f}G"
    return f"{b / 1024 ** 2:4.0f}M"


def fmt_load(x: float) -> str:
    """'  9.92' .. '999.99' - six cells."""
    return f"{min(x, 999.99):6.2f}"


def fmt_uptime(seconds: float) -> str:
    """'  3d 18h' / '  0d 07h' - eight cells; days never vanish, so the width never moves."""
    days, rest = int(seconds // 86400), seconds % 86400
    return f"{min(days, 999):3d}d {int(rest // 3600):02d}h"


def fmt_count(n: int, width: int = 5) -> str:
    return f"{min(n, 10 ** width - 1):{width}d}"


def fmt_axis(v: float, width: int) -> str:
    """Axis tick label right-aligned in a fixed field, so the plot never shifts."""
    return f"{v:{width}.0f}"


# ---------------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------------

RGB = Tuple[int, int, int]


def hex_of(rgb: Sequence[int]) -> str:
    return "#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def rgb_of(hex_colour: str) -> RGB:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


class Theme(NamedTuple):
    name: str
    stops: Tuple[Tuple[float, RGB], ...]   # the ramp: idle -> working -> saturated
    text: str          # primary readable values
    soft: str          # secondary values (rss column)
    dim: str           # labels
    muted: str         # annotations, subtitles, table headers
    faint: str         # version tag
    track: str         # the empty part of a meter - its own tone, not the background
    panels: Tuple[Tuple[str, str], ...]   # (panel name, border colour)
    wordmark_bg: str
    wordmark_fg: str
    bg: str            # the terminal background the theme is designed for (contrast tests)

    def panel(self, name: str) -> str:
        return dict(self.panels).get(name, self.muted)


# The 0.3.0 look, moved here verbatim: the greys are rich's grey30/42/54/62/70 as hex, so
# nothing on screen changed when the tokens left cli.py.
THEMES = {
    "default": Theme(
        name="default",
        stops=((0.00, (0x5A, 0xD8, 0xC8)), (0.55, (0xF0, 0xBE, 0x5A)), (1.00, (0xF0, 0x6E, 0x78))),
        text="#b2b2b2", soft="#9e9e9e", dim="#8a8a8a", muted="#6c6c6c", faint="#4e4e4e",
        track="#4e4e4e",
        panels=(("cpu", "#4a6fa5"), ("memory", "#4a9575"), ("network", "#4a6fa5"),
                ("disk", "#a5904a"), ("processes", "#7a5a95")),
        wordmark_bg="#2d6cdf", wordmark_fg="#ffffff", bg="#1a1b26",
    ),
}

DEFAULT_THEME = "default"


def _lerp_rgb(a: RGB, b: RGB, k: float) -> RGB:
    return tuple(round(a[i] + (b[i] - a[i]) * k) for i in range(3))  # type: ignore[return-value]


class Ramp:
    """The shared colour ramp of a theme.

    ramp.rgb(t) / ramp.hex(t) for t in 0..1. Positions are rounded to a millionth and
    cached: bar cells ask for the same i/width positions every frame, so a frame with a
    few hundred cells costs a few hundred dict lookups, not interpolations. A coarser key
    (a thousandth was tried) shifts single channels by one - invisible, but not identical.
    """

    def __init__(self, stops: Sequence[Tuple[float, RGB]]):
        self.stops = tuple((float(p), tuple(int(c) for c in rgb)) for p, rgb in stops)
        self._rgb = lru_cache(maxsize=2048)(self._compute)

    def _compute(self, key: int) -> RGB:
        t = key / 1_000_000.0
        for pos, rgb in self.stops:
            if abs(t - pos) < 1e-9:
                return rgb                       # a stop is returned exactly, never re-derived
        for (lo, c_lo), (hi, c_hi) in zip(self.stops, self.stops[1:]):
            if t <= hi:
                k = 0.0 if hi == lo else (t - lo) / (hi - lo)
                return _lerp_rgb(c_lo, c_hi, k)
        return self.stops[-1][1]

    def rgb(self, t: float) -> RGB:
        t = 0.0 if t != t else max(0.0, min(1.0, t))      # t != t catches NaN
        return self._rgb(int(round(t * 1_000_000)))

    def hex(self, t: float) -> str:
        return hex_of(self.rgb(t))


def dim_hex(rgb: Sequence[int], factor: float = 0.45) -> str:
    """The same colour at a fraction of its brightness - for a secondary segment that must
    read as related to the primary but clearly not the same thing."""
    return hex_of(tuple(round(c * factor) for c in rgb))
