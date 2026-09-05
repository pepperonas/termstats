#!/usr/bin/env python3
"""
termstats - Beautiful terminal server dashboard with real-time charts.

Cross-platform system monitoring: CPU, RAM, Swap, Disk, Network,
Top Processes, and live history graphs - all in your terminal.
"""

import math
import os
import platform
import sys
import time
import shutil
import psutil
import plotext as plt
from collections import deque
from rich import box
from rich.console import Console, Group
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live

from termstats import __version__
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
          note_w=None, fill=None, peak=None):
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
    text.append(f"{value:>{value_w}}", style=f"bold {ramp(occupied / 100)}")
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
        dt = time.time() - get_disk_section._last_time
        if dt > 0:
            read_s = (io.read_bytes - get_disk_section._last_io.read_bytes) / dt
            write_s = (io.write_bytes - get_disk_section._last_io.write_bytes) / dt
    get_disk_section._last_io = io
    get_disk_section._last_time = time.time()
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
        dt = time.time() - get_network_section._last_time
        sent_s = (net.bytes_sent - get_network_section._last.bytes_sent) / dt if dt > 0 else 0
        recv_s = (net.bytes_recv - get_network_section._last.bytes_recv) / dt if dt > 0 else 0
    else:
        sent_s = recv_s = 0
    get_network_section._last = net
    get_network_section._last_time = time.time()

    try:
        conns = len(psutil.net_connections())
    except psutil.AccessDenied:
        conns = -1

    peak = max(list(net_sent_history) + list(net_recv_history) + [sent_s, recv_s, 1.0])
    tx_pct, rx_pct = 100 * sent_s / peak, 100 * recv_s / peak
    rows = [
        meter("tx", tx_pct, width, value=T.fmt_rate(sent_s), value_w=T.RATE_W,
              note=f"{GLYPHS.sigma} {T.fmt_gb(net.bytes_sent)}", note_w=T.NOTE_TOTAL_W,
              fill=shown("net.tx", tx_pct), peak=peak_of("net.tx", tx_pct)),
        meter("rx", rx_pct, width, value=T.fmt_rate(recv_s), value_w=T.RATE_W,
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


def _time_ticks(n):
    """x-axis labels in seconds-ago, which is what the axis actually measures.

    plotext labels the x axis with sample indices by default (1.0, 15.8, 30.5, ...) - a
    number nobody reading a live dashboard has any use for.
    """
    span = HISTORY_LEN * sample_interval
    positions = [0, n // 2, max(n - 1, 0)]
    labels = [f"-{span:.0f}s", f"-{span / 2:.0f}s", "now"]
    return positions, labels


def _ascii_chart(values, ylim, width, height):
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
    for r in range(plot_h):
        top = (plot_h - r) / plot_h
        bottom = (plot_h - r - 1) / plot_h
        label = f"{hi:.0f}" if r == 0 else (f"{lo:.0f}" if r == plot_h - 1 else "")
        line = Text(f"{label:>{axis_w - 1}} ", style=MUTED, no_wrap=True, overflow="crop")
        for t in levels:
            if t >= top:
                line.append(GLYPHS.chart_full, style=ramp(t))
            elif t > bottom:
                line.append(GLYPHS.chart_half, style=ramp(t))
            else:
                line.append(" ")
        rows.append(line)
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


def _render_chart(series, ylim, width, height, axis_w=None):
    """Build one plotext chart. Never raises - a broken chart is a note, not a crash.

    axis_w fixes the y-label field per CHART, not per value: a rate chart whose top
    happens to be 100 must not narrow its axis and shift the plot two columns left.
    series: list of (values, label, color[, fill]); color may be a name or an RGB tuple.
    A filled series is drawn as an area under the line - the mass reads far better than a
    thin braille trace, but two overlapping fills turn to mud where they cross, so callers
    fill at most one series and draw the others as lines over it.
    """
    if GLYPHS.chart_marker is None:
        return _ascii_chart(series[0][0], ylim, width, height)
    if not _PLOTEXT_5:
        return Text(_CHART_NEEDS_PLOTEXT_5, style=MUTED)
    try:
        plt.clear_figure()
        for entry in series:
            values, _label, color = entry[:3]
            fill = bool(entry[3]) if len(entry) > 3 else False
            # braille packs 2x4 dots into one cell - four times the vertical resolution of
            # the block markers, and the same trick btop uses. "clear" keeps plotext from
            # painting its own black background over the terminal's.
            plt.plot(values, marker=GLYPHS.chart_marker, color=color, fillx=fill)
        if ylim is None:
            top = nice_ceiling(max((max(e[0]) for e in series if e[0]), default=1.0))
            ylim = (0, top)
        lo, hi = ylim
        plt.ylim(lo, hi)
        # Labels in a fixed field: plotext sizes the axis column to the widest label, so a
        # "600" that becomes "1000" would shift the whole plot one column to the right.
        if axis_w is None:
            axis_w = T.AXIS_W_PCT if hi <= 100 and lo >= 0 and hi - lo <= 100 else T.AXIS_W_RATE
        plt.yticks([lo, (lo + hi) / 2, hi],
                   [T.fmt_axis(v, axis_w, top=hi) for v in (lo, (lo + hi) / 2, hi)])
        plt.theme("clear")
        plt.plotsize(width, height)
        positions, labels = _time_ticks(len(series[0][0]))
        plt.xticks(positions, labels)
        return Text.from_ansi(plt.build(), no_wrap=True, overflow="crop")
    except Exception:
        return Text("  Chart unavailable", style=MUTED)


def _collecting():
    return Text(f"  Collecting data{GLYPHS.ellipsis if GLYPHS.name != 'ascii' else '...'}", style=MUTED)


def cpu_chart_colour():
    """The area is tinted by the MEAN load of the window - the same position on the ramp
    the meters use, so a chart that has gone amber says the same thing an amber bar does."""
    if not cpu_history:
        return ramp_rgb(0.0)
    return ramp_rgb(sum(cpu_history) / len(cpu_history) / 100.0)


def get_cpu_chart(width, height):
    if len(cpu_history) < 2:
        return _collecting()
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
        return _collecting()
    div, _unit = net_scale()
    series = [
        ([x / div for x in net_recv_history], "RX", NET_RX_RGB, True),
        ([x / div for x in net_sent_history], "TX", NET_TX_RGB, False),
    ]
    return _render_chart(series, None, width, height, axis_w=T.AXIS_W_RATE)


# ---------------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------------

def _panel(renderable, title, colour, subtitle=""):
    head = f"[b]{title}[/b]"
    if subtitle:
        head += f" [{MUTED}]{_sep()} {subtitle}[/{MUTED}]"
    return Panel(renderable, title=head, title_align="left", border_style=colour,
                 box=getattr(box, GLYPHS.box), padding=T.PANEL_PADDING)


def _sep():
    """The dot that joins subtitle parts - and the one glyph that leaked into ASCII mode."""
    return GLYPHS.sep


def cpu_chart_subtitle():
    """`last 30s · 42%` - the window, then the value the newest sample carries."""
    now = cpu_history[-1] if cpu_history else 0.0
    return f"{_window_label()} {_sep()} [{ramp(now / 100)}]{min(now, 999):3.0f}%[/]"


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


def header_line(width):
    load1, load5, load15 = psutil.getloadavg()
    ncpu = psutil.cpu_count() or 1
    uptime_s = time.time() - psutil.boot_time()
    os_name = {"Linux": "Linux", "Darwin": "macOS", "Windows": "Windows"}.get(
        platform.system(), platform.system())

    # Every field has a fixed width. The load can gain a digit, the uptime a day, the
    # process count a thousand - none of it may move the fields to its right.
    text = Text(no_wrap=True, overflow="crop")
    text.append(" TERMSTATS ", style=f"bold {THEME.wordmark_fg} on {THEME.wordmark_bg}")
    text.append(f"  {platform.node()[:12]:12s}", style=f"bold {THEME.wordmark_fg}")
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
    clock.append(time.strftime("%H:%M:%S") + "  ", style=TEXT)
    clock.append(f"{sample_interval:>4g}s  ", style=MUTED)
    clock.append(f"v{__version__} ", style=FAINT)

    full = Text(no_wrap=True)
    # A SPARK_W-cell CPU sparkline, tmux-status-bar style: the whole recent history in one
    # glance without looking down at the chart. Peaks per cell, tinted by the ramp - and
    # always the same width, so the clock never drifts while the history fills.
    spark = sparkline(cpu_history, T.SPARK_W)
    if not spark.cell_len:
        for _ in range(T.SPARK_W):
            spark.append(SPARK[0] if SPARK else " ", style=TRACK)
    full.append_text(spark)
    full.append("  ")
    full.append_text(clock)

    for tail in (full, clock):
        pad = width - text.cell_len - tail.cell_len
        if pad >= 2:
            text.append(" " * pad)
            text.append_text(tail)
            break
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

    narrow = tw < 92
    right_w = 0 if narrow else max(36, min(tw * 2 // 5, 52))
    left_w = tw - right_w

    # --- collect (widths are panel interiors: minus border and padding) --------------
    cpu_body_w = (left_w if not narrow else tw) - 4
    right_body_w = (right_w if not narrow else tw) - 4

    mem_h = memory_section_rows() + 2
    net_h = network_section_rows() + 2
    disk_h = disk_section_rows() + 2

    # Height is decided before the columns are, not after: the CPU panel would rather be
    # one tall column of wide bars than two short ones with a hole underneath, so first
    # work out how tall the row wants to be, then fit the cores into it.
    ncores = psutil.cpu_count() or 1
    extra_rows = 1 + (1 if IS_LINUX else 0)          # TOTAL, plus steal on Linux
    cpu_wanted = min(ncores + extra_rows + 2, max(8, th // 2))

    # A short CPU panel next to a tall stack is exactly the hole this rewrite exists to
    # remove. When that happens the disk panel leaves the stack and takes the full width -
    # same total height, no dead space.
    disk_in_stack = cpu_wanted + 2 >= mem_h + net_h + disk_h
    stack_h = mem_h + net_h + (disk_h if disk_in_stack else 0)

    if narrow:
        # One column: the panels are stacked, so they compete for the same lines the
        # side-by-side layout would have given them for free. Take only what fits, whole.
        # (A cap on cpu_wanted itself was tried here and removed: 0 differences across
        # 13,608 geometries, because the section-drop loop below already covers it.)
        room = th - 1 - cpu_wanted
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

    core_rows = max(top_h - 2 - extra_rows, 1)
    cpu_body, cpu_total, steal_pct = get_cpu_section(cpu_body_w, max_rows=core_rows)
    mem_body = get_memory_section(right_body_w)
    net_body, sent_s, recv_s = get_network_section(right_body_w)
    disk_body = get_disk_section(right_body_w if disk_in_stack else tw - 4)

    cpu_history.append(cpu_total)
    steal_history.append(steal_pct)
    net_sent_history.append(sent_s)
    net_recv_history.append(recv_s)
    _smoother.end_frame()
    _peaks.end_frame()

    # --- height budget ---------------------------------------------------------------
    remaining = th - 1 - top_h - (0 if disk_in_stack else disk_h)
    charts_h = 0
    if remaining >= 13:
        charts_h = min(12, remaining - 5)
    proc_h = remaining - charts_h

    sections = [Layout(header_line(tw), name="head", size=1)]

    stack_panels = [(_panel(mem_body, "memory", THEME.panel("memory")), mem_h),
                    (_panel(net_body, "network", THEME.panel("network")), net_h)]
    if disk_in_stack:
        stack_panels.append((_panel(disk_body, "disk", THEME.panel("disk")), disk_h))
    stack_panels = stack_panels[:kept_stack]

    cpu_panel = _panel(cpu_body, "cpu", THEME.panel("cpu"))
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
        sections.append(Layout(_panel(disk_body, "disk", THEME.panel("disk")), name="disk", size=disk_h))

    if charts_h:
        chart_w = (tw - 1) // 2 - 4
        chart_h = charts_h - 2
        charts = Layout(name="charts", size=charts_h)
        charts.split_row(
            Layout(_panel(get_cpu_chart(chart_w, chart_h), "cpu", THEME.panel("cpu"),
                          cpu_chart_subtitle()), name="c1"),
            Layout(_panel(get_net_chart(chart_w, chart_h), "network", THEME.panel("network"),
                          net_chart_subtitle()), name="c2"),
        )
        sections.append(charts)

    # A panel needs its two borders, a header row and at least two rows of content before
    # it is worth drawing; below that it is a stump, and a stump is worse than the space.
    if proc_h >= 5:
        procs = get_top_processes(tw - 4, n=max(proc_h - 3, 1))
        sections.append(Layout(_panel(procs, "processes", THEME.panel("processes"), "by cpu"), name="proc"))

    # ⚠️ The budget above is a plan, not a guarantee: a narrow terminal, a machine with
    # several mountpoints or Linux's extra steal row can all push the fixed sizes past the
    # height available. Drop sections from the bottom until the plan actually fits -
    # otherwise the trailing ratio section is squeezed to two lines and renders as a
    # bordered stump. (Found by CI on Linux, where the steal row tips the balance.)
    while len(sections) > 2 and sum(s.size or 0 for s in sections) > th:
        sections.pop()

    # Whatever section ends up last takes the remaining height instead of a fixed size, so
    # a dropped panel leaves its lines to its neighbour rather than to a blank strip.
    sections[-1].size = None
    sections[-1].ratio = 1

    root = Layout()
    root.split_column(*sections)
    return root


# ---------------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------------

def _prime_measurements():
    psutil.cpu_percent(percpu=True)
    psutil.cpu_percent()
    for p in psutil.process_iter(['cpu_percent']):
        try:
            p.cpu_percent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.net_io_counters()
    try:
        psutil.disk_io_counters()
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


def run_once():
    """One snapshot, then exit.

    Always samples for SNAPSHOT_SAMPLE_S regardless of --interval: rates need a gap
    between two reads, and --interval is the *live refresh rate*, not a sampling window.
    Never smoothed: a report carries raw samples only.
    """
    global sample_interval, SMOOTHING
    sample_interval = SNAPSHOT_SAMPLE_S
    SMOOTHING = False
    _prime_measurements()
    time.sleep(SNAPSHOT_SAMPLE_S)
    console.print(render_dashboard())


def run_live(interval=DEFAULT_INTERVAL):
    global sample_interval, SMOOTHING
    sample_interval = interval
    SMOOTHING = True
    _smoother.reset()
    _peaks.reset()
    _prime_measurements()
    time.sleep(min(interval, 0.5))
    refresh = max(1, min(10, round(1 / interval)))
    try:
        with Live(render_dashboard(), console=console, refresh_per_second=refresh, screen=True) as live:
            next_tick = time.monotonic()
            while True:
                next_tick, delay = _schedule_tick(next_tick, time.monotonic(), interval)
                time.sleep(delay)
                live.update(render_dashboard(), refresh=True)
    except KeyboardInterrupt:
        pass


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
    print("  -V, --version       Show version")
    print("  -h, --help          Show this help")
    print()
    print("Long options also work with a single dash: -live, -once, -interval, -theme, -help")
    print()
    print("Environment:")
    print(f"  {T.THEME_ENV}=NAME      Default theme (the flag wins)")
    print("  TERMSTATS_GLYPHS=LEVEL   braille | block | ascii (default: detected)")
    print("  TERMSTATS_NERD_FONT=1    Icons in panel titles (needs a Nerd Font)")
    print("  NO_COLOR=1               No colour at all; TERM=dumb also drops to ASCII")
    print()
    print("Examples:")
    print("  termstats           Live dashboard (Ctrl+C to exit)")
    print("  termstats -i 2      Live, refresh every 2 seconds")
    print("  termstats --once    One snapshot, then exit")
    print("  termstats > out.txt One snapshot (stdout is not a terminal)")
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
    mode = None  # None = decide from the terminal
    theme_name = os.environ.get(T.THEME_ENV, "").strip() or None
    list_themes = False

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
            i += 1
        else:
            _fail(f"unknown option '{arg}'")
        i += 1

    if theme_name is not None and theme_name not in T.THEMES:
        _fail(f"unknown theme '{theme_name}' - choose one of: {', '.join(T.theme_names())}")
    set_theme(theme_name)

    if list_themes:
        print_themes()
        sys.exit(0)

    if mode is None:
        mode = "live" if _stdout_is_interactive() else "once"

    if mode == "live":
        run_live(interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
