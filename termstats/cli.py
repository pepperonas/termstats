#!/usr/bin/env python3
"""
termstats - Beautiful terminal server dashboard with real-time charts.

Cross-platform system monitoring: CPU, RAM, Swap, Disk, Network,
Top Processes, and live history graphs - all in your terminal.
"""

import math
import os
import platform
import signal
import sys
import threading
import time
import shutil
import psutil
import plotext as plt
from collections import deque
from rich import box
from rich.console import Console, Group
from rich.layout import Layout
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.rule import Rule
from rich.style import Style

from termstats import __version__
from termstats import demo
from termstats import theme as T

IS_LINUX = platform.system() == "Linux"
IS_MACOS = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"

# HISTORY_LEN is a SAMPLE count, not a duration. What it covers in wall-clock time is
# HISTORY_LEN * the refresh interval - see _window_label(), which is why the chart titles
# are computed instead of hard-coded.
HISTORY_LEN = 60
DEFAULT_INTERVAL = 0.5
SNAPSHOT_SAMPLE_S = 1.0

# The interval the samples currently in the history deques were taken at. Set by run_live()
# and run_once(); read by _window_label() so a chart never mislabels its own x-axis.
sample_interval = DEFAULT_INTERVAL

console = Console()

cpu_history = deque(maxlen=HISTORY_LEN)
steal_history = deque(maxlen=HISTORY_LEN)
net_sent_history = deque(maxlen=HISTORY_LEN)
net_recv_history = deque(maxlen=HISTORY_LEN)

_steal_last_total = None
_steal_last_steal = None

# plotext 6.0.0 (released 2026-08-23, still labelled beta upstream) replaced the whole 5.x
# top-level function API - no clear_figure/plot/ylim/plotsize/build. pyproject pins <6; this
# guard means a stray 6.x in the environment costs the charts, not the whole dashboard.
_PLOTEXT_5 = all(hasattr(plt, name) for name in ("clear_figure", "plot", "ylim", "plotsize", "build"))
_CHART_NEEDS_PLOTEXT_5 = "  Charts need plotext 5.x  (pip install 'plotext<6')"


# ---------------------------------------------------------------------------------
# Terminal capabilities
#
# Everything below degrades on its own. Colour depth is rich's problem - it quantises a
# truecolor hex down to 256 or 16 by itself - but the *glyphs* are ours: a stream that
# cannot encode a block character must not be handed one.
# ---------------------------------------------------------------------------------

_GLYPH_PROBE = T.GLYPH_PROBE
_stream_can_draw = T.stream_can_draw

CAPS = T.Capabilities(color="truecolor", glyphs="braille", nerd=False)
GLYPHS = T.GLYPH_SETS["braille"]
UNICODE = True     # legacy alias: GLYPHS.name != "ascii"

# The active glyph vocabulary, as module globals so the formatters read one name each.
BAR_FULL = BAR_PARTIALS = BAR_EMPTY = BAR_SECONDARY = SPARK = ""
ASCII_FULL, ASCII_EMPTY, ASCII_SECONDARY = (T.GLYPH_SETS["ascii"].bar_full,
                                            T.GLYPH_SETS["ascii"].bar_empty,
                                            T.GLYPH_SETS["ascii"].bar_secondary)


def set_glyph_level(level):
    """Switch the whole drawing vocabulary at once - never mix two levels."""
    global GLYPHS, UNICODE, BAR_FULL, BAR_PARTIALS, BAR_EMPTY, BAR_SECONDARY, SPARK
    GLYPHS = T.GLYPH_SETS[level]
    UNICODE = level != "ascii"
    BAR_FULL, BAR_PARTIALS = GLYPHS.bar_full, GLYPHS.bar_partials
    BAR_EMPTY, BAR_SECONDARY, SPARK = GLYPHS.bar_empty, GLYPHS.bar_secondary, GLYPHS.spark


set_glyph_level("braille")


def detect_capabilities(env=None, stream=None):
    """Decide once what this terminal can show, and configure the renderer for it.

    Returns the legacy boolean (True when the drawing glyphs are safe on stdout) that
    the encoding tests read; the full answer lives in CAPS.
    """
    global CAPS
    CAPS = T.detect(env, stream)
    set_glyph_level(CAPS.glyphs)
    return UNICODE


# ---------------------------------------------------------------------------------
# One colour ramp for everything
#
# btop's design rule, and the reason it reads as one instrument rather than a pile of
# widgets: every meter, graph and value maps onto the SAME three stops. Cool and idle at
# the bottom, warm in the middle, hot and saturated at the top.
# ---------------------------------------------------------------------------------

THEME = T.THEMES[T.DEFAULT_THEME]
RAMP_OBJ = T.Ramp(THEME.stops)
RAMP = RAMP_OBJ.stops
MUTED = DIM = FAINT = SOFT = TEXT = TRACK = ""
NET_RX_RGB = NET_TX_RGB = (0, 0, 0)


BANDED = None      # BandedRamp on 256-/16-colour terminals, None on truecolor and mono


def set_theme(name, color=None):
    """Activate a theme: the ramp, every text tone, and the two network series colours.

    `color` is the terminal's colour depth (CAPS.color when omitted). Below truecolor the
    ramp is served from a banded palette that is monotone by construction - see
    theme.BandedRamp for why nearest-colour quantisation is not enough.
    """
    global THEME, RAMP_OBJ, RAMP, BANDED, MUTED, DIM, FAINT, SOFT, TEXT, TRACK
    global NET_RX_RGB, NET_TX_RGB
    THEME = T.resolve_theme(name)
    RAMP_OBJ = T.Ramp(THEME.stops)
    RAMP = RAMP_OBJ.stops
    color = CAPS.color if color is None else color
    BANDED = T.BandedRamp(RAMP_OBJ, color, THEME.bands16) if color in ("256", "16") else None
    MUTED, DIM, FAINT = THEME.muted, THEME.dim, THEME.faint
    SOFT, TEXT, TRACK = THEME.soft, THEME.text, THEME.track
    NET_RX_RGB = RAMP_OBJ.rgb(0.0)      # the filled series
    NET_TX_RGB = RAMP_OBJ.rgb(0.55)     # the line drawn over it


set_theme(T.DEFAULT_THEME, color="truecolor")


def ramp_rgb(t):
    """Colour at position t (0..1) on the shared ramp, as an (r, g, b) tuple."""
    return RAMP_OBJ.rgb(t)


def ramp(t):
    """Colour at position t (0..1) on the shared ramp, as a rich style string.

    Truecolor hex on terminals that can show it; a banded palette name on 256- and
    16-colour terminals, where letting rich pick the nearest colour per cell breaks the
    monotone scale.
    """
    if BANDED is not None:
        return BANDED.name(t)
    return RAMP_OBJ.hex(t)


dim_rgb = T.dim_hex


# ---------------------------------------------------------------------------------
# Meters
# ---------------------------------------------------------------------------------

def bar(pct, width, secondary=0.0, peak=None):
    """A gradient meter, accurate to an eighth of a character cell.

    Each cell is tinted by its own position on the ramp rather than the bar carrying one
    flat colour, which is what makes a long bar read as a scale instead of a block.

    `secondary` is a second percentage drawn after the first in a dimmed tone - used for
    the memory the kernel holds as cache: not free, not the process's, and worth seeing.
    `peak` is a percentage marked with a hairline on the empty track: the recent maximum,
    drawn only when it lies beyond what is filled, in the ramp colour of where it sits.
    """
    full_ch, empty_ch, second_ch, partials = BAR_FULL, BAR_EMPTY, BAR_SECONDARY, BAR_PARTIALS

    text = Text(no_wrap=True, overflow="crop")
    if width <= 0:
        return text
    pct = 0.0 if pct != pct else max(0.0, min(100.0, pct))
    secondary = 0.0 if secondary != secondary else max(0.0, min(100.0 - pct, secondary))
    span = max(width - 1, 1)
    cells = width * pct / 100.0
    filled = int(cells)
    for i in range(filled):
        text.append(full_ch, style=ramp(i / span))
    if partials and filled < width:
        eighths = int((cells - filled) * 8)
        if eighths:
            text.append(partials[eighths - 1], style=ramp(filled / span))
            filled += 1
    second = min(int(width * secondary / 100.0), width - filled)
    for i in range(second):
        text.append(second_ch, style=dim_rgb(ramp_rgb((filled + i) / span)))
    filled += second

    peak_cell = None
    if peak is not None and peak == peak:
        peak_cell = min(int(width * max(0.0, min(100.0, peak)) / 100.0), width - 1)
        # Only strictly beyond the fill: a peak equal to the value has nothing to mark,
        # and a hairline in the cell right after the fill is indistinguishable from it.
        if peak_cell <= filled:
            peak_cell = None
    if peak_cell is None:
        text.append(empty_ch * (width - filled), style=TRACK)
    else:
        text.append(empty_ch * (peak_cell - filled), style=TRACK)
        text.append(GLYPHS.peak, style=ramp(peak_cell / span))
        text.append(empty_ch * (width - peak_cell - 1), style=TRACK)
    return text


MIN_BAR_W = T.MIN_BAR_W


class Smoother:
    """Exponential smoothing for bar POSITIONS in live mode - and nothing else.

    A meter that jumps 30 -> 80 -> 35 between frames is noise the eye has to track; the
    same values eased over three frames read as movement. Only the drawn fill is eased:
    the printed number is always the raw sample, and snapshot mode (SMOOTHING off) shows
    raw fills too, so a report never carries an interpolated value. Keys that were not
    touched in a frame are dropped, so a process list cannot grow the table forever.
    """

    def __init__(self, alpha=T.SMOOTH_ALPHA):
        self.alpha = alpha
        self._state = {}
        self._touched = set()

    def value(self, key, raw):
        self._touched.add(key)
        prev = self._state.get(key)
        cur = raw if prev is None else prev + self.alpha * (raw - prev)
        self._state[key] = cur
        return cur

    def forget(self, key):
        """Drop a key's history, so the next sample is shown as it is rather than eased
        towards from a stale one - what a tempo needs when the music stops."""
        self._state.pop(key, None)
        self._touched.discard(key)

    def end_frame(self):
        for key in list(self._state):
            if key not in self._touched:
                del self._state[key]
        self._touched.clear()

    def reset(self):
        self._state.clear()
        self._touched.clear()


SMOOTHING = False           # run_live() switches it on; run_once() never does
_smoother = Smoother()


class PeakTracker:
    """The high-water mark of each meter over the last PEAK_WINDOW samples.

    A meter shows one instant; the peak marker tells the other half of the story - how
    high it went in the last quarter minute. It is drawn from RAW samples (never the
    eased fill), and it decays on its own as old samples leave the window, which is the
    one animation here that carries data. Keys not drawn in a frame are dropped.
    """

    def __init__(self, window=T.PEAK_WINDOW):
        self.window = window
        self._hist = {}
        self._touched = set()

    def value(self, key, raw):
        self._touched.add(key)
        hist = self._hist.get(key)
        if hist is None:
            hist = self._hist[key] = deque(maxlen=self.window)
        hist.append(raw)
        return max(hist)

    def end_frame(self):
        for key in list(self._hist):
            if key not in self._touched:
                del self._hist[key]
        self._touched.clear()

    def reset(self):
        self._hist.clear()
        self._touched.clear()


_peaks = PeakTracker()


def peak_of(key, raw):
    return _peaks.value(key, raw)


def shown(key, raw):
    """The fill to draw for `raw`: eased in live mode, raw everywhere else."""
    if not SMOOTHING:
        return raw
    return _smoother.value(key, raw)


def meter(label, pct, total, value=None, note="", label_w=9, value_w=7, secondary=0.0,
          note_w=None, fill=None, peak=None, unit_w=1):
    """`label  ▉▉▉▉╌╌╌  62.5%  note` on exactly one line, budgeted so it never wraps.

    The old two-line form (bar, then "6.1G / 16.0G" underneath) doubled the height of
    every panel for information that fits beside the bar.

    When the line is too narrow for everything, the annotation is dropped rather than
    sliced: a cut-off "421.4G/460." is worse than no annotation at all, because it still
    looks like a number.
    """
    # The number and its colour describe the WHOLE occupied part - primary plus secondary.
    # For memory that is psutil's percent (everything not available), which is what the
    # reader expects to see; the bar underneath shows how that splits.
    occupied = min(100.0, pct + max(0.0, secondary))
    if value is None:
        value = T.fmt_pct(occupied)
    # note_w fixes the annotation FIELD so the bar keeps its length whatever the note
    # says this frame - "9.8G" and "10.2G" must not move the bar by a cell.
    field_w = note_w if note_w is not None else (len(note) if note else 0)
    if field_w and total - label_w - value_w - (field_w + 2) < MIN_BAR_W:
        note, field_w = "", 0
    bar_w = max(total - label_w - value_w - (field_w + 2 if field_w else 0), 3)
    fill_pct = pct if fill is None else fill

    text = Text(no_wrap=True, overflow="crop")
    text.append(f"{label[:label_w - 1]:>{label_w - 1}} ", style=DIM)
    text.append_text(bar(fill_pct, bar_w, secondary, peak=peak))
    # Value bright and bold in the ramp tone; its unit ("%", "K/s") dim, so the digits
    # carry the weight and the unit is read once and then ignored.
    padded = f"{value:>{value_w}}"
    unit_w = min(unit_w, len(padded))
    text.append(padded[:len(padded) - unit_w], style=f"bold {ramp(occupied / 100)}")
    if unit_w:
        text.append(padded[len(padded) - unit_w:], style=DIM)
    if field_w:
        text.append(f"  {note:>{field_w}}", style=MUTED)
    return text


def sparkline(values, width):
    """A width-cell block sparkline, each cell the PEAK of its slice, tinted by the ramp.

    Peaks rather than means: a sparkline exists to show that something spiked, and a mean
    over four samples flattens exactly the sample you wanted to see.
    """
    text = Text(no_wrap=True, overflow="crop")
    if not values or width <= 0 or not SPARK:
        return text
    values = list(values)
    step = max(1, math.ceil(len(values) / width))
    cells = []
    for i in range(0, len(values), step):
        peak = max(values[i:i + step])
        t = max(0.0, min(1.0, peak / 100.0))
        cells.append((SPARK[min(int(t * len(SPARK)), len(SPARK) - 1)], ramp(t)))
    # Always `width` cells: while the history is still filling, the missing cells are
    # drawn as the lowest glyph in the track tone, so the header's tail never moves.
    for _ in range(width - len(cells)):
        text.append(SPARK[0], style=TRACK)
    for glyph, style in cells:
        text.append(glyph, style=style)
    return text


def heat_strip(values, width):
    """One cell per value, coloured by the ramp - for machines with too many cores to list."""
    text = Text(no_wrap=True, overflow="crop")
    if not values:
        return text
    step = max(1, math.ceil(len(values) / width))
    for i in range(0, len(values), step):
        chunk = values[i:i + step]
        avg = sum(chunk) / len(chunk)
        text.append(BAR_FULL, style=ramp(avg / 100))
    return text


# ---------------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------------

def _read_steal_pct():
    global _steal_last_total, _steal_last_steal
    if not IS_LINUX:
        return 0.0
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        total_j = sum(int(x) for x in parts[1:])
        steal_j = int(parts[8])
        if _steal_last_total is not None:
            dt = total_j - _steal_last_total
            ds = steal_j - _steal_last_steal
            pct = (ds / dt * 100) if dt > 0 else 0
        else:
            pct = 0
        _steal_last_total = total_j
        _steal_last_steal = steal_j
        return pct
    except Exception:
        return 0.0


def core_columns(ncores, max_rows):
    """How many columns to lay the per-core meters out in, so they fit `max_rows`.

    Returns 0 when even four columns would not fit - the caller then draws a heat strip
    instead of a list, because 64 core meters are not information, they are wallpaper.
    """
    # One column first: wider bars, and it fills a panel that would otherwise sit next to
    # a taller neighbour with a hole in it. Only split when the list would not fit.
    if ncores <= max_rows:
        return 1
    for cols in (2, 3, 4):
        if math.ceil(ncores / cols) <= max_rows:
            return cols
    return 0


def get_cpu_section(width, max_rows=99):
    percents = psutil.cpu_percent(percpu=True)
    total = psutil.cpu_percent()
    steal_pct = _read_steal_pct()

    cols = core_columns(len(percents), max_rows)
    if cols == 0:
        rows = [Text("      cores ", style=DIM).append_text(heat_strip(percents, width - 14))]
    elif cols == 1:
        rows = [meter(f"cpu{i}", p, width, fill=shown(f"cpu{i}", p), peak=peak_of(f"cpu{i}", p))
                for i, p in enumerate(percents)]
    else:
        grid = Table.grid(expand=True, padding=(0, 2))
        col_w = (width - 2 * (cols - 1)) // cols
        for _ in range(cols):
            grid.add_column(width=col_w)
        per_col = math.ceil(len(percents) / cols)
        for r in range(per_col):
            cells = []
            for c in range(cols):
                i = c * per_col + r
                cells.append(meter(f"cpu{i}", percents[i], col_w, fill=shown(f"cpu{i}", percents[i]),
                                   peak=peak_of(f"cpu{i}", percents[i]))
                             if i < len(percents) else Text(""))
            grid.add_row(*cells)
        rows = [grid]

    rows.append(meter("TOTAL", total, width, fill=shown("cpu.total", total),
                      peak=peak_of("cpu.total", total)))
    if IS_LINUX:
        rows.append(meter("steal", steal_pct, width, fill=shown("cpu.steal", steal_pct),
                          peak=peak_of("cpu.steal", steal_pct)))
    return Group(*rows), total, steal_pct


def cpu_section_rows(ncores, max_rows=99):
    """Height in lines that get_cpu_section() will occupy for this core count."""
    cols = core_columns(ncores, max_rows)
    core_rows = 1 if cols == 0 else (ncores if cols == 1 else math.ceil(ncores / cols))
    return core_rows + 1 + (1 if IS_LINUX else 0)


_fmt_gb = T.fmt_gb


def get_memory_section(width):
    mem = psutil.virtual_memory()
    # What is neither in use nor available is the kernel's cache: reclaimable, so not
    # "used", but not free either. It gets its own dimmed segment on the bar.
    # psutil's `percent` is everything-not-available, which already counts the cache. The
    # bar splits that figure into what processes hold (ramp) and what the kernel caches
    # (dimmed); the number stays psutil's, because that is the "how full" everyone means.
    cache = max(0, mem.total - mem.used - mem.available)
    used_pct = 100.0 * mem.used / mem.total if mem.total else 0.0
    cache_pct = max(0.0, mem.percent - used_pct)
    # The note field is fixed per panel width, not per value: with the cache suffix on a
    # panel wide enough to hold it NEXT TO a usable bar, without it otherwise - never
    # appearing and disappearing. (A threshold of 44 once chose the long variant on a
    # panel that could not fit it, and meter() then dropped the note altogether.)
    wide = width >= T.LABEL_W + T.VALUE_W + T.NOTE_MEM_W + 2 + T.MIN_BAR_W
    note = T.fmt_gb_pair(mem.used, mem.total)
    if wide and cache:
        note += f" +{min(cache / 1024 ** 3, 99.9):4.1f}G cache"
    rows = [meter("ram", used_pct, width, value=T.fmt_pct(mem.percent), note=note,
                  note_w=T.NOTE_MEM_W if wide else T.NOTE_GB_PAIR_W,
                  secondary=cache_pct, fill=shown("mem.ram", used_pct),
                  peak=peak_of("mem.ram", mem.percent))]
    swap = psutil.swap_memory()
    if swap.total > 0:
        rows.append(meter("swap", swap.percent, width,
                          note=T.fmt_gb_pair(swap.used, swap.total),
                          note_w=T.NOTE_MEM_W if wide else T.NOTE_GB_PAIR_W,
                          fill=shown("mem.swap", swap.percent),
                          peak=peak_of("mem.swap", swap.percent)))
    return Group(*rows)


def memory_section_rows():
    return 1 + (1 if psutil.swap_memory().total > 0 else 0)


SKIP_FS = {"tmpfs", "devtmpfs", "squashfs", "overlay", "devfs", "autofs"}
# macOS marks its own internal volumes "nobrowse"/"dontbrowse" - Finder hides them and so
# should we. Without this filter a stock Mac lists nine partitions for one physical disk
# (Preboot, Update, VM, xarts, iSCPreboot, Hardware, ...), four of them reporting the same
# total because APFS shares space across a container.
HIDDEN_MOUNT_OPTS = ("dontbrowse", "nobrowse")
MACOS_DATA_VOLUME = "/System/Volumes/Data"


def disk_entries():
    """The partitions worth showing, as (label, usage) pairs."""
    entries, seen = [], set()
    for part in psutil.disk_partitions(all=False):
        if part.fstype in SKIP_FS or part.mountpoint in seen:
            continue
        opts = (part.opts or "").lower()
        if any(hidden in opts for hidden in HIDDEN_MOUNT_OPTS):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        seen.add(part.mountpoint)
        entries.append((part.mountpoint, usage))

    # On macOS "/" is a sealed read-only system snapshot; every byte the user owns lives in
    # the Data volume of the same APFS group, which the filter above just removed. Reporting
    # "/" verbatim would show 11G used on a disk that is 98% full.
    if IS_MACOS:
        try:
            data = psutil.disk_usage(MACOS_DATA_VOLUME)
        except (PermissionError, OSError):
            data = None
        if data is not None:
            entries = [(label, data if label == "/" else usage) for label, usage in entries]
    return entries


DISK_LABEL_W = T.DISK_LABEL_W


def short_mount(label, width=DISK_LABEL_W):
    """Shorten a mountpoint from the left, keeping the part that identifies it.

    Cutting blindly turns "/Volumes/Untitled" into "…umes/Un", which names nothing. The
    last path component is the part a human recognises.
    """
    if len(label) <= width:
        return label
    ellipsis = GLYPHS.ellipsis
    tail = label.rstrip("/").rsplit("/", 1)[-1]
    if tail and len(tail) + 1 <= width:
        return ellipsis + tail
    return ellipsis + label[-(width - 1):]


def get_disk_section(width):
    rows = []
    for label, usage in disk_entries():
        rows.append(meter(short_mount(label), usage.percent, width, label_w=DISK_LABEL_W + 1,
                          note=T.fmt_gb_pair(usage.used, usage.total), note_w=T.NOTE_GB_PAIR_W,
                          fill=shown(f"disk.{label}", usage.percent),
                          peak=peak_of(f"disk.{label}", usage.percent)))
    io_line = _disk_io_line()
    if io_line is not None:
        rows.append(io_line)
    if not rows:
        return Group(Text("  No disks found", style=MUTED))
    return Group(*rows)


def _disk_io_available():
    try:
        return psutil.disk_io_counters() is not None
    except Exception:
        return False


def _disk_io_line():
    """Read/write throughput. Present from the FIRST frame, with placeholders until the
    second sample exists - a line that appears one frame later reflows the whole panel."""
    try:
        io = psutil.disk_io_counters()
    except Exception:
        return None
    if not io:
        return None
    read_s = write_s = None
    if hasattr(get_disk_section, "_last_io"):
        dt = _now() - get_disk_section._last_time
        if dt > 0:
            read_s = (io.read_bytes - get_disk_section._last_io.read_bytes) / dt
            write_s = (io.write_bytes - get_disk_section._last_io.write_bytes) / dt
    get_disk_section._last_io = io
    get_disk_section._last_time = _now()
    placeholder = f"{'n/a':>{T.RATE_W - 1}}"
    line = Text(no_wrap=True, overflow="crop")
    line.append(f"{'io':>{DISK_LABEL_W}} ", style=DIM)
    line.append("read ", style=MUTED)
    line.append(T.fmt_rate(read_s) if read_s is not None else placeholder, style=SOFT)
    line.append("   write ", style=MUTED)
    line.append(T.fmt_rate(write_s) if write_s is not None else placeholder, style=SOFT)
    return line


def disk_section_rows():
    return max(len(disk_entries()), 1) + (1 if _disk_io_available() else 0)


def _fmt_bytes_rate(b):
    if b > 1024**2:
        return f"{b / 1024**2:.1f} MB/s"
    elif b > 1024:
        return f"{b / 1024:.1f} KB/s"
    return f"{b:.0f} B/s"


def get_network_section(width):
    net = psutil.net_io_counters()
    if hasattr(get_network_section, '_last'):
        dt = _now() - get_network_section._last_time
        sent_s = (net.bytes_sent - get_network_section._last.bytes_sent) / dt if dt > 0 else 0
        recv_s = (net.bytes_recv - get_network_section._last.bytes_recv) / dt if dt > 0 else 0
    else:
        sent_s = recv_s = 0
    get_network_section._last = net
    get_network_section._last_time = _now()

    try:
        conns = len(psutil.net_connections())
    except psutil.AccessDenied:
        conns = -1

    peak = max(list(net_sent_history) + list(net_recv_history) + [sent_s, recv_s, 1.0])
    tx_pct, rx_pct = 100 * sent_s / peak, 100 * recv_s / peak
    rows = [
        meter("tx", tx_pct, width, value=T.fmt_rate(sent_s), value_w=T.RATE_W, unit_w=3,
              note=f"{GLYPHS.sigma} {T.fmt_gb(net.bytes_sent)}", note_w=T.NOTE_TOTAL_W,
              fill=shown("net.tx", tx_pct), peak=peak_of("net.tx", tx_pct)),
        meter("rx", rx_pct, width, value=T.fmt_rate(recv_s), value_w=T.RATE_W, unit_w=3,
              note=f"{GLYPHS.sigma} {T.fmt_gb(net.bytes_recv)}", note_w=T.NOTE_TOTAL_W,
              fill=shown("net.rx", rx_pct), peak=peak_of("net.rx", rx_pct)),
    ]
    if conns >= 0:
        line = Text(no_wrap=True, overflow="crop")
        line.append(f"{'conns':>{T.LABEL_W - 1}} ", style=DIM)
        line.append(T.fmt_count(conns), style=TEXT)
        rows.append(line)
    return Group(*rows), sent_s, recv_s


def network_section_rows():
    try:
        psutil.net_connections()
        return 3
    except psutil.AccessDenied:
        return 2


_fmt_mem = T.fmt_mem


def get_top_processes(width, n=8):
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'memory_info']):
        try:
            info = p.info
            if info['cpu_percent'] is not None:
                procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Descending CPU with the PID as tiebreaker: without it, processes at equal CPU swap
    # rows from frame to frame and the list dances.
    procs.sort(key=lambda x: (-(x['cpu_percent'] or 0), x['pid']))

    # The inline bar earns the empty space the name column used to leave behind, and puts
    # the shape of the load next to the number - btop's trick.
    bar_w = 14 if width >= 86 else 0
    table = Table(box=None, show_header=True, header_style=MUTED, expand=True,
                  pad_edge=False, padding=(0, 1))
    table.add_column("pid", justify="right", width=6, style=MUTED)
    table.add_column("process", ratio=1, no_wrap=True, overflow="ellipsis")
    if bar_w:
        table.add_column("", width=bar_w)
    table.add_column("cpu%", justify="right", width=6)
    table.add_column("mem%", justify="right", width=6)
    table.add_column("rss", justify="right", width=7)

    for proc in procs[:n]:
        cpu_pct = proc['cpu_percent'] or 0
        mem_pct = proc['memory_percent'] or 0
        rss = proc['memory_info'].rss if proc['memory_info'] else 0
        # The ellipsis comes from the column, not from here - see the no_wrap column above.
        cells = [str(proc['pid']), Text(proc['name'] or "?")]
        if bar_w:
            cells.append(bar(shown(f"proc.{proc['pid']}", cpu_pct), bar_w - 1,
                             peak=peak_of(f"proc.{proc['pid']}", cpu_pct)))
        cells += [
            Text(f"{min(cpu_pct, 9999.9):5.1f}", style=f"bold {ramp(cpu_pct / 100)}"),
            Text(f"{min(mem_pct, 100.0):5.1f}", style=ramp(mem_pct / 25)),
            Text(_fmt_mem(rss), style=SOFT),
        ]
        table.add_row(*cells)
    return table


# ---------------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------------

def _window_label(interval=None):
    """Wall-clock span of a full history buffer, e.g. "last 30s" / "last 3m".

    Hard-coding "last 60s" was true for exactly one interval value and wrong for every
    other one - at the 0.5 s default the charts span 30 s, not 60.
    """
    seconds = HISTORY_LEN * (sample_interval if interval is None else interval)
    if seconds < 90:
        return f"last {seconds:.0f}s"
    minutes = f"{seconds / 60:.1f}".rstrip("0").rstrip(".")
    return f"last {minutes}m"


def _time_ticks(n, span_s=None):
    """x-axis labels in seconds-ago, which is what the axis actually measures.

    plotext labels the x axis with sample indices by default (1.0, 15.8, 30.5, ...) - a
    number nobody reading a live dashboard has any use for. The dashboard's charts span
    HISTORY_LEN samples; a caller with its own window (the audio histories) passes span_s.
    """
    span = HISTORY_LEN * sample_interval if span_s is None else span_s
    positions = [0, n // 2, max(n - 1, 0)]
    labels = [f"-{span:.0f}s", f"-{span / 2:.0f}s", "now"]
    return positions, labels


def _ascii_chart(values, ylim, width, height, span_s=None):
    """Chart fallback for terminals that cannot draw plotext's box characters.

    plotext frames every plot in box-drawing glyphs, so no marker choice yields pure
    ASCII - the chart has to be drawn by hand or dropped, and dropping it would remove
    the feature the tool is named for. This draws real columns, so it fills the panel
    the braille version would have filled.
    """
    if not values or width <= 0 or height <= 0:
        return Text("")
    lo, hi = ylim if ylim else (min(values), max(values) or 1.0)
    span = (hi - lo) or 1.0
    axis_w = max(len(f"{hi:.0f}"), len(f"{lo:.0f}")) + 1
    plot_w = max(width - axis_w, 1)

    step = max(1, math.ceil(len(values) / plot_w))
    levels = [max(0.0, min(1.0, (sum(values[i:i + step]) / len(values[i:i + step]) - lo) / span))
              for i in range(0, len(values), step)]

    rows, plot_h = [], max(height - 1, 1)
    bg = T.rgb_of(THEME.bg)
    for r in range(plot_h):
        top = (plot_h - r) / plot_h
        bottom = (plot_h - r - 1) / plot_h
        # The same fade as the braille fill: solid at the base, toward the background up top.
        fade = T.CHART_FADE_TOP * (plot_h - 1 - r) / max(plot_h - 1, 1)
        label = f"{hi:.0f}" if r == 0 else (f"{lo:.0f}" if r == plot_h - 1 else "")
        line = Text(f"{label:>{axis_w - 1}} ", style=MUTED, no_wrap=True, overflow="crop")
        for t in levels:
            if t >= top:
                line.append(GLYPHS.chart_full, style=T.hex_of(T.mix_rgb(ramp_rgb(t), bg, fade)))
            elif t > bottom:
                line.append(GLYPHS.chart_half, style=T.hex_of(T.mix_rgb(ramp_rgb(t), bg, fade)))
            else:
                line.append(" ")
        rows.append(line)
    if span_s is None:
        span_s = HISTORY_LEN * sample_interval
    footer = Text(" " * axis_w, no_wrap=True, overflow="crop")
    footer.append(f"-{span_s:.0f}s".ljust(max(plot_w - 3, 1))[:max(plot_w - 3, 1)] + "now", style=MUTED)
    rows.append(footer)
    return Group(*rows)


def nice_ceiling(x):
    """The smallest "round" number >= x, for an axis top that does not read as noise.

    plotext's own auto-ticks land on values like 466.6 and 373.3; an axis that says
    0 / 300 / 600 costs nothing to read.
    """
    if x <= 0:
        return 1.0
    base = 10.0 ** math.floor(math.log10(x))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * base >= x:
            return m * base
    return 10 * base


def _render_chart(series, ylim, width, height, axis_w=None, span_s=None):
    """Build one plotext chart. Never raises - a broken chart is a note, not a crash.

    axis_w fixes the y-label field per CHART, not per value: a rate chart whose top
    happens to be 100 must not narrow its axis and shift the plot two columns left.
    series: list of (values, label, color[, fill]); color may be a name or an RGB tuple.
    A filled series is drawn as an area under the line - the mass reads far better than a
    thin braille trace, but two overlapping fills turn to mud where they cross, so callers
    fill at most one series and draw the others as lines over it.
    """
    if GLYPHS.chart_marker is None:
        return _ascii_chart(series[0][0], ylim, width, height, span_s)
    if not _PLOTEXT_5:
        return Text(_CHART_NEEDS_PLOTEXT_5, style=MUTED)
    try:
        plt.clear_figure()
        if ylim is None:
            top = nice_ceiling(max((max(e[0]) for e in series if e[0]), default=1.0))
            ylim = (0, top)
        lo, hi = ylim
        for entry in series:
            values, _label, color = entry[:3]
            fill = bool(entry[3]) if len(entry) > 3 else False
            # braille packs 2x4 dots into one cell - four times the vertical resolution of
            # the block markers, and the same trick btop uses. "clear" keeps plotext from
            # painting its own black background over the terminal's.
            # Values are plotted RELATIVE TO THE FLOOR: plotext's fillx fills towards y = 0,
            # so a dBFS chart with ylim (-80, 0) filled downward from silence at the top.
            # Shifting puts the baseline at the bottom of every chart; the labels below stay
            # in the caller's units, and for a 0-based chart nothing changes.
            plt.plot([v - lo for v in values], marker=GLYPHS.chart_marker, color=color, fillx=fill)
        plt.ylim(0, hi - lo)
        # Labels in a fixed field: plotext sizes the axis column to the widest label, so a
        # "600" that becomes "1000" would shift the whole plot one column to the right.
        if axis_w is None:
            axis_w = T.AXIS_W_PCT if hi <= 100 and lo >= 0 and hi - lo <= 100 else T.AXIS_W_RATE
        plt.yticks([0, (hi - lo) / 2, hi - lo],
                   [T.fmt_axis(v, axis_w, top=hi) for v in (lo, (lo + hi) / 2, hi)])
        plt.theme("clear")
        # ticks_color must come AFTER theme(): the theme resets it. Labels sit in the
        # muted tone so the data, not the scaffolding, is what the eye lands on.
        plt.ticks_color(T.rgb_of(MUTED))
        plt.frame(T.CHART_FRAME)
        # plotext caps a plot at the terminal size IT detects - 80 columns in a pipe, a test or
        # a screenshot render - and a 116-column chart came back 80 wide. The caller has
        # already measured the slot; the cap only ever shrinks a chart that fits.
        plt.limit_size(False, False)
        plt.plotsize(width, height)
        positions, labels = _time_ticks(len(series[0][0]), span_s)
        plt.xticks(positions, labels)
        text = Text.from_ansi(plt.build(), no_wrap=True, overflow="crop")
        for entry in series:
            if len(entry) > 3 and entry[3] and isinstance(entry[2], tuple):
                text = _vertical_gradient(text, entry[2])
        return text
    except Exception:
        return Text("  Chart unavailable", style=MUTED)


def _collecting(width=40, height=6):
    """The empty state while the second sample is still on its way.

    A designed skeleton rather than a bare sentence: a flat baseline where the plot will
    be, in the track tone, and one quiet line saying why. It takes the same space as the
    chart it stands in for, so the panel does not change shape when the data arrives.
    """
    width, height = max(width, 8), max(height, 1)
    rows = [Text("", no_wrap=True, overflow="crop") for _ in range(height)]
    baseline = max(0, (height - 1) // 2)
    rows[baseline] = Text(GLYPHS.collecting * width, style=TRACK, no_wrap=True, overflow="crop")
    hint = f"collecting {GLYPHS.sep} 2 samples needed"
    if baseline + 1 < height:
        rows[baseline + 1] = Text(hint.center(width)[:width], style=MUTED, no_wrap=True, overflow="crop")
    return Group(*rows)


def _vertical_gradient(text, fill_rgb):
    """Fade a filled area toward the background from the bottom up.

    plotext paints a series in one colour. Re-tinting each row of the fill by its height
    - solid at the base, CHART_FADE_TOP of the way to the background at the top - gives
    the area weight where the data is and lets the line read on top of it. Only spans in
    the fill colour are touched; lines and axes keep theirs.
    """
    lines = text.split("\n")
    if len(lines) < 2:
        return text
    fill_hex = T.hex_of(fill_rgb)
    bg = T.rgb_of(THEME.bg)
    plot_rows = [i for i, line in enumerate(lines)
                 if any(sp.style and getattr(sp.style, "color", None) is not None
                        and sp.style.color.triplet is not None
                        and sp.style.color.triplet.hex == fill_hex for sp in line.spans)]
    if not plot_rows:
        return text
    top, bottom = plot_rows[0], plot_rows[-1]
    span_rows = max(bottom - top, 1)
    for i in plot_rows:
        k = T.CHART_FADE_TOP * (bottom - i) / span_rows
        tint = Style(color=T.hex_of(T.mix_rgb(fill_rgb, bg, k)))   # a Style, like from_ansi's
        line = lines[i]
        line.spans = [
            sp._replace(style=tint) if (sp.style and getattr(sp.style, "color", None) is not None
                                         and sp.style.color.triplet is not None
                                         and sp.style.color.triplet.hex == fill_hex) else sp
            for sp in line.spans
        ]
    joined = Text("\n", no_wrap=True, overflow="crop").join(lines)
    joined.no_wrap, joined.overflow = True, "crop"
    return joined


def cpu_chart_colour():
    """The area is tinted by the MEAN load of the window - the same position on the ramp
    the meters use, so a chart that has gone amber says the same thing an amber bar does."""
    if not cpu_history:
        return ramp_rgb(0.0)
    return ramp_rgb(sum(cpu_history) / len(cpu_history) / 100.0)


def get_cpu_chart(width, height):
    if len(cpu_history) < 2:
        return _collecting(width, height)
    series = [(list(cpu_history), "CPU %", cpu_chart_colour(), True)]
    if IS_LINUX and any(s > 0 for s in steal_history):
        series.append((list(steal_history), "Steal %", ramp_rgb(1.0), False))
    return _render_chart(series, (0, 100), width, height, axis_w=T.AXIS_W_PCT)


NET_UNITS = ((1024, "KB/s"), (1024 ** 2, "MB/s"), (1024 ** 3, "GB/s"))
_net_unit = "KB/s"


def net_scale():
    """(divisor, unit) so the network chart reads in KB/s, MB/s or GB/s, whichever fits.

    With hysteresis: up a unit once the window's peak reaches 2 of the next, back down
    only when it falls below 1 of the current. A single threshold made the axis and the
    legend flip back and forth with every sample near it. The top unit keeps the axis
    label within its five-cell field whatever the numbers do.
    """
    global _net_unit
    peak = max(list(net_sent_history) + list(net_recv_history) + [0.0])
    idx = [u for _, u in NET_UNITS].index(_net_unit)
    while idx + 1 < len(NET_UNITS) and peak >= 2 * NET_UNITS[idx + 1][0]:
        idx += 1
    while idx > 0 and peak < NET_UNITS[idx][0]:
        idx -= 1
    _net_unit = NET_UNITS[idx][1]
    return NET_UNITS[idx]


def get_net_chart(width, height):
    if len(net_sent_history) < 2:
        return _collecting(width, height)
    div, _unit = net_scale()
    series = [
        ([x / div for x in net_recv_history], "RX", NET_RX_RGB, True),
        ([x / div for x in net_sent_history], "TX", NET_TX_RGB, False),
    ]
    return _render_chart(series, None, width, height, axis_w=T.AXIS_W_RATE)


# ---------------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------------

COMPACT = False        # --compact: no horizontal padding inside panels
NO_BORDER = False      # --no-border: a title rule above each body instead of a frame


def set_frame(compact=False, no_border=False):
    global COMPACT, NO_BORDER
    COMPACT, NO_BORDER = compact, no_border


def chrome():
    """(rows, columns) a panel spends on its own frame - the ONE place this is decided,
    so the height budget, the body widths and the drawn panels can never disagree."""
    if NO_BORDER:
        return T.RULE_CHROME_H, T.RULE_CHROME_W
    if COMPACT:
        return T.PANEL_CHROME_H, T.COMPACT_CHROME_W
    return T.PANEL_CHROME_H, T.PANEL_CHROME_W


def _title(title, subtitle=""):
    """Panel title hierarchy: bold accent for the name, muted for the subtitle."""
    icon = ""
    if CAPS.nerd and title in T.NERD_ICONS:
        icon = T.NERD_ICONS[title] + " "
    head = f"[b {THEME.accent}]{icon}{title}[/]"
    if subtitle:
        head += f" [{MUTED}]{_sep()} {subtitle}[/{MUTED}]"
    return head


def _panel(renderable, title, subtitle=""):
    """One frame for every panel: the border in the theme's quiet border tone, never in a
    colour that competes with the content, and the same box and padding everywhere."""
    if NO_BORDER:
        # A title rule above the body and a one-column gutter each side: without the
        # gutter two columns of meters run into each other ("100.0%ram").
        rule = Rule(_title(title, subtitle), style=THEME.border, align="left",
                    characters=GLYPHS.rule)
        return Padding(Group(rule, renderable), (0, T.RULE_CHROME_W // 2))
    return Panel(renderable, title=_title(title, subtitle), title_align="left",
                 border_style=THEME.border, box=getattr(box, GLYPHS.box),
                 padding=T.COMPACT_PADDING if COMPACT else T.PANEL_PADDING)


def _sep():
    """The dot that joins subtitle parts - and the one glyph that leaked into ASCII mode."""
    return GLYPHS.sep


def cpu_chart_subtitle():
    """`last 30s · 42%` - the window, then the value the newest sample carries."""
    now = cpu_history[-1] if cpu_history else 0.0
    # Digits in the ramp tone, the unit dim - the same hierarchy as every meter value.
    return f"{_window_label()} {_sep()} [{ramp(now / 100)}]{min(now, 999):3.0f}[/][{DIM}]%[/]"


def net_chart_subtitle():
    """A legend that also states the current rates: `▇ rx 3.1MB/s  ━ tx 1.2MB/s · KB/s`.

    The filled series gets the block glyph and the line series the bar, so the legend
    shows what the reader will see rather than naming colours.
    """
    _div, unit = net_scale()
    rx = T.fmt_rate(net_recv_history[-1] if net_recv_history else 0.0)
    tx = T.fmt_rate(net_sent_history[-1] if net_sent_history else 0.0)
    rx_glyph, tx_glyph = GLYPHS.legend_fill, GLYPHS.legend_line
    rx_hex = "#%02x%02x%02x" % NET_RX_RGB
    tx_hex = "#%02x%02x%02x" % NET_TX_RGB
    return f"[{rx_hex}]{rx_glyph} rx[/] {rx}  [{tx_hex}]{tx_glyph} tx[/] {tx} {_sep()} {unit:>4}"


def header_line(width, badge=None):
    load1, load5, load15 = psutil.getloadavg()
    ncpu = psutil.cpu_count() or 1
    uptime_s = _now() - psutil.boot_time()
    if DEMO is not None:
        host, os_name = DEMO.node, DEMO.system
    else:
        host = platform.node()
        os_name = {"Linux": "Linux", "Darwin": "macOS", "Windows": "Windows"}.get(
            platform.system(), platform.system())

    # Every field has a fixed width. The load can gain a digit, the uptime a day, the
    # process count a thousand - none of it may move the fields to its right.
    text = Text(no_wrap=True, overflow="crop")
    text.append(" TERMSTATS ", style=f"bold {THEME.wordmark_fg} on {THEME.wordmark_bg}")
    if DEMO is not None:
        # Scripted numbers must never pass for a real machine: the badge is part of the
        # fixed fields, so it is on every frame at every width.
        text.append(" DEMO ", style=f"bold {THEME.wordmark_fg} on {ramp(1.0)}")
    if badge:                                       # the microphone modes: EQ / BPM / DB
        text.append(f" {badge} ", style=f"bold {THEME.wordmark_fg} on {ramp(0.55)}")
    text.append(f"  {host[:12]:12s}", style=f"bold {THEME.wordmark_fg}")
    text.append(f" {os_name:7s}", style=MUTED)
    text.append(" load ", style=MUTED)
    text.append(T.fmt_load(load1), style=f"bold {ramp(load1 / (ncpu * 2))}")
    text.append(f" {T.fmt_load(load5)} {T.fmt_load(load15)}", style=DIM)
    text.append(f"  {min(ncpu, 999):3d} cpu", style=MUTED)
    text.append(" up ", style=MUTED)
    text.append(T.fmt_uptime(uptime_s), style=TEXT)
    text.append(" proc ", style=MUTED)
    text.append(T.fmt_count(len(psutil.pids())), style=TEXT)

    # The tail degrades in fixed steps decided by the WIDTH alone - full, without the
    # sparkline, none - so what the reader sees at a given width never changes between
    # frames. (Dropping it whenever the numbers happened to be long made the clock blink.)
    clock = Text(no_wrap=True)
    # The wall clock is the liveness signal: a frozen dashboard and a quiet machine look
    # identical without it. It sits in the tail because the head is the identity.
    clock.append(time.strftime("%H:%M:%S", time.localtime(_now())) + "  ", style=TEXT)   # demo mode: the demo clock
    clock.append(f"{sample_interval:>4g}s  ", style=MUTED)
    clock.append(f"v{__version__} ", style=FAINT)

    # A SPARK_W-cell CPU sparkline, tmux-status-bar style: the whole recent history in one
    # glance without looking down at the chart. Peaks per cell, tinted by the ramp - and
    # always the same width, so the clock never drifts while the history fills. It sits
    # in the MIDDLE of the free space: identity left, liveness right, history between.
    spark = sparkline(cpu_history, T.SPARK_W)
    if not spark.cell_len:
        for _ in range(T.SPARK_W):
            spark.append(SPARK[0] if SPARK else " ", style=TRACK)

    free = width - text.cell_len - clock.cell_len
    if free >= T.SPARK_W + 4:                         # full: centred sparkline + clock
        left = (free - T.SPARK_W) // 2
        text.append(" " * left)
        text.append_text(spark)
        text.append(" " * (free - T.SPARK_W - left))
        text.append_text(clock)
    elif free >= 2:                                   # clock only
        text.append(" " * free)
        text.append_text(clock)
    return text


LIVE = False               # set by the run modes: the footer's exit hint is for a human
FOOTER_BRAND = "Martin Pfeffer | celox.io"


def _current_year():
    return time.localtime().tm_year


def footer_line(width):
    """`Ctrl+C to exit` on the left in live mode, `© <year> Martin Pfeffer | celox.io`
    right-aligned - the year from the clock, so it is never stale. Both fixed-width;
    the hint goes first when the line cannot hold both."""
    text = Text(no_wrap=True, overflow="crop")
    hint = " Esc or Ctrl+C to exit" if LIVE else ""
    brand = f"{GLYPHS.copyright} {_current_year()} {FOOTER_BRAND} "
    if len(hint) + len(brand) + 2 > width:
        hint = ""
    text.append(hint, style=MUTED)
    text.append(" " * max(width - len(hint) - len(brand), 0))
    text.append(brand, style=FAINT)
    return text


def render_dashboard(width=None, height=None):
    """Compose the whole dashboard for a terminal of this size.

    Explicitly size-aware: the layout has to know the height to decide what fits. The old
    grid had no idea and produced 79 lines on a 40-line terminal, which meant the charts
    and the process list - the reason the tool exists - were simply never on screen.
    """
    size = console.size
    tw = max(width or size.width, 40)
    th = max(height or size.height, 8)

    ch, cw = chrome()
    body_h = th - 2                                  # header and footer are fixed rows
    narrow = tw < T.NARROW_BELOW
    right_w = 0 if narrow else max(T.RIGHT_COL_MIN, min(int(tw * T.RIGHT_COL_SHARE), T.RIGHT_COL_MAX))
    left_w = tw - right_w

    # --- collect (widths are panel interiors: minus the frame's own columns) ----------
    cpu_body_w = (left_w if not narrow else tw) - cw
    right_body_w = (right_w if not narrow else tw) - cw

    mem_h = memory_section_rows() + ch
    net_h = network_section_rows() + ch
    disk_h = disk_section_rows() + ch

    # Height is decided before the columns are, not after: the CPU panel would rather be
    # one tall column of wide bars than two short ones with a hole underneath, so first
    # work out how tall the row wants to be, then fit the cores into it.
    ncores = psutil.cpu_count() or 1
    extra_rows = 1 + (1 if IS_LINUX else 0)          # TOTAL, plus steal on Linux
    # Exact, not capped: when the cores have to be packed into columns the panel uses
    # ceil(n / cols) rows, which can be one short of the cap - and that row would sit
    # blank under TOTAL. (Invisible inside a frame; a hole once the frame is a rule.)
    cap = max(8, body_h // 2)
    cpu_wanted = cpu_section_rows(ncores, max(cap - ch - extra_rows, 1)) + ch

    # A short CPU panel next to a tall stack is exactly the hole this rewrite exists to
    # remove. When that happens the disk panel leaves the stack and takes the full width -
    # same total height, no dead space.
    disk_in_stack = cpu_wanted + ch >= mem_h + net_h + disk_h
    stack_h = mem_h + net_h + (disk_h if disk_in_stack else 0)

    if narrow:
        # One column: the panels are stacked, so they compete for the same lines the
        # side-by-side layout would have given them for free. Take only what fits, whole.
        # (A cap on cpu_wanted itself was tried here and removed: 0 differences across
        # 13,608 geometries, because the section-drop loop below already covers it.)
        room = body_h - cpu_wanted
        stack_h = 0
        kept_stack = 0
        for height in ([mem_h, net_h] + ([disk_h] if disk_in_stack else [])):
            if stack_h + height > room:
                break
            stack_h += height
            kept_stack += 1
        top_h = cpu_wanted + stack_h
    else:
        kept_stack = 3 if disk_in_stack else 2
        top_h = max(cpu_wanted, stack_h)

    # --- height budget ---------------------------------------------------------------
    remaining = body_h - top_h - (0 if disk_in_stack else disk_h)
    proc_min = ch + 1 + 2                            # frame, header row, two processes
    charts_h = 0
    if remaining >= T.CHART_MIN_H:
        charts_h = min(T.CHART_MAX_H, remaining - proc_min)
    proc_h = remaining - charts_h

    # ⚠️ The CPU panel gets cpu_wanted rows in the narrow stack, top_h in the wide row.
    # Deriving core_rows from top_h in BOTH cases laid the cores out for the height of
    # the whole stack and let the Layout crop the panel: at 60x20 cpu8, cpu9 and TOTAL
    # were simply missing (found by the --no-border sweep, present since the layout
    # rewrite).
    cpu_h = cpu_wanted if narrow else top_h
    core_rows = max(cpu_h - ch - extra_rows, 1)
    cpu_body, cpu_total, steal_pct = get_cpu_section(cpu_body_w, max_rows=core_rows)
    mem_body = get_memory_section(right_body_w)
    net_body, sent_s, recv_s = get_network_section(right_body_w)
    disk_body = get_disk_section(right_body_w if disk_in_stack else tw - cw)

    cpu_history.append(cpu_total)
    steal_history.append(steal_pct)
    net_sent_history.append(sent_s)
    net_recv_history.append(recv_s)
    _smoother.end_frame()
    _peaks.end_frame()

    sections = [Layout(header_line(tw), name="head", size=1)]

    stack_panels = [(_panel(mem_body, "memory"), mem_h),
                    (_panel(net_body, "network"), net_h)]
    if disk_in_stack:
        stack_panels.append((_panel(disk_body, "disk"), disk_h))
    stack_panels = stack_panels[:kept_stack]

    cpu_panel = _panel(cpu_body, "cpu")
    top = Layout(name="top", size=top_h)

    # ⚠️ The last card everywhere takes ratio instead of a fixed size, so slack is absorbed
    # by a panel that stretches rather than left as a blank strip. A rich Group does NOT
    # stretch - it renders its children at their natural height and leaves the remainder
    # empty - so the narrow branch nests Layouts too, rather than stacking a Group.
    if not stack_panels:
        top = Layout(cpu_panel, name="top", size=top_h)
    elif narrow:
        top.split_column(Layout(cpu_panel, size=cpu_wanted),
                         *[Layout(panel, size=h) for panel, h in stack_panels[:-1]],
                         Layout(stack_panels[-1][0], ratio=1))
    else:
        stack = Layout(name="right")
        stack.split_column(*[Layout(panel, size=h) for panel, h in stack_panels[:-1]],
                           Layout(stack_panels[-1][0], ratio=1))
        top.split_row(Layout(cpu_panel, name="cpu"),
                      Layout(stack, name="rightcol", size=right_w))
    sections.append(top)

    if not disk_in_stack and kept_stack:
        sections.append(Layout(_panel(disk_body, "disk"), name="disk", size=disk_h))

    if charts_h:
        chart_w = (tw - 1) // 2 - cw
        chart_h = charts_h - ch
        charts = Layout(name="charts", size=charts_h)
        charts.split_row(
            Layout(_panel(get_cpu_chart(chart_w, chart_h), "cpu",
                          cpu_chart_subtitle()), name="c1"),
            Layout(_panel(get_net_chart(chart_w, chart_h), "network",
                          net_chart_subtitle()), name="c2"),
        )
        sections.append(charts)

    # A panel needs its frame, a header row and at least two rows of content before it is
    # worth drawing; below that it is a stump, and a stump is worse than the space.
    if proc_h >= proc_min:
        procs = get_top_processes(tw - cw, n=max(proc_h - ch - 1, 1))
        sections.append(Layout(_panel(procs, "processes", "by cpu"), name="proc"))

    # ⚠️ The budget above is a plan, not a guarantee: a narrow terminal, a machine with
    # several mountpoints or Linux's extra steal row can all push the fixed sizes past the
    # height available. Drop sections from the bottom until the plan actually fits -
    # otherwise the trailing ratio section is squeezed to two lines and renders as a
    # bordered stump. (Found by CI on Linux, where the steal row tips the balance.)
    while len(sections) > 2 and sum(s.size or 0 for s in sections) > th - 1:
        sections.pop()

    # Whatever section ends up last takes the remaining height instead of a fixed size, so
    # a dropped panel leaves its lines to its neighbour rather than to a blank strip.
    sections[-1].size = None
    sections[-1].ratio = 1
    sections.append(Layout(footer_line(tw), name="foot", size=1))

    root = Layout()
    root.split_column(*sections)
    return root


# ---------------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------------

DEMO = None                # a demo.DemoSource while --demo runs, else None
_REAL_PSUTIL = psutil


def set_demo(source):
    """Swap the metrics source: a DemoSource, or None for the real psutil. Every
    collector looks psutil up on this module at call time, so rebinding is enough."""
    global psutil, DEMO
    DEMO = source
    psutil = source if source is not None else _REAL_PSUTIL


def _now():
    """Wall clock for the rate deltas and the uptime: the demo's own clock in demo mode
    (one interval per frame), so rates come out as designed however fast the frames are
    produced - the prefill plays sixty of them in a tight loop."""
    return DEMO.now() if DEMO is not None else time.time()


def _prefill_history(frames=HISTORY_LEN):
    """Demo only: play `frames` frames through the collectors before the first visible
    one, so the charts open full instead of with "collecting"."""
    for _ in range(frames):
        _, total, steal = get_cpu_section(80, max_rows=99)
        _, sent_s, recv_s = get_network_section(80)
        get_disk_section(80)
        cpu_history.append(total)
        steal_history.append(steal)
        net_sent_history.append(sent_s)
        net_recv_history.append(recv_s)
        _smoother.end_frame()
        _peaks.end_frame()


def _prime_measurements():
    psutil.cpu_percent(percpu=True)
    psutil.cpu_percent()
    for p in psutil.process_iter(['cpu_percent']):
        try:
            p.cpu_percent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    # Seed the rate collectors. A rate is a delta, and they keep the previous sample in
    # function attributes that only their own calls set - so the single render of a snapshot
    # was their FIRST call and printed 0.0B/s and n/a despite the priming pause (0.1.0-0.4.1).
    # The history deques are deliberately left alone: a two-point chart is not a chart.
    get_network_section._last = psutil.net_io_counters()
    get_network_section._last_time = _now()
    try:
        io = psutil.disk_io_counters()
    except Exception:
        io = None
    if io:
        get_disk_section._last_io = io
        get_disk_section._last_time = _now()
    try:
        # Windows emulates the load average with a sampler that reads 0.00 for its first
        # five seconds; asking now starts that clock before the first frame, not with it.
        psutil.getloadavg()
    except Exception:
        pass


def _schedule_tick(next_tick, now, interval):
    """Fixed-cadence scheduling. Returns (next_tick, seconds_to_sleep).

    Sleeping a flat `interval` after every render makes the real period
    interval + render time, which drifts visibly at 0.5 s. If a render overran the
    interval the schedule resyncs to now, rather than banking a backlog of instant frames.
    """
    next_tick += interval
    if next_tick <= now:
        next_tick = now + interval
    return next_tick, next_tick - now


# --- lifecycle ---------------------------------------------------------------------------
# A resize must not wait for the next tick: the old frame was laid out for the old size and
# reads as garbage until replaced. SIGWINCH sets a flag; the tick sleep runs in slices and
# returns early when it is set. Windows has no SIGWINCH - there the flag is simply never set.
_resized = threading.Event()
RESIZE_SLICE_S = 0.1


def _on_resize(signum=None, frame=None):
    _resized.set()


def _install_resize_handler():
    """Route SIGWINCH to _on_resize. Returns the previous handler so it can be put back,
    or None where the signal does not exist or cannot be installed (not the main thread)."""
    if not hasattr(signal, "SIGWINCH"):
        return None
    try:
        return signal.signal(signal.SIGWINCH, _on_resize)
    except (ValueError, OSError):
        return None


def _restore_resize_handler(previous):
    if previous is None or not hasattr(signal, "SIGWINCH"):
        return
    try:
        signal.signal(signal.SIGWINCH, previous)
    except (ValueError, OSError):
        pass


# ---------------------------------------------------------------------------------
# Ending a live session with a key
#
# Ctrl+C has always worked; Esc is what a full-screen program is expected to answer to.
# The terminal is put into cbreak mode for the session so a single keypress arrives without
# Enter, and restored in the same `finally` that restores the cursor. When stdin is not a
# terminal (`termstats --live | tee log`) the watcher stays inactive and nothing is read.
# ---------------------------------------------------------------------------------

QUIT_KEYS = (b"\x1b", b"q", b"Q")     # Esc, or q for the habit of it
_ESC_SEQUENCE_STARTS = (b"[", b"O")   # Esc is also the first byte of every arrow/function key


def is_quit_key(data):
    """True when a burst of input from the terminal asks to leave.

    An arrow key sends `Esc [ A`, so a lone Esc means "quit" and an Esc that introduces a
    sequence does not - the whole burst arrives in one read, which is what makes them
    distinguishable at all.
    """
    if not data:
        return False
    if data.startswith(b"\x1b") and len(data) > 1 and data[1:2] in _ESC_SEQUENCE_STARTS:
        return False
    return any(key in data for key in QUIT_KEYS)


def _set_cbreak(fd):
    """Put the terminal into cbreak mode and return what it was, or None where it cannot be."""
    try:
        import termios
        import tty
    except ImportError:                 # Windows: msvcrt polls without changing modes
        return None
    try:
        saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        return saved
    except Exception:
        return None


def _restore_tty(fd, saved):
    if saved is None:
        return
    try:
        import termios
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    except Exception:
        pass


def _read_ready(fd):
    """Whatever is waiting on the terminal right now - never blocks, b"" when nothing is."""
    try:
        import msvcrt                    # Windows has no select() on a console handle
    except ImportError:
        pass
    else:
        out = b""
        while msvcrt.kbhit():
            out += msvcrt.getch()
        return out
    import select
    ready, _, _ = select.select([fd], [], [], 0)
    return os.read(fd, 64) if ready else b""


class KeyWatcher:
    """Non-blocking single-key input for the duration of a live session."""

    def __init__(self):
        self.active = False
        self._fd = None
        self._saved = None

    def start(self):
        stdin = sys.stdin
        try:
            if stdin is None or not stdin.isatty():
                return
            self._fd = stdin.fileno()
        except Exception:               # a stream without a real descriptor (pytest, a pipe)
            self._fd = None
            return
        self._saved = _set_cbreak(self._fd)
        self.active = True

    def stop(self):
        if not self.active:
            return
        self.active = False
        _restore_tty(self._fd, self._saved)
        self._saved = None

    def quit_pressed(self):
        if not self.active:
            return False
        try:
            return is_quit_key(_read_ready(self._fd))
        except Exception:               # the terminal went away mid-session
            return False


_keys = KeyWatcher()
_quit = threading.Event()


def _sleep_until(deadline):
    """Sleep in RESIZE_SLICE_S slices until `deadline`.

    True when the wait was cut short - by a resize, or by the quit key, which also sets
    `_quit` so the caller knows to leave rather than draw one more frame.
    """
    while True:
        if _resized.is_set():
            _resized.clear()
            return True
        if _keys.quit_pressed():
            _quit.set()
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(remaining, RESIZE_SLICE_S))


def run_once():
    """One snapshot, then exit.

    Always samples for SNAPSHOT_SAMPLE_S regardless of --interval: rates need a gap
    between two reads, and --interval is the *live refresh rate*, not a sampling window.
    Never smoothed: a report carries raw samples only.
    """
    global sample_interval, SMOOTHING, LIVE
    sample_interval = SNAPSHOT_SAMPLE_S
    SMOOTHING = False
    LIVE = False
    _prime_measurements()
    if DEMO is not None:
        DEMO.interval = SNAPSHOT_SAMPLE_S   # one frame = one second, as the chart title says
        _prefill_history()             # a demo snapshot with empty charts shows nothing
    else:
        time.sleep(SNAPSHOT_SAMPLE_S)
    console.print(render_dashboard())


def run_live(interval=DEFAULT_INTERVAL):
    global sample_interval, SMOOTHING, LIVE
    sample_interval = interval
    SMOOTHING = True
    LIVE = True
    _smoother.reset()
    _peaks.reset()
    refresh = max(1, min(10, round(1 / interval)))
    previous = _install_resize_handler()
    _resized.clear()
    _quit.clear()
    _keys.start()
    # The cursor goes away before the priming pause, not with the alternate screen: half a
    # second of blinking cursor on an otherwise empty line read as "nothing is happening".
    # Ctrl+C anywhere in here - priming included, which used to sit outside the try and
    # print a traceback - ends the session quietly; the finally puts the terminal back
    # whatever happened, even if a render raised. (Live restores the alternate screen and
    # the cursor itself on the way out of its `with`; the finally covers what is outside.)
    console.show_cursor(False)
    try:
        _prime_measurements()
        if DEMO is not None:
            _prefill_history()
        else:
            time.sleep(min(interval, 0.5))
        with Live(render_dashboard(), console=console, refresh_per_second=refresh, screen=True) as live:
            next_tick = time.monotonic()
            while True:
                next_tick, _ = _schedule_tick(next_tick, time.monotonic(), interval)
                if _sleep_until(next_tick):
                    if _quit.is_set():
                        break
                    # Resized: relayout now and resync the cadence from here, rather than
                    # rendering an extra frame and keeping the old tick.
                    next_tick = time.monotonic()
                live.update(render_dashboard(), refresh=True)
    except KeyboardInterrupt:
        pass
    finally:
        _keys.stop()
        console.show_cursor(True)
        _restore_resize_handler(previous)


# ---------------------------------------------------------------------------------
# Microphone modes: -eq / -bpm / -db
#
# The analysis lives in termstats/audio.py (numpy - the optional audio extra), the device in
# termstats/capture.py. Everything here only DRAWS an Analyzer's levels, peaks, db, bpm and
# beats at time `now`, so the screens are testable from a synthetic analyzer and screenshots
# come from --demo without a microphone.
# ---------------------------------------------------------------------------------

AUDIO_MODES = ("eq", "bpm", "db")
AUDIO_INTERVAL = 0.05        # 20 frames a second: a spectrum at the dashboard's 0.5 s is a slideshow
AUDIO_READOUT_S = 0.2        # the big number's own, slower clock - a digit redrawn 20 times a
                             # second is a blur, while the bars and the beat flash need every frame
AUDIO_SNAPSHOT_S = 1.5       # --once: listen this long, then print one frame
AUDIO_DEMO_PREFILL_S = 8.0   # --demo: scripted music played (not in real time) before the first frame
AUDIO_HINT = "pip install 'termstats[audio]'"
BEAT_LIT_S = 0.12            # the beat indicator stays lit this long after an onset
EQ_BAR_W, EQ_GAP = 2, 1      # each bar is two cells wide with one cell between bars
EQ_MIN_COLUMNS = 4
EQ_LABELS = ((40.0, "40"), (100.0, "100"), (1000.0, "1k"), (10000.0, "10k"), (16000.0, "16k"))
AUDIO_CHART_MIN_H = 6        # a level/tempo history chart needs at least this many rows


def _load_audio():
    """The audio module - or ImportError, because numpy is the audio extra, not a dependency."""
    from termstats import audio
    return audio


def _mic_source(device):
    from termstats import capture
    return capture.MicSource(device)


def _list_devices():
    from termstats import capture
    return capture.list_devices()


def _default_device():
    """The input `--device` would pick if it were not given - None when it cannot be read."""
    try:
        from termstats import capture
        return capture.resolve_device(None)
    except Exception:
        return None


DEVICE_MARK = T.DEVICE_MARK


def print_devices(devices, default=None):
    """`--list-devices`, in one place so the screenshot shows what the command prints."""
    if not devices:
        console.print("no input devices found", style=MUTED)
        return
    name_w = max(len(name) for _, name, _, _ in devices)
    for index, name, channels, rate in devices:
        row = Text(no_wrap=True, overflow="crop")
        row.append(f"{index:>3}  ", style=DIM)
        row.append(f"{name:<{name_w}}", style=TEXT if index == default else SOFT)
        row.append(f"   {channels} ch   {rate:>6.0f} Hz", style=MUTED)
        if index == default:
            row.append(f"   {DEVICE_MARK}", style=ramp(0.5))
        console.print(row)


def eq_columns(width, bands=28):
    """How many bars fit in `width` (two cells each plus a gap, inside the panel chrome).

    Never more than the bands there are; below that the bands are folded into the columns
    so a narrow terminal still shows the whole 40 Hz - 16 kHz range, just coarser."""
    _, cw = chrome()
    inner = max(0, width - cw)
    n = (inner + EQ_GAP) // (EQ_BAR_W + EQ_GAP)
    return max(EQ_MIN_COLUMNS, min(bands, n))


def _group_bands(values, n):
    """Fold `values` into n groups, each the maximum of its members."""
    values = list(values)
    k = len(values)
    if n >= k:
        return values
    out = []
    for g in range(n):
        lo, hi = (g * k) // n, max(((g + 1) * k) // n, (g * k) // n + 1)
        out.append(max(values[lo:hi]))
    return out


def _eighths(level, rows):
    """A 0..1 level as (full cells, eighths of the next cell) over `rows` cells."""
    cells = max(0.0, min(1.0, level)) * rows
    full = int(cells)
    part = int(round((cells - full) * 8))
    if part == 8:
        full, part = full + 1, 0
    return min(full, rows), part


def _db_pct(db):
    floor = _load_audio().DB_FLOOR
    return max(0.0, min(100.0, (db - floor) / -floor * 100.0))


def _shown_db(db):
    """The number to print for a dBFS value: the positive scale, see audio.SPL_OFFSET."""
    return _load_audio().spl(db)


def level_history(an):
    """The level history on the scale the chart is labelled in - the same shift the numbers
    get. Passing the raw dBFS here would plot every point below the axis floor: an empty
    chart under a correct-looking axis, which no test that reads text can see."""
    spl = _load_audio().spl
    return [(t, spl(v)) for t, v in an.db_history]


def big_digits(text):
    """`text` in the five-row font, as a list of equal-length strings of the bar glyph.

    The shapes live in theme.py; here they are only painted, so cli.py still names no glyph.
    """
    glyphs = [T.BIG_FONT.get(ch, T.BIG_FONT["?"]) for ch in str(text)]
    gap = " " * T.BIG_DIGIT_GAP
    rows = []
    for r in range(T.BIG_DIGIT_ROWS):
        row = gap.join(g[r] for g in glyphs)
        painted = row.replace("#", GLYPHS.bar_full * T.BIG_DIGIT_SCALE).replace(".", " " * T.BIG_DIGIT_SCALE)
        rows.append(painted)
    return rows


_readouts = {}               # key -> (value shown, when it was taken)


def readout(key, value, now):
    """The value the big font should show: refreshed on its own clock, held in between.

    In a snapshot there is nothing to hold - one frame, one number, always the current one.
    """
    if not SMOOTHING:
        return value
    shown_value, taken = _readouts.get(key, (None, None))
    if shown_value is None or now - taken >= AUDIO_READOUT_S:
        _readouts[key] = (value, now)
        return value
    return shown_value


def big_number(text, tone, width):
    """The big font, centred in `width`, in one colour - five Text rows."""
    rows = big_digits(text)
    pad = max(0, (width - len(rows[0])) // 2)
    return [Text(" " * pad + row, style=f"bold {tone}", no_wrap=True, overflow="crop") for row in rows]


def centred(text, width, style=MUTED):
    pad = max(0, (width - len(text)) // 2)
    return Text(" " * pad + text, style=style, no_wrap=True, overflow="crop")


def shown_level(db):
    """The dB value to draw BIG: eased in live mode, exact in a snapshot.

    The HUD keeps the raw sample either way, so the instantaneous value is always on screen
    and a redirected run still carries no interpolated number.
    """
    return _smoother.value("audio.db.readout", db) if SMOOTHING else db


def shown_tempo(bpm):
    """The tempo to draw BIG. Eased between two tempos - but a tempo that has just been
    found appears at once rather than counting up from nothing, and a lost one leaves at once."""
    if not bpm:
        _smoother.forget("audio.bpm.readout")
        return 0
    return _smoother.value("audio.bpm.readout", bpm) if SMOOTHING else bpm


def tempo_tone(an, now):
    """The colour of the big tempo: it flares on every detected beat, so the number itself
    keeps the pulse instead of only the little dot in the HUD."""
    return ramp(1.0) if an.beat_age(now) <= BEAT_LIT_S else ramp(0.75)


def audio_hud(an, now, width):
    """One line every microphone screen shares: beat dot, BPM, level, confidence."""
    lit = an.beat_age(now) <= BEAT_LIT_S
    t = Text(no_wrap=True, overflow="crop")
    t.append(GLYPHS.beat_on if lit else GLYPHS.beat_off, style=ramp(1.0) if lit else DIM)
    t.append("  BPM ", style=DIM)
    if an.bpm:
        t.append(f"{an.bpm:>3d}", style=f"bold {ramp(0.75)}")
    else:
        t.append("---", style=MUTED)
    t.append(f"  {GLYPHS.sep}  ", style=DIM)
    t.append(f"{_shown_db(an.db):6.1f}", style=f"bold {ramp(_db_pct(an.db) / 100.0)}")
    t.append(" dB", style=DIM)
    t.append(f"  {GLYPHS.sep}  conf ", style=DIM)
    t.append_text(bar(an.confidence * 100.0, 8))
    if not an.music:
        t.append("  quiet", style=MUTED)
    return t


def _band_of(edges, freq, bands):
    """Which band a frequency belongs to, CLAMPED at both ends.

    40 Hz and 16 kHz are the outermost edges themselves, and whether `edges[0] <= 40.0`
    holds depends on how the numpy at hand rounded `logspace` - 39.999999999999993 on one
    machine, 40.000000000000007 on the next. A plain search then finds no band for the
    label, falls back to the last one, and "40" is drawn on top of "16k" (which is how the
    frequency axis lost its labels on Linux while passing on macOS).
    """
    if not edges:
        return 0
    if freq <= edges[0]:
        return 0
    for i in range(bands):
        if edges[i] <= freq < edges[i + 1]:
            return i
    return bands - 1          # at or above the top edge, whichever way it rounded


def _eq_labels(an, columns, left, width):
    """The frequency axis under the bars: a few round numbers at the column they fall in."""
    edges = getattr(getattr(an, "spectrum", None), "edges", None)
    bands = len(an.levels)
    row = [" "] * max(width, 1)
    for freq, label in EQ_LABELS:
        if not edges:
            break
        k = _band_of(edges, freq, bands)
        col = min(columns - 1, (k * columns) // bands)
        x = left + col * (EQ_BAR_W + EQ_GAP)
        if x + len(label) > width or any(ch != " " for ch in row[max(0, x - 1):x + len(label) + 1]):
            continue
        row[x:x + len(label)] = list(label)
    return Text("".join(row).rstrip(), style=MUTED, no_wrap=True, overflow="crop")


def eq_body(an, now, width, rows):
    """The analyser: HUD, a spacer, the bars, the frequency axis - exactly `rows` lines."""
    n = eq_columns(width, len(an.levels))
    levels, peaks = _group_bands(an.levels, n), _group_bands(an.peaks, n)
    plot_h = max(1, rows - 3)
    span = n * EQ_BAR_W + (n - 1) * EQ_GAP
    left = max(0, (width - span) // 2)
    fulls = [_eighths(v, plot_h) for v in levels]
    peak_cells = [int(math.ceil(max(0.0, min(1.0, p)) * plot_h)) for p in peaks]
    lines = [audio_hud(an, now, width), Text("")]
    for r in range(plot_h):
        h = plot_h - r                                       # 1 = the bottom row
        tone = ramp((h - 0.5) / plot_h)
        line = Text(" " * left, no_wrap=True, overflow="crop")
        for c in range(n):
            full, part = fulls[c]
            if full >= h:
                glyph, style = GLYPHS.bar_full, tone
            elif full == h - 1 and part > 0 and GLYPHS.spark:
                glyph, style = GLYPHS.spark[part - 1], tone
            elif full == h - 1 and part >= 4:               # no partials (ASCII): round the top cell
                glyph, style = GLYPHS.bar_full, tone
            elif peak_cells[c] == h:                          # only reached above the bar: the branches
                glyph, style = GLYPHS.vpeak, tone              # before took every cell the bar owns
            else:
                glyph, style = " ", ""
            line.append(glyph * EQ_BAR_W, style=style)
            if c < n - 1:
                line.append(" " * EQ_GAP)
        lines.append(line)
    lines.append(_eq_labels(an, n, left, width))
    return Group(*lines[:rows])


def _history_series(history, now, window_s, points):
    """Bucket a (t, value) history over the last `window_s` seconds into `points` means."""
    recent = [(t, v) for t, v in history if now - t <= window_s]
    if len(recent) < 2 or points < 2:
        return None, 0.0
    t0 = recent[0][0]
    span = max(recent[-1][0] - t0, 1e-9)
    buckets = [[] for _ in range(points)]
    for t, v in recent:
        buckets[min(points - 1, int((t - t0) / span * points))].append(v)
    out, last = [], recent[0][1]
    for b in buckets:
        if b:
            last = sum(b) / len(b)
        out.append(last)
    return out, span


def _audio_chart(title, history, now, ylim, width, height, fill=True):
    """A history chart with its own title line, or None when there is nothing to draw."""
    window_s = min(60.0, max(1.0, now - history[0][0])) if history else 0.0
    values, span = _history_series(history, now, window_s, max(2, width - T.AXIS_W_PCT - 2))
    if values is None:
        return None
    label = Text(f"{title} {GLYPHS.sep} last {int(round(span))}s", style=MUTED, no_wrap=True, overflow="crop")
    chart = _render_chart([(values, title, ramp_rgb(0.5), fill)], ylim, width, height, axis_w=T.AXIS_W_PCT,
                          span_s=span)
    return Group(label, chart)


def db_body(an, now, width, rows):
    """The level meter: the number big and centred, a meter with its peak, extremes, history."""
    audio = _load_audio()
    pct = _db_pct(an.db)
    stats = Text(no_wrap=True, overflow="crop")
    stats.append("min ", style=DIM); stats.append(f"{_shown_db(an.db_min):.1f}", style=SOFT)
    stats.append("   max ", style=DIM); stats.append(f"{_shown_db(an.db_max):.1f}", style=SOFT)
    stats.append(f"   {GLYPHS.sep}   beats ", style=DIM); stats.append(f"{an.beats}", style=SOFT)
    lines = [audio_hud(an, now, width), Text("")]
    if rows >= T.BIG_DIGIT_MIN_ROWS:
        eased = readout("audio.db", shown_level(an.db), now)
        lines.extend(big_number(f"{_shown_db(eased):.1f}", ramp(_db_pct(eased) / 100.0), width))
        lines.append(centred(f"dB   {GLYPHS.sep}   smoothed {_shown_db(an.db_smooth):.1f}", width))
        lines.append(Text(""))
    else:
        one = Text(no_wrap=True, overflow="crop")
        one.append(f"{_shown_db(an.db):.1f}", style=f"bold {ramp(pct / 100.0)}")
        one.append(" dB", style=DIM)
        one.append(f"   {GLYPHS.sep}   smoothed {_shown_db(an.db_smooth):.1f}", style=MUTED)
        lines.append(one)
    lines.append(meter("level", pct, width, value=f"{_shown_db(an.db):6.1f}dB", value_w=8, unit_w=2,
                       fill=shown("audio.db", pct), peak=peak_of("audio.db", pct)))
    lines.append(stats)
    remaining = rows - len(lines) - 2
    if remaining >= AUDIO_CHART_MIN_H:
        chart = _audio_chart("level", level_history(an), now,
                             (audio.spl(audio.DB_FLOOR), audio.spl(0.0)), width, remaining)
        if chart is not None:
            lines.extend([Text(""), chart])
    return Group(*lines[:rows])


def bpm_body(an, now, width, rows):
    """The tempo screen: the number big and centred, flaring on the beat, then the meters."""
    audio = _load_audio()
    detail = f"BPM   {GLYPHS.sep}   {an.tempo.onset_rate():.1f} beats/s   {GLYPHS.sep}   {an.beats} beats"
    if not an.music:
        detail += f"   {GLYPHS.sep}   waiting for music"
    lines = [audio_hud(an, now, width), Text("")]
    if rows >= T.BIG_DIGIT_MIN_ROWS:
        eased = readout("audio.bpm", shown_tempo(an.bpm), now)
        lines.extend(big_number(f"{eased:.0f}" if eased else "---", tempo_tone(an, now), width))
        lines.append(centred(detail, width))
        lines.append(Text(""))
    else:
        lit = an.beat_age(now) <= BEAT_LIT_S
        one = Text(no_wrap=True, overflow="crop")
        one.append(GLYPHS.beat_on if lit else GLYPHS.beat_off, style=f"bold {ramp(1.0)}" if lit else DIM)
        one.append("  ")
        one.append(f"{an.bpm}" if an.bpm else "---", style=f"bold {ramp(0.75)}" if an.bpm else MUTED)
        one.append(" ", style=DIM)
        one.append(detail, style=MUTED)
        lines.append(one)
    lines.append(meter("confidence", an.confidence * 100.0, width, value=f"{an.confidence * 100.0:5.0f}%",
                       label_w=11, fill=shown("audio.conf", an.confidence * 100.0)))
    lines.append(meter("kick band", an.bass * 100.0, width, value=f"{an.bass * 100.0:5.0f}%", label_w=11,
                       fill=shown("audio.bass", an.bass * 100.0), peak=peak_of("audio.bass", an.bass * 100.0)))
    remaining = rows - len(lines) - 2
    tempo_hist = [(t, b) for t, b in an.bpm_history if b > 0]
    if remaining >= AUDIO_CHART_MIN_H and len(tempo_hist) >= 2:
        chart = _audio_chart("tempo", tempo_hist, now, (audio.BPM_MIN, audio.BPM_MAX), width, remaining, fill=False)
        if chart is not None:
            lines.extend([Text(""), chart])
    return Group(*lines[:rows])


_AUDIO_BODIES = {"eq": eq_body, "db": db_body, "bpm": bpm_body}
_AUDIO_TITLES = {"eq": ("equalizer", "bands from 40 Hz to 16 kHz"),
                 "db": ("level", "dB, uncalibrated estimate"),
                 "bpm": ("tempo", "beats per minute")}


def render_audio(mode, an, now, width=None, height=None):
    """A whole microphone frame: header with the mode badge, one panel, footer."""
    size = console.size
    tw = size.width if width is None else width
    th = size.height if height is None else height
    body_h = max(3, th - 2)
    ch, cw = chrome()
    title, subtitle = _AUDIO_TITLES[mode]
    if mode == "eq":
        subtitle = f"{len(an.levels)} {subtitle}"
    body = _AUDIO_BODIES[mode](an, now, max(10, tw - cw), max(1, body_h - ch))
    root = Layout()
    root.split_column(Layout(header_line(tw, badge=mode.upper()), name="head", size=1),
                      Layout(_panel(body, title, subtitle), name="main"),
                      Layout(footer_line(tw), name="foot", size=1))
    return root


def run_audio(mode, interval, source, once=False):
    """Drive a microphone screen from `source`: a MicSource (push, its own thread) or a
    DemoAudio (pull, its own clock). Ctrl+C ends a live session quietly with exit 0."""
    global LIVE, SMOOTHING
    audio = _load_audio()
    pull = hasattr(source, "read")
    an = audio.Analyzer(getattr(source, "samplerate", audio.SAMPLE_RATE), audio.BLOCK)
    lock = threading.Lock()
    clock = source.now if pull else time.monotonic

    def on_block(block):
        with lock:
            an.feed(block, time.monotonic())

    def pump(seconds):
        n = int(seconds * an.samplerate)
        while n > 0:
            an.feed(source.read(audio.BLOCK), source.now())
            n -= audio.BLOCK

    previous = None
    try:
        if pull:
            pump(AUDIO_DEMO_PREFILL_S)
        else:
            source.start(on_block)
        if once:
            if not pull:
                time.sleep(AUDIO_SNAPSHOT_S)
            with lock:
                console.print(render_audio(mode, an, clock()))
            return
        LIVE, SMOOTHING = True, True
        _smoother.reset()
        _peaks.reset()
        _readouts.clear()
        previous = _install_resize_handler()
        _resized.clear()
        _quit.clear()
        _keys.start()
        console.show_cursor(False)
        refresh = max(1, min(30, round(1 / interval)))
        with lock:
            first = render_audio(mode, an, clock())
        with Live(first, console=console, refresh_per_second=refresh, screen=True) as live:
            next_tick = time.monotonic()
            while True:
                next_tick, _ = _schedule_tick(next_tick, time.monotonic(), interval)
                if _sleep_until(next_tick):
                    if _quit.is_set():
                        break
                    next_tick = time.monotonic()
                if pull:
                    pump(interval)
                with lock:
                    frame = render_audio(mode, an, clock())
                live.update(frame, refresh=True)
    except KeyboardInterrupt:
        pass
    finally:
        _keys.stop()
        console.show_cursor(True)
        if previous is not None:
            _restore_resize_handler(previous)
        if not pull:
            source.stop()
        LIVE = False


# ---------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------

# Long options are accepted with one dash too ("-live"). The hand-rolled parser used to
# match only "-l"/"--live" and SILENTLY ignore everything else, so "termstats -live" ran a
# snapshot - which shows "Collecting data..." in both history panels and looks like live
# mode is broken. Unknown options are now an error, not a shrug.
_HELP_FLAGS = ("-h", "--help", "-help")
_VERSION_FLAGS = ("-V", "--version", "-version")
_LIVE_FLAGS = ("-l", "--live", "-live")
_ONCE_FLAGS = ("-1", "--once", "-once")
_INTERVAL_FLAGS = ("-i", "--interval", "-interval")
_THEME_FLAGS = ("-t", "--theme", "-theme")
_LIST_THEMES_FLAGS = ("--list-themes", "-list-themes")
_COMPACT_FLAGS = ("--compact", "-compact")
_NO_BORDER_FLAGS = ("--no-border", "-no-border")
_DEMO_FLAGS = ("--demo", "-demo")
_EQ_FLAGS = ("-eq", "--eq", "--equalizer", "-equalizer")
_BPM_FLAGS = ("-bpm", "--bpm")
_DB_FLAGS = ("-db", "--db")
_DEVICE_FLAGS = ("-d", "--device", "-device")
_LIST_DEVICES_FLAGS = ("--list-devices", "-list-devices")


def print_help():
    print(f"termstats v{__version__} - Beautiful terminal server dashboard")
    print()
    print("Usage: termstats [OPTIONS]")
    print()
    print("Runs the live dashboard by default. When stdout is not a terminal")
    print("(piped, redirected, cron) it prints a single snapshot instead, so")
    print("`termstats > report.txt` terminates rather than looping forever.")
    print()
    print("Options:")
    print(f"  -i, --interval N    Live refresh interval in seconds (default: {DEFAULT_INTERVAL:g})")
    print("  -1, --once          Force a single snapshot and exit")
    print("  -l, --live          Force the live dashboard, even when piped")
    print(f"  -t, --theme NAME    Colour theme: {', '.join(T.theme_names())}")
    print("      --list-themes   Show every theme with its ramp and exit")
    print("      --compact       No padding inside panels (narrow terminals)")
    print("      --no-border     Title rules instead of frames (screenshots, tiny terminals)")
    print("      --demo          Scripted, repeatable metrics instead of this machine's")
    print("  -eq, --equalizer    Live microphone spectrum: 28 bands, peak hold, BPM + dB")
    print("  -bpm, --bpm         Tempo detector: BPM, confidence, beat indicator")
    print("  -db, --db           Level meter: dB with peak, session min/max, history")
    print("  -d, --device NAME   Microphone to use (part of its name; see --list-devices)")
    print("      --list-devices  List the input devices and exit")
    print("  -V, --version       Show version")
    print("  -h, --help          Show this help")
    print()
    print("Long options also work with a single dash: -live, -once, -interval, -theme, -help")
    print()
    print(f"The microphone modes need the audio extra:  {AUDIO_HINT}")
    print()
    print("Environment:")
    print(f"  {T.THEME_ENV}=NAME     Default theme (the flag wins)")
    print("  TERMSTATS_GLYPHS=LEVEL   braille | block | ascii (default: detected)")
    print("  TERMSTATS_NERD_FONT=1    Icons in panel titles (needs a Nerd Font)")
    print("  NO_COLOR=1               No colour at all; TERM=dumb also drops to ASCII")
    print()
    print("Examples:")
    print("  termstats           Live dashboard (Esc or Ctrl+C to exit)")
    print("  termstats -i 2      Live, refresh every 2 seconds")
    print("  termstats --once    One snapshot, then exit")
    print("  termstats > out.txt One snapshot (stdout is not a terminal)")
    print("  termstats -eq       Spectrum analyser from the microphone (ts -eq with the alias)")
    print("  termstats --demo -bpm  The tempo screen on the scripted machine, no microphone")
    print()
    print("Module form:  python -m termstats [OPTIONS]")


# Windows hands a redirected stdout the legacy cp1252 codec, which can encode neither the
# block characters the bars are drawn with nor the beer in the header. "termstats > out.txt"
# therefore died with UnicodeEncodeError on Windows (found by CI on its first run, 1.1.4).
# A real console is fine - rich talks to it through the win32 API - so only widen a stream
# that demonstrably cannot carry the output.
def _ensure_console_encoding():
    for stream in (sys.stdout, sys.stderr):
        if _stream_can_draw(stream):
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _stdout_is_interactive():
    """True when a human is watching. Decides live-vs-snapshot when no mode was asked for.

    A bare `termstats` in a terminal should keep updating; the same command in a pipe,
    a cron job or a CI step must produce output and exit.
    """
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _fail(message):
    print(f"termstats: {message}", file=sys.stderr)
    print("Try 'termstats --help' for usage.", file=sys.stderr)
    sys.exit(2)


_RICH_COLOR_SYSTEM = {"truecolor": "truecolor", "256": "256", "16": "standard", "mono": None}


def configure_console():
    """Hand rich the colour depth the capabilities decided on.

    rich's own detection is good, but it does not know NO_COLOR from a request it should
    honour completely (it keeps bold and dim), and it cannot see TERMSTATS_GLYPHS at all.
    """
    global console
    console = Console(color_system=_RICH_COLOR_SYSTEM[CAPS.color],
                      force_terminal=None if CAPS.color != "mono" else False)


def print_themes():
    """`--list-themes`: every theme with its ramp, and how many bands survive quantisation."""
    swatch_w = 24
    console.print(f"[{MUTED}]{'theme':18s} ramp{' ' * (swatch_w - 4)}  256  16[/]")
    for name in T.theme_names():
        theme = T.resolve_theme(name)
        r = T.Ramp(theme.stops)
        swatch = Text(no_wrap=True)
        for i in range(swatch_w):
            swatch.append(GLYPHS.bar_full, style=r.hex(i / (swatch_w - 1)))
        line = Text(f"{name:18s}", style=TEXT if name == THEME.name else DIM)
        line.append_text(swatch)
        b256 = T.BandedRamp(r, "256").band_count
        b16 = T.BandedRamp(r, "16", theme.bands16).band_count
        line.append(f"  {b256:3d}  {b16:2d}", style=MUTED)
        console.print(line)


def main():
    _ensure_console_encoding()
    detect_capabilities()
    configure_console()
    args = sys.argv[1:]

    if any(a in _HELP_FLAGS for a in args):
        print_help()
        sys.exit(0)

    if any(a in _VERSION_FLAGS for a in args):
        print(f"termstats {__version__}")
        sys.exit(0)

    interval = DEFAULT_INTERVAL
    interval_given = False
    mode = None  # None = decide from the terminal
    theme_name = os.environ.get(T.THEME_ENV, "").strip() or None
    list_themes = list_devices = False
    compact = no_border = use_demo = False
    audio_mode = device = None

    def pick_audio(chosen):
        if audio_mode is not None and audio_mode != chosen:
            _fail(f"only one audio mode at a time: -eq, -bpm or -db (got -{audio_mode} and -{chosen})")
        return chosen

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _LIVE_FLAGS:
            if mode == "once":
                _fail("'--live' and '--once' are mutually exclusive")
            mode = "live"
        elif arg in _ONCE_FLAGS:
            if mode == "live":
                _fail("'--live' and '--once' are mutually exclusive")
            mode = "once"
        elif arg in _LIST_THEMES_FLAGS:
            list_themes = True
        elif arg in _LIST_DEVICES_FLAGS:
            list_devices = True
        elif arg in _COMPACT_FLAGS:
            compact = True
        elif arg in _NO_BORDER_FLAGS:
            no_border = True
        elif arg in _DEMO_FLAGS:
            use_demo = True
        elif arg in _EQ_FLAGS:
            audio_mode = pick_audio("eq")
        elif arg in _BPM_FLAGS:
            audio_mode = pick_audio("bpm")
        elif arg in _DB_FLAGS:
            audio_mode = pick_audio("db")
        elif arg in _DEVICE_FLAGS:
            if i + 1 >= len(args):
                _fail(f"option '{arg}' needs a device name (see --list-devices)")
            device = args[i + 1]
            i += 1
        elif arg in _THEME_FLAGS:
            if i + 1 >= len(args):
                _fail(f"option '{arg}' needs a theme name ({', '.join(T.theme_names())})")
            theme_name = args[i + 1]
            i += 1
        elif arg in _INTERVAL_FLAGS:
            if i + 1 >= len(args):
                _fail(f"option '{arg}' needs an interval in seconds")
            raw = args[i + 1]
            try:
                interval = float(raw)
            except ValueError:
                _fail(f"option '{arg}' needs a number, got '{raw}'")
            if not math.isfinite(interval) or interval <= 0:
                _fail(f"option '{arg}' needs a positive, finite number, got '{raw}'")
            interval_given = True
            i += 1
        else:
            _fail(f"unknown option '{arg}'")
        i += 1

    if theme_name is not None and theme_name not in T.THEMES:
        _fail(f"unknown theme '{theme_name}' - choose one of: {', '.join(T.theme_names())}")
    set_theme(theme_name)
    set_frame(compact=compact, no_border=no_border)
    set_demo(demo.DemoSource(demo.DEFAULT_SEED, interval) if use_demo else None)

    if list_themes:
        print_themes()
        sys.exit(0)

    if list_devices:
        try:
            devices = _list_devices()
        except ImportError as exc:
            _fail(f"the microphone modes need the audio extra: {AUDIO_HINT} ({exc})")
        except Exception as exc:              # capture.AudioUnavailable, without importing it here
            _fail(str(exc))
        print_devices(devices, _default_device())
        sys.exit(0)

    if device is not None and audio_mode is None:
        _fail("'--device' only makes sense with -eq, -bpm or -db")

    if mode is None:
        mode = "live" if _stdout_is_interactive() else "once"

    if audio_mode is not None:
        try:
            audio = _load_audio()
        except ImportError as exc:
            _fail(f"the microphone modes need the audio extra: {AUDIO_HINT} ({exc})")
        if use_demo:
            source = audio.DemoAudio(demo.DEFAULT_SEED)
        else:
            try:
                source = _mic_source(device)
            except Exception as exc:          # capture.AudioUnavailable: library, PortAudio or device
                _fail(str(exc))
        run_audio(audio_mode, interval if interval_given else AUDIO_INTERVAL, source, once=(mode == "once"))
        return

    if mode == "live":
        run_live(interval)          # Ctrl+C ends it quietly with exit 0 - see run_live
    else:
        try:
            run_once()
        except KeyboardInterrupt:
            # An interrupted snapshot is no snapshot: quiet, but not a success either -
            # 130 is what a shell reports for a SIGINT-terminated command.
            sys.exit(130)

if __name__ == "__main__":
    main()
