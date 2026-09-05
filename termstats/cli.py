#!/usr/bin/env python3
"""
termstats - Beautiful terminal server dashboard with real-time charts.

Cross-platform system monitoring: CPU, RAM, Swap, Disk, Network,
Top Processes, and live history graphs - all in your terminal.
"""

import math
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

# Every non-ASCII character the dashboard draws with. Add to this when you add a glyph.
_GLYPH_PROBE = "█░╭\U0001f37b▏╌⠀▁▂▃▄▅▆▇━"

UNICODE = True


def _stream_can_draw(stream):
    try:
        _GLYPH_PROBE.encode(stream.encoding or "")
    except (AttributeError, LookupError, TypeError, UnicodeEncodeError):
        return False
    return True


def detect_capabilities():
    """Decide once whether the drawing glyphs are safe on this stdout."""
    global UNICODE
    UNICODE = _stream_can_draw(sys.stdout)
    return UNICODE


# ---------------------------------------------------------------------------------
# One colour ramp for everything
#
# btop's design rule, and the reason it reads as one instrument rather than a pile of
# widgets: every meter, graph and value maps onto the SAME three stops. Cool and idle at
# the bottom, warm in the middle, hot and saturated at the top.
# ---------------------------------------------------------------------------------

RAMP = (
    (0.00, (0x5A, 0xD8, 0xC8)),   # teal   - idle
    (0.55, (0xF0, 0xBE, 0x5A)),   # amber  - working
    (1.00, (0xF0, 0x6E, 0x78)),   # rose   - saturated
)

MUTED = "grey42"
DIM = "grey54"
FAINT = "grey30"


def ramp_rgb(t):
    """Colour at position t (0..1) on the shared ramp, as an (r, g, b) tuple."""
    t = 0.0 if t != t else max(0.0, min(1.0, t))          # t != t catches NaN
    for (lo, c_lo), (hi, c_hi) in zip(RAMP, RAMP[1:]):
        if t <= hi:
            k = 0.0 if hi == lo else (t - lo) / (hi - lo)
            return tuple(round(c_lo[i] + (c_hi[i] - c_lo[i]) * k) for i in range(3))
    return RAMP[-1][1]


def ramp(t):
    """Colour at position t (0..1) on the shared ramp, as a hex string.

    Returned as truecolor; rich quantises it to 256 or 16 colours on terminals that
    need it, so there is deliberately no palette table here.
    """
    r, g, b = ramp_rgb(t)
    return f"#{r:02x}{g:02x}{b:02x}"


def dim_rgb(rgb, factor=0.45):
    """The same hue at a fraction of the brightness - for a secondary segment that must
    read as related to the primary but clearly not the same thing."""
    return f"#{round(rgb[0] * factor):02x}{round(rgb[1] * factor):02x}{round(rgb[2] * factor):02x}"


# ---------------------------------------------------------------------------------
# Meters
# ---------------------------------------------------------------------------------

BAR_FULL = "█"
BAR_PARTIALS = "▏▎▍▌▋▊▉"   # 1/8 .. 7/8 of a cell
BAR_EMPTY = "╌"
ASCII_FULL = "#"
ASCII_EMPTY = "-"


BAR_SECONDARY = "▒"
ASCII_SECONDARY = "="


def bar(pct, width, secondary=0.0):
    """A gradient meter, accurate to an eighth of a character cell.

    Each cell is tinted by its own position on the ramp rather than the bar carrying one
    flat colour, which is what makes a long bar read as a scale instead of a block.

    `secondary` is a second percentage drawn after the first in a dimmed tone - used for
    the memory the kernel holds as cache: not free, not the process's, and worth seeing.
    """
    full_ch = BAR_FULL if UNICODE else ASCII_FULL
    empty_ch = BAR_EMPTY if UNICODE else ASCII_EMPTY
    second_ch = BAR_SECONDARY if UNICODE else ASCII_SECONDARY
    partials = BAR_PARTIALS if UNICODE else ""

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
    text.append(empty_ch * (width - filled), style=FAINT)
    return text


MIN_BAR_W = 6


def meter(label, pct, total, value=None, note="", label_w=9, value_w=7, secondary=0.0):
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
        value = f"{occupied:.1f}%"
    if note and total - label_w - value_w - (len(note) + 2) < MIN_BAR_W:
        note = ""
    note_w = len(note) + 2 if note else 0
    bar_w = max(total - label_w - value_w - note_w, 3)

    text = Text(no_wrap=True, overflow="crop")
    text.append(f"{label[:label_w - 1]:>{label_w - 1}} ", style=DIM)
    text.append_text(bar(pct, bar_w, secondary))
    text.append(f"{value:>{value_w}}", style=f"bold {ramp(occupied / 100)}")
    if note:
        text.append(f"  {note}", style=MUTED)
    return text


SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values, width):
    """A width-cell block sparkline, each cell the PEAK of its slice, tinted by the ramp.

    Peaks rather than means: a sparkline exists to show that something spiked, and a mean
    over four samples flattens exactly the sample you wanted to see.
    """
    text = Text(no_wrap=True, overflow="crop")
    if not values or width <= 0 or not UNICODE:
        return text
    values = list(values)
    step = max(1, math.ceil(len(values) / width))
    for i in range(0, len(values), step):
        peak = max(values[i:i + step])
        t = max(0.0, min(1.0, peak / 100.0))
        text.append(SPARK[min(int(t * len(SPARK)), len(SPARK) - 1)], style=ramp(t))
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
        text.append(BAR_FULL if UNICODE else ASCII_FULL, style=ramp(avg / 100))
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
        rows = [meter(f"cpu{i}", p, width) for i, p in enumerate(percents)]
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
                cells.append(meter(f"cpu{i}", percents[i], col_w) if i < len(percents) else Text(""))
            grid.add_row(*cells)
        rows = [grid]

    rows.append(meter("TOTAL", total, width))
    if IS_LINUX:
        rows.append(meter("steal", steal_pct, width))
    return Group(*rows), total, steal_pct


def cpu_section_rows(ncores, max_rows=99):
    """Height in lines that get_cpu_section() will occupy for this core count."""
    cols = core_columns(ncores, max_rows)
    core_rows = 1 if cols == 0 else (ncores if cols == 1 else math.ceil(ncores / cols))
    return core_rows + 1 + (1 if IS_LINUX else 0)


def _fmt_gb(n):
    return f"{n / 1024**3:.1f}G"


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
    note = f"{_fmt_gb(mem.used)}/{_fmt_gb(mem.total)}"
    if cache and width >= 44:
        note += f" +{_fmt_gb(cache)} cache"
    rows = [meter("ram", used_pct, width, value=f"{mem.percent:.1f}%", note=note,
                  secondary=cache_pct)]
    swap = psutil.swap_memory()
    if swap.total > 0:
        rows.append(meter("swap", swap.percent, width,
                          note=f"{swap.used / 1024**3:.1f}G/{swap.total / 1024**3:.1f}G"))
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


DISK_LABEL_W = 12


def short_mount(label, width=DISK_LABEL_W):
    """Shorten a mountpoint from the left, keeping the part that identifies it.

    Cutting blindly turns "/Volumes/Untitled" into "…umes/Un", which names nothing. The
    last path component is the part a human recognises.
    """
    if len(label) <= width:
        return label
    ellipsis = "…" if UNICODE else "~"
    tail = label.rstrip("/").rsplit("/", 1)[-1]
    if tail and len(tail) + 1 <= width:
        return ellipsis + tail
    return ellipsis + label[-(width - 1):]


def get_disk_section(width):
    rows = []
    for label, usage in disk_entries():
        rows.append(meter(short_mount(label), usage.percent, width, label_w=DISK_LABEL_W + 1,
                          note=f"{usage.used / 1024**3:.1f}G/{usage.total / 1024**3:.1f}G"))
    io_line = _disk_io_line()
    if io_line is not None:
        rows.append(io_line)
    if not rows:
        return Group(Text("  No disks found", style=MUTED))
    return Group(*rows)


def _disk_io_line():
    try:
        io = psutil.disk_io_counters()
    except Exception:
        return None
    if not io:
        return None
    line = None
    if hasattr(get_disk_section, "_last_io"):
        dt = time.time() - get_disk_section._last_time
        if dt > 0:
            read_s = (io.read_bytes - get_disk_section._last_io.read_bytes) / dt
            write_s = (io.write_bytes - get_disk_section._last_io.write_bytes) / dt
            line = Text(no_wrap=True, overflow="crop")
            line.append(f"{'io':>{DISK_LABEL_W}} ", style=DIM)
            line.append(f"read {_fmt_bytes_rate(read_s):>10}", style=MUTED)
            line.append(f"    write {_fmt_bytes_rate(write_s):>10}", style=MUTED)
    get_disk_section._last_io = io
    get_disk_section._last_time = time.time()
    return line


def disk_section_rows():
    return max(len(disk_entries()), 1) + (1 if hasattr(get_disk_section, "_last_io") else 0)


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
    rows = [
        meter("tx", 100 * sent_s / peak, width, value=_fmt_bytes_rate(sent_s).replace(" ", ""),
              note=f"Σ {net.bytes_sent / 1024**3:.2f}G" if UNICODE else f"tot {net.bytes_sent / 1024**3:.2f}G"),
        meter("rx", 100 * recv_s / peak, width, value=_fmt_bytes_rate(recv_s).replace(" ", ""),
              note=f"Σ {net.bytes_recv / 1024**3:.2f}G" if UNICODE else f"tot {net.bytes_recv / 1024**3:.2f}G"),
    ]
    if conns >= 0:
        line = Text(no_wrap=True, overflow="crop")
        line.append("    conns ", style=DIM)
        line.append(str(conns), style="grey70")
        rows.append(line)
    return Group(*rows), sent_s, recv_s


def network_section_rows():
    try:
        psutil.net_connections()
        return 3
    except psutil.AccessDenied:
        return 2


def _fmt_mem(b):
    """RSS as 482M or 1.2G - a 1234M column is harder to scan than a 1.2G one."""
    if b >= 1024**3:
        return f"{b / 1024**3:.1f}G"
    return f"{b / 1024**2:.0f}M"


def get_top_processes(width, n=8):
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'memory_info']):
        try:
            info = p.info
            if info['cpu_percent'] is not None:
                procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda x: x['cpu_percent'] or 0, reverse=True)

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
            cells.append(bar(cpu_pct, bar_w - 1))
        cells += [
            Text(f"{cpu_pct:.1f}", style=f"bold {ramp(cpu_pct / 100)}"),
            Text(f"{mem_pct:.1f}", style=ramp(mem_pct / 25)),
            Text(_fmt_mem(rss), style="grey62"),
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
                line.append("#", style=ramp(t))
            elif t > bottom:
                line.append("=", style=ramp(t))
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


def _render_chart(series, ylim, width, height):
    """Build one plotext chart. Never raises - a broken chart is a note, not a crash.

    series: list of (values, label, color[, fill]); color may be a name or an RGB tuple.
    A filled series is drawn as an area under the line - the mass reads far better than a
    thin braille trace, but two overlapping fills turn to mud where they cross, so callers
    fill at most one series and draw the others as lines over it.
    """
    if not UNICODE:
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
            plt.plot(values, marker="braille", color=color, fillx=fill)
        if ylim is None:
            top = nice_ceiling(max((max(e[0]) for e in series if e[0]), default=1.0))
            ylim = (0, top)
        lo, hi = ylim
        plt.ylim(lo, hi)
        plt.yticks([lo, (lo + hi) / 2, hi], [f"{lo:g}", f"{(lo + hi) / 2:g}", f"{hi:g}"])
        plt.theme("clear")
        plt.plotsize(width, height)
        positions, labels = _time_ticks(len(series[0][0]))
        plt.xticks(positions, labels)
        return Text.from_ansi(plt.build(), no_wrap=True, overflow="crop")
    except Exception:
        return Text("  Chart unavailable", style=MUTED)


def _collecting():
    return Text("  Collecting data…" if UNICODE else "  Collecting data...", style=MUTED)


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
    return _render_chart(series, (0, 100), width, height)


NET_RX_RGB = ramp_rgb(0.0)     # teal  - the filled series
NET_TX_RGB = ramp_rgb(0.55)    # amber - the line drawn over it


def net_scale():
    """(divisor, unit) so the network chart reads in KB/s or MB/s, whichever fits the peak."""
    peak = max(list(net_sent_history) + list(net_recv_history) + [0.0])
    if peak >= 2 * 1024**2:
        return 1024**2, "MB/s"
    return 1024, "KB/s"


def get_net_chart(width, height):
    if len(net_sent_history) < 2:
        return _collecting()
    div, _unit = net_scale()
    series = [
        ([x / div for x in net_recv_history], "RX", NET_RX_RGB, True),
        ([x / div for x in net_sent_history], "TX", NET_TX_RGB, False),
    ]
    return _render_chart(series, None, width, height)


# ---------------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------------

def _panel(renderable, title, colour, subtitle=""):
    head = f"[b]{title}[/b]"
    if subtitle:
        head += f" [{MUTED}]{_sep()} {subtitle}[/{MUTED}]"
    return Panel(renderable, title=head, title_align="left", border_style=colour,
                 box=box.ROUNDED if UNICODE else box.ASCII, padding=(0, 1))


C_CPU, C_MEM, C_NET, C_DISK, C_PROC = "#4a6fa5", "#4a9575", "#4a6fa5", "#a5904a", "#7a5a95"


def _sep():
    """The dot that joins subtitle parts - and the one glyph that leaked into ASCII mode."""
    return "·" if UNICODE else "-"


def cpu_chart_subtitle():
    """`last 30s · 42%` - the window, then the value the newest sample carries."""
    now = cpu_history[-1] if cpu_history else 0.0
    return f"{_window_label()} {_sep()} [{ramp(now / 100)}]{now:.0f}%[/]"


def net_chart_subtitle():
    """A legend that also states the current rates: `▇ rx 3.1MB/s  ━ tx 1.2MB/s · KB/s`.

    The filled series gets the block glyph and the line series the bar, so the legend
    shows what the reader will see rather than naming colours.
    """
    _div, unit = net_scale()
    rx = _fmt_bytes_rate(net_recv_history[-1] if net_recv_history else 0.0).replace(" ", "")
    tx = _fmt_bytes_rate(net_sent_history[-1] if net_sent_history else 0.0).replace(" ", "")
    rx_glyph, tx_glyph = ("▇", "━") if UNICODE else ("#", "-")
    rx_hex = "#%02x%02x%02x" % NET_RX_RGB
    tx_hex = "#%02x%02x%02x" % NET_TX_RGB
    return f"[{rx_hex}]{rx_glyph} rx[/] {rx}  [{tx_hex}]{tx_glyph} tx[/] {tx} {_sep()} {unit}"


def header_line(width):
    load1, load5, load15 = psutil.getloadavg()
    ncpu = psutil.cpu_count() or 1
    uptime_s = time.time() - psutil.boot_time()
    days, rest = int(uptime_s // 86400), uptime_s % 86400
    up = f"{days}d {int(rest // 3600)}h" if days else f"{int(rest // 3600)}h {int((rest % 3600) // 60)}m"
    os_name = {"Linux": "Linux", "Darwin": "macOS", "Windows": "Windows"}.get(
        platform.system(), platform.system())

    text = Text(no_wrap=True, overflow="crop")
    text.append(" TERMSTATS ", style="bold white on #2d6cdf")
    text.append(f"  {platform.node()[:24]}", style="bold white")
    text.append(f" {os_name}", style=MUTED)
    text.append("   load ", style=MUTED)
    text.append(f"{load1:.2f}", style=f"bold {ramp(load1 / (ncpu * 2))}")
    text.append(f" {load5:.2f} {load15:.2f}", style=DIM)
    text.append(f"   {ncpu} cpu", style=MUTED)
    text.append("   up ", style=MUTED)
    text.append(up, style="grey70")
    text.append("   proc ", style=MUTED)
    text.append(str(len(psutil.pids())), style="grey70")

    tail = Text(no_wrap=True)
    # A 16-cell CPU sparkline, tmux-status-bar style: the whole recent history in one
    # glance without looking down at the chart. Peaks per cell, tinted by the ramp.
    spark = sparkline(cpu_history, 16)
    if spark.cell_len:
        tail.append_text(spark)
        tail.append("  ")
    # The wall clock is the liveness signal: a frozen dashboard and a quiet machine look
    # identical without it. It sits in the tail because the head is the identity.
    tail.append(time.strftime("%H:%M:%S") + "  ", style="grey70")
    tail.append(f"{sample_interval:g}s  ", style=MUTED)
    tail.append(f"v{__version__} ", style=FAINT)
    # Only right-align the tail when there is actually room; otherwise it collides with the
    # process count and the two run together into one unreadable number.
    pad = width - text.cell_len - tail.cell_len
    if pad >= 2:
        text.append(" " * pad)
        text.append_text(tail)
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

    # --- height budget ---------------------------------------------------------------
    remaining = th - 1 - top_h - (0 if disk_in_stack else disk_h)
    charts_h = 0
    if remaining >= 13:
        charts_h = min(12, remaining - 5)
    proc_h = remaining - charts_h

    sections = [Layout(header_line(tw), name="head", size=1)]

    stack_panels = [(_panel(mem_body, "memory", C_MEM), mem_h),
                    (_panel(net_body, "network", C_NET), net_h)]
    if disk_in_stack:
        stack_panels.append((_panel(disk_body, "disk", C_DISK), disk_h))
    stack_panels = stack_panels[:kept_stack]

    cpu_panel = _panel(cpu_body, "cpu", C_CPU)
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
        sections.append(Layout(_panel(disk_body, "disk", C_DISK), name="disk", size=disk_h))

    if charts_h:
        chart_w = (tw - 1) // 2 - 4
        chart_h = charts_h - 2
        charts = Layout(name="charts", size=charts_h)
        charts.split_row(
            Layout(_panel(get_cpu_chart(chart_w, chart_h), "cpu", C_CPU,
                          cpu_chart_subtitle()), name="c1"),
            Layout(_panel(get_net_chart(chart_w, chart_h), "network", C_NET,
                          net_chart_subtitle()), name="c2"),
        )
        sections.append(charts)

    # A panel needs its two borders, a header row and at least two rows of content before
    # it is worth drawing; below that it is a stump, and a stump is worse than the space.
    if proc_h >= 5:
        procs = get_top_processes(tw - 4, n=max(proc_h - 3, 1))
        sections.append(Layout(_panel(procs, "processes", C_PROC, "by cpu"), name="proc"))

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
    """
    global sample_interval
    sample_interval = SNAPSHOT_SAMPLE_S
    _prime_measurements()
    time.sleep(SNAPSHOT_SAMPLE_S)
    console.print(render_dashboard())


def run_live(interval=DEFAULT_INTERVAL):
    global sample_interval
    sample_interval = interval
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
    print("  -V, --version       Show version")
    print("  -h, --help          Show this help")
    print()
    print("Long options also work with a single dash: -live, -once, -interval, -version, -help")
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


def main():
    _ensure_console_encoding()
    detect_capabilities()
    args = sys.argv[1:]

    if any(a in _HELP_FLAGS for a in args):
        print_help()
        sys.exit(0)

    if any(a in _VERSION_FLAGS for a in args):
        print(f"termstats {__version__}")
        sys.exit(0)

    interval = DEFAULT_INTERVAL
    mode = None  # None = decide from the terminal

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

    if mode is None:
        mode = "live" if _stdout_is_interactive() else "once"

    if mode == "live":
        run_live(interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
