"""Design tokens - the single source of truth for how termstats looks.

Nothing in here touches the operating system or draws anything. It answers three
questions for the renderer: what can this terminal show (Capabilities), which glyphs and
colours to use for it (GlyphSet, Theme, Ramp), and how wide every field is (spacing and
the fixed-width number formats). cli.py imports from here and holds no hex value and no
decorative glyph of its own.
"""

from __future__ import annotations

import math
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
GLYPH_PROBE = "█░▒╭╰│─▏▎▍▌▋▊▉╌╵⠀⠒⣿▁▂▃▄▅▆▇━·…Σ©"


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
    rule: str              # --no-border: the title rule
    copyright: str         # the footer's mark


GLYPH_SETS = {
    "braille": GlyphSet(
        name="braille", bar_full="█", bar_partials="▏▎▍▌▋▊▉", bar_empty="╌", bar_secondary="▒",
        peak="╵", spark="▁▂▃▄▅▆▇█", sep="·", legend_fill="▇", legend_line="━", ellipsis="…",
        sigma="Σ", collecting="⠒", chart_marker="braille", chart_full="#", chart_half="=",
        box="ROUNDED", rule="─", copyright="©",
    ),
    "block": GlyphSet(
        name="block", bar_full="█", bar_partials="▏▎▍▌▋▊▉", bar_empty="╌", bar_secondary="▒",
        peak="╵", spark="▁▂▃▄▅▆▇█", sep="·", legend_fill="▇", legend_line="━", ellipsis="…",
        sigma="Σ", collecting="╌", chart_marker="hd", chart_full="#", chart_half="=",
        box="ROUNDED", rule="─", copyright="©",
    ),
    "ascii": GlyphSet(
        name="ascii", bar_full="#", bar_partials="", bar_empty="-", bar_secondary="=",
        peak="|", spark="", sep="-", legend_fill="#", legend_line="-", ellipsis="~",
        sigma="tot", collecting="-", chart_marker=None, chart_full="#", chart_half="=",
        box="ASCII", rule="-", copyright="(c)",
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
COMPACT_PADDING = (0, 0)   # --compact: the border is the only chrome
COMPACT_CHROME_W = 2
RULE_CHROME_H = 1          # --no-border: a title rule above the body, nothing below
RULE_CHROME_W = 2          # ... but one gutter column per side, or the columns run together

LABEL_W = 9                # "    cpu0 " - right-aligned label plus one space
VALUE_W = 7                # "  62.5%" - percentage field
RATE_W = 9                 # " 45.2K/s" - fixed-width transfer rate
DISK_LABEL_W = 12          # mountpoints get more room than "cpu0"
MIN_BAR_W = 6              # below this the annotation is dropped, never sliced
NOTE_GB_PAIR_W = 13        # "  6.2G/ 16.0G" - two six-cell fields and a slash
NOTE_MEM_W = 26            # "  6.2G/ 16.0G + 5.7G cache"
NOTE_TOTAL_W = 10          # "tot   1.9G" - the ASCII sigma is three letters wide
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
    """'  6.2G' .. '999.9G' - six cells, then 'T' above a terabyte, still six."""
    gb = 0.0 if n != n else max(0.0, n / 1024 ** 3)
    if gb >= 1000:
        return f"{min(gb / 1024, 999.9):5.1f}T"
    return f"{gb:5.1f}G"


def fmt_gb_pair(used: float, total: float) -> str:
    """'  6.2G/ 16.0G' - NOTE_GB_PAIR_W cells."""
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


def fmt_axis(v: float, width: int, top: Optional[float] = None) -> str:
    """Axis tick label right-aligned in a fixed field, so the plot never shifts.

    Small axes (top below 10) keep one decimal - a 1.5 GB/s tick printed as "2" would
    be wrong, and "  1.5" is exactly as wide as "    2".
    """
    if (top if top is not None else v) < 10:
        return f"{v:{width}.1f}"
    return f"{min(v, 10 ** width - 1):{width}.0f}"


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
    border: str        # ONE frame colour for every panel - quiet, so the content leads
    accent: str        # panel titles
    wordmark_bg: str
    wordmark_fg: str
    bg: str            # the terminal background the theme is designed for (contrast tests)
    bands16: Tuple[str, ...]   # the ramp on a 16-colour terminal, as rich colour names


def _stops(*hexes_at):
    return tuple((pos, rgb_of(h)) for pos, h in hexes_at)


# Every ramp runs idle -> cool -> warm -> hot with OKLab lightness that never decreases,
# so it still reads as a scale in greyscale. The idle stop is deliberately desaturated:
# an idle machine should look calm, not cold, so that load stands out when it comes.
# Red at usable chroma is darker than yellow in sRGB, which is why cool and warm sit a
# little lower than the vendor palettes' own picks - the alternative is a pink "hot".
THEMES = {
    "default": Theme(
        name="default",
        stops=_stops((0.00, "#5f7f80"), (0.30, "#3aa898"), (0.60, "#c0922c"), (1.00, "#ff7b78")),
        text="#b2b2b2", soft="#9e9e9e", dim="#8a8a8a", muted="#6c6c6c", faint="#4e4e4e",
        track="#4e4e4e",
        border="#4b5160", accent="#7aa2f7",
        wordmark_bg="#2d6cdf", wordmark_fg="#ffffff", bg="#1a1b26",
        bands16=("cyan", "bright_cyan", "yellow", "bright_red"),
    ),
    "mono": Theme(
        name="mono",
        stops=_stops((0.00, "#5c5c5c"), (0.35, "#8a8a8a"), (0.70, "#b8b8b8"), (1.00, "#e6e6e6")),
        text="#c8c8c8", soft="#a8a8a8", dim="#8a8a8a", muted="#6c6c6c", faint="#4e4e4e",
        track="#3c3c3c",
        border="#4e4e4e", accent="#dcdcdc",
        wordmark_bg="#dcdcdc", wordmark_fg="#101010", bg="#121212",
        bands16=("bright_black", "white", "bright_white"),
    ),
    "nord": Theme(
        name="nord",
        stops=_stops((0.00, "#616e88"), (0.30, "#5f8f8f"), (0.60, "#b0925f"), (1.00, "#d08770")),
        text="#d8dee9", soft="#b8c0cc", dim="#8a94a6", muted="#616e88", faint="#4c566a",
        track="#3b4252",
        border="#4c566a", accent="#88c0d0",
        wordmark_bg="#5e81ac", wordmark_fg="#eceff4", bg="#2e3440",
        bands16=("cyan", "yellow", "red"),
    ),
    "gruvbox": Theme(
        name="gruvbox",
        stops=_stops((0.00, "#665c54"), (0.30, "#5f8f60"), (0.60, "#b08420"), (1.00, "#fb4934")),
        text="#ebdbb2", soft="#bdae93", dim="#a89984", muted="#7c6f64", faint="#504945",
        track="#3c3836",
        border="#504945", accent="#83a598",
        wordmark_bg="#d79921", wordmark_fg="#1d2021", bg="#282828",
        bands16=("green", "yellow", "bright_red"),
    ),
    "catppuccin-mocha": Theme(
        name="catppuccin-mocha",
        stops=_stops((0.00, "#585b70"), (0.30, "#5fa89c"), (0.60, "#bfa878"), (1.00, "#f38ba8")),
        text="#cdd6f4", soft="#a6adc8", dim="#9399b2", muted="#6c7086", faint="#585b70",
        track="#313244",
        border="#45475a", accent="#89b4fa",
        wordmark_bg="#89b4fa", wordmark_fg="#1e1e2e", bg="#1e1e2e",
        bands16=("cyan", "yellow", "bright_magenta"),
    ),
    # Colour-blind safe: the viridis ramp is lightness-monotone by construction and keeps
    # its order under both deuteranopia and protanopia.
    "viridis": Theme(
        name="viridis",
        stops=_stops((0.00, "#414487"), (0.25, "#3b528b"), (0.50, "#21918c"),
                     (0.75, "#5ec962"), (1.00, "#fde725")),
        text="#c8c8c8", soft="#a8a8a8", dim="#8a8a8a", muted="#6c6c6c", faint="#4e4e4e",
        track="#3c3c3c",
        border="#4e4e4e", accent="#5ec962",
        wordmark_bg="#fde725", wordmark_fg="#1a1a1a", bg="#161616",
        bands16=("magenta", "blue", "cyan", "green", "bright_yellow"),
    ),
}

THEME_ENV = "TERMSTATS_THEME"


def theme_names() -> Tuple[str, ...]:
    return tuple(THEMES)


def resolve_theme(name: Optional[str]) -> Theme:
    """Theme by name; None or "" means the default. Unknown names raise KeyError."""
    return THEMES[name or DEFAULT_THEME]


def quantised(ramp: "Ramp", system: str, samples: int = 32) -> Tuple[int, ...]:
    """The ramp as rich would render it on a 256- or 16-colour terminal.

    Returns the palette index rich picks for each of `samples` positions - the thing to
    check is not the truecolor design but what survives quantisation.
    """
    from rich.color import Color, ColorSystem
    target = {"256": ColorSystem.EIGHT_BIT, "16": ColorSystem.STANDARD}[system]
    out = []
    for i in range(samples):
        colour = Color.parse(ramp.hex(i / (samples - 1))).downgrade(target)
        out.append(colour.number if colour.number is not None else -1)
    return tuple(out)


class BandedRamp:
    """The ramp for a terminal that cannot show truecolor - monotone by construction.

    On 256 colours rich's nearest-colour choice can leave a palette index and come back
    to it a few cells later, and at that point the meter is no longer a scale. Here the
    ramp is sampled once, quantised, and any colour that would reappear is replaced by
    the band before it. On 16 colours nearest-colour is hopeless (a teal->amber->red ramp
    collapses to two of them), so each theme names its bands outright.
    """

    def __init__(self, ramp: "Ramp", system: str, bands16: Sequence[str] = ()):
        self.system = system
        if system == "16":
            self._names = tuple(bands16) or ("cyan", "yellow", "red")
        else:
            from rich.color import Color, ColorSystem
            names, seen, last = [], set(), None
            for i in range(32):
                colour = Color.parse(ramp.hex(i / 31)).downgrade(ColorSystem.EIGHT_BIT)
                name = f"color({colour.number})"
                if name != last and name in seen:
                    name = last                      # never come back to an earlier band
                names.append(name)
                seen.add(name)
                last = name
            self._names = tuple(names)

    def name(self, t: float) -> str:
        t = 0.0 if t != t else max(0.0, min(1.0, t))
        return self._names[min(int(t * len(self._names)), len(self._names) - 1)]

    @property
    def band_count(self) -> int:
        return bands(self._names)


def bands(sequence: Sequence) -> int:
    """How many distinct runs a quantised ramp has - and 0 if a colour ever comes back."""
    runs, seen = [], set()
    for value in sequence:
        if not runs or runs[-1] != value:
            if value in seen:
                return 0          # A B A: the quantised ramp is no longer a scale
            runs.append(value)
            seen.add(value)
    return len(runs)

DEFAULT_THEME = "default"


# --- OKLab ---------------------------------------------------------------------------
#
# Björn Ottosson's perceptual space, ~30 lines and no dependency. Interpolating in sRGB
# passes through a grey-brown trough between teal and amber (both channels fall before
# the other rises) and the ramp reads dirty; in OKLab the path keeps its chroma. L is a
# genuine lightness, so "monotone L" means the ramp still reads as a scale in greyscale
# and for colour-blind readers.

Lab = Tuple[float, float, float]


def _srgb_to_linear(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return 255.0 * (12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055)


def _cbrt(x: float) -> float:
    return math.copysign(abs(x) ** (1.0 / 3.0), x)


def rgb_to_oklab(rgb: Sequence[int]) -> Lab:
    r, g, b = (_srgb_to_linear(c) for c in rgb)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s_ = _cbrt(l), _cbrt(m), _cbrt(s_)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s_,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s_,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s_)


def _oklab_to_linear(lab: Lab) -> Tuple[float, float, float]:
    L, a, b = lab
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (+4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s_,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s_,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s_)


def oklab_to_rgb(lab: Lab) -> RGB:
    return tuple(int(round(_linear_to_srgb(c))) for c in _oklab_to_linear(lab))  # type: ignore[return-value]


def _in_gamut(lab: Lab) -> bool:
    return all(-1e-6 <= c <= 1.0 + 1e-6 for c in _oklab_to_linear(lab))


def oklab_to_rgb_in_gamut(lab: Lab) -> RGB:
    """Map into sRGB by shrinking chroma, never by clipping channels.

    Clipping shifts the hue (a red pushed past the gamut comes back orange); pulling the
    chroma toward grey at the same L keeps hue and lightness, which is what a lightness
    repair needs.
    """
    if _in_gamut(lab):
        return oklab_to_rgb(lab)
    L, a, b = lab
    lo, hi = 0.0, 1.0
    for _ in range(24):
        mid = (lo + hi) / 2
        if _in_gamut((L, a * mid, b * mid)):
            lo = mid
        else:
            hi = mid
    return oklab_to_rgb((L, a * lo, b * lo))


def lightness(rgb: Sequence[int]) -> float:
    return rgb_to_oklab(rgb)[0]


def mix_rgb(a: Sequence[int], b: Sequence[int], k: float) -> RGB:
    """a blended toward b by k (0 = a, 1 = b), in OKLab so the midpoint stays clean."""
    k = max(0.0, min(1.0, k))
    la, lb = rgb_to_oklab(a), rgb_to_oklab(b)
    return oklab_to_rgb_in_gamut(tuple(la[i] + (lb[i] - la[i]) * k for i in range(3)))  # type: ignore[arg-type]


CHART_FADE_TOP = 0.55      # how far the top row of a filled area blends toward the background
CHART_FRAME = False        # the panel border is the frame; a second box inside it is clutter


def monotone_stops(stops: Sequence[Tuple[float, RGB]]) -> Tuple[Tuple[float, RGB], ...]:
    """Enforce non-decreasing OKLab lightness along the ramp.

    A stop darker than its predecessor is lifted to the predecessor's L (hue and chroma
    kept, then gamut-mapped). Vendor palettes need this at the hot end: red at usable
    chroma is darker than yellow in sRGB, so a naive teal -> amber -> red ramp dips at the
    very point that should stand out most.
    """
    out = []
    floor = -1.0
    for pos, rgb in stops:
        L, a, b = rgb_to_oklab(rgb)
        if L < floor:
            rgb = oklab_to_rgb_in_gamut((floor, a, b))
            L = floor
        floor = max(floor, L)
        out.append((float(pos), tuple(int(c) for c in rgb)))
    return tuple(out)


class Ramp:
    """The shared colour ramp of a theme, interpolated in OKLab.

    ramp.rgb(t) / ramp.hex(t) for t in 0..1. The stops are made lightness-monotone at
    construction (see monotone_stops) and returned exactly when hit. Positions are rounded
    to a millionth and cached: bar cells ask for the same i/width positions every frame, so
    a frame with a few hundred cells costs a few hundred dict lookups, not interpolations.
    """

    def __init__(self, stops: Sequence[Tuple[float, RGB]]):
        self.designed = tuple((float(p), tuple(int(c) for c in rgb)) for p, rgb in stops)
        self.stops = monotone_stops(self.designed)
        self._labs = tuple((pos, rgb_to_oklab(rgb)) for pos, rgb in self.stops)
        self._rgb = lru_cache(maxsize=4096)(self._compute)

    @property
    def repaired(self) -> bool:
        """True when monotone_stops had to lift a designed stop."""
        return self.stops != self.designed

    def _compute(self, key: int) -> RGB:
        t = key / 1_000_000.0
        for pos, rgb in self.stops:
            if abs(t - pos) < 1e-9:
                return rgb                       # a stop is returned exactly, never re-derived
        for (lo, lab_lo), (hi, lab_hi) in zip(self._labs, self._labs[1:]):
            if t <= hi:
                k = 0.0 if hi == lo else (t - lo) / (hi - lo)
                lab = tuple(lab_lo[i] + (lab_hi[i] - lab_lo[i]) * k for i in range(3))
                return oklab_to_rgb_in_gamut(lab)  # type: ignore[arg-type]
        return self.stops[-1][1]

    def lightness(self, t: float) -> float:
        return lightness(self.rgb(t))

    def rgb(self, t: float) -> RGB:
        t = 0.0 if t != t else max(0.0, min(1.0, t))      # t != t catches NaN
        return self._rgb(int(round(t * 1_000_000)))

    def hex(self, t: float) -> str:
        return hex_of(self.rgb(t))


DIM_FACTOR = 0.55          # secondary segments keep this share of the primary's OKLab L


def dim_hex(rgb: Sequence[int], factor: float = DIM_FACTOR) -> str:
    """The same HUE at a fraction of the lightness - for a secondary segment that must
    read as related to the primary but clearly not the same thing.

    Done in OKLab: scaling sRGB channels darkens, but also drifts the hue (a dimmed amber
    turns olive). Lowering L and keeping a/b keeps the colour the eye pairs with the bar.
    """
    L, a, b = rgb_to_oklab(rgb)
    return hex_of(oklab_to_rgb_in_gamut((L * factor, a, b)))


# --- contrast -------------------------------------------------------------------------------

def _rel_luminance(rgb):
    """WCAG 2.x relative luminance of an sRGB triplet (0-255)."""
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg):
    """WCAG contrast ratio between two colours (hex strings or RGB triplets), 1..21.
    The Definition of Done asks every theme's text to clear a minimum against the
    background it was designed for; this is the number that decides it."""
    a = _rel_luminance(rgb_of(fg) if isinstance(fg, str) else fg)
    b = _rel_luminance(rgb_of(bg) if isinstance(bg, str) else bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)
