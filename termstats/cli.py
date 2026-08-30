#!/usr/bin/env python3
"""
termstats - Beautiful terminal server dashboard with real-time charts.

Cross-platform system monitoring: CPU, RAM, Swap, Disk, Network,
Top Processes, and live history graphs — all in your terminal.
"""

import platform
import sys
import time
import shutil
import psutil
import plotext as plt
from collections import deque
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich import box

from termstats import __version__

IS_LINUX = platform.system() == "Linux"
IS_MACOS = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"
HISTORY_LEN = 60

console = Console()

cpu_history = deque(maxlen=HISTORY_LEN)
steal_history = deque(maxlen=HISTORY_LEN)
net_sent_history = deque(maxlen=HISTORY_LEN)
net_recv_history = deque(maxlen=HISTORY_LEN)

_steal_last_total = None
_steal_last_steal = None


def bar_horizontal(label, percent, width=40, color="green"):
    if percent > 90:
        color = "red"
    elif percent > 70:
        color = "yellow"
    filled = int(width * percent / 100)
    empty = width - filled
    bar = f"[{color}]{'█' * filled}[/{color}][dim]{'░' * empty}[/dim]"
    return f"{label:>12s} {bar} {percent:5.1f}%"


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


def get_cpu_section():
    percents = psutil.cpu_percent(percpu=True)
    lines = []
    for i, p in enumerate(percents):
        lines.append(bar_horizontal(f"Core {i}", p))
    total = psutil.cpu_percent()
    lines.append("")
    lines.append(bar_horizontal("Total", total, color="cyan"))

    steal_pct = _read_steal_pct()
    if IS_LINUX:
        lines.append(bar_horizontal("Steal", steal_pct, color="red"))

    return "\n".join(lines), total, steal_pct


def get_memory_section():
    mem = psutil.virtual_memory()
    lines = [
        bar_horizontal("RAM used", mem.percent),
        f"{'':>12s} {mem.used / 1024**3:.1f}G / {mem.total / 1024**3:.1f}G  (avail {mem.available / 1024**3:.1f}G)",
    ]
    swap = psutil.swap_memory()
    if swap.total > 0:
        lines.append("")
        lines.append(bar_horizontal("Swap", swap.percent))
        lines.append(f"{'':>12s} {swap.used / 1024**3:.1f}G / {swap.total / 1024**3:.1f}G")
    return "\n".join(lines)


def get_disk_section():
    lines = []
    skip_fs = {"tmpfs", "devtmpfs", "squashfs", "overlay", "devfs"}
    seen = set()
    for part in psutil.disk_partitions(all=False):
        if part.fstype in skip_fs or part.mountpoint in seen:
            continue
        seen.add(part.mountpoint)
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        label = part.mountpoint
        if len(label) > 12:
            label = "..." + label[-9:]
        lines.append(bar_horizontal(label, usage.percent))
        lines.append(f"{'':>12s} {usage.used / 1024**3:.1f}G / {usage.total / 1024**3:.1f}G")

    try:
        io = psutil.disk_io_counters()
        if io and hasattr(get_disk_section, '_last_io'):
            dt = time.time() - get_disk_section._last_time
            if dt > 0:
                read_s = (io.read_bytes - get_disk_section._last_io.read_bytes) / dt
                write_s = (io.write_bytes - get_disk_section._last_io.write_bytes) / dt
                lines.append("")
                lines.append(f"{'Read':>12s}  {read_s / 1024**2:6.1f} MB/s")
                lines.append(f"{'Write':>12s}  {write_s / 1024**2:6.1f} MB/s")
        if io:
            get_disk_section._last_io = io
            get_disk_section._last_time = time.time()
    except Exception:
        pass
    return "\n".join(lines) if lines else "  No disks found"


def _fmt_bytes_rate(b):
    if b > 1024**2:
        return f"{b / 1024**2:.1f} MB/s"
    elif b > 1024:
        return f"{b / 1024:.1f} KB/s"
    return f"{b:.0f} B/s"


def get_network_section():
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

    lines = [
        f"  {'TX':>6s}  {_fmt_bytes_rate(sent_s):>12s}   (total {net.bytes_sent / 1024**3:.2f} GB)",
        f"  {'RX':>6s}  {_fmt_bytes_rate(recv_s):>12s}   (total {net.bytes_recv / 1024**3:.2f} GB)",
    ]
    if conns >= 0:
        lines.append(f"  {'Conns':>6s}  {conns:>12d}")
    return "\n".join(lines), sent_s, recv_s


def get_top_processes(n=8):
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'memory_info']):
        try:
            info = p.info
            if info['cpu_percent'] is not None:
                procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda x: x['cpu_percent'] or 0, reverse=True)

    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan", expand=True)
    table.add_column("PID", justify="right", width=7)
    table.add_column("Name", width=24)
    table.add_column("CPU%", justify="right", width=7)
    table.add_column("MEM%", justify="right", width=7)
    table.add_column("RSS", justify="right", width=9)

    for proc in procs[:n]:
        cpu_pct = proc['cpu_percent'] or 0
        mem_pct = proc['memory_percent'] or 0
        rss = proc['memory_info'].rss / 1024**2 if proc['memory_info'] else 0
        cpu_style = "red" if cpu_pct > 50 else "yellow" if cpu_pct > 10 else ""
        table.add_row(
            str(proc['pid']),
            (proc['name'] or "?")[:24],
            f"[{cpu_style}]{cpu_pct:.1f}[/{cpu_style}]" if cpu_style else f"{cpu_pct:.1f}",
            f"{mem_pct:.1f}",
            f"{rss:.0f}M",
        )
    return table


def get_cpu_chart(width, height):
    if len(cpu_history) < 2:
        return "  Collecting data..."
    plt.clear_figure()
    plt.plot(list(cpu_history), label="CPU %", color="cyan")
    if IS_LINUX and any(s > 0 for s in steal_history):
        plt.plot(list(steal_history), label="Steal %", color="red")
    plt.ylim(0, 100)
    plt.title("CPU Usage (last 60s)")
    plt.theme("dark")
    plt.plotsize(width, height)
    return plt.build()


def get_net_chart(width, height):
    if len(net_sent_history) < 2:
        return "  Collecting data..."
    plt.clear_figure()
    sent_kb = [x / 1024 for x in net_sent_history]
    recv_kb = [x / 1024 for x in net_recv_history]
    plt.plot(sent_kb, label="TX KB/s", color="green")
    plt.plot(recv_kb, label="RX KB/s", color="blue")
    plt.title("Network (last 60s)")
    plt.theme("dark")
    plt.plotsize(width, height)
    return plt.build()


def get_load_info():
    load1, load5, load15 = psutil.getloadavg()
    ncpu = psutil.cpu_count()
    boot = psutil.boot_time()
    uptime_s = time.time() - boot
    days = int(uptime_s // 86400)
    hours = int((uptime_s % 86400) // 3600)
    mins = int((uptime_s % 3600) // 60)

    load_color = "green"
    if load1 > ncpu * 2:
        load_color = "red"
    elif load1 > ncpu:
        load_color = "yellow"

    hostname = platform.node()
    os_name = {"Linux": "Linux", "Darwin": "macOS", "Windows": "Windows"}.get(platform.system(), platform.system())

    return (
        f"  [{load_color}]{hostname}[/{load_color}] ({os_name})    "
        f"Load: [{load_color}]{load1:.2f}[/{load_color}] / {load5:.2f} / {load15:.2f}  "
        f"({ncpu} CPUs)    "
        f"Up: {days}d {hours}h {mins}m    "
        f"Procs: {len(psutil.pids())}"
    )


def render_dashboard():
    tw = shutil.get_terminal_size().columns
    th = shutil.get_terminal_size().lines

    cpu_text, cpu_total, steal_pct = get_cpu_section()
    mem_text = get_memory_section()
    disk_text = get_disk_section()
    net_text, sent_s, recv_s = get_network_section()
    top_table = get_top_processes()
    load_text = get_load_info()

    cpu_history.append(cpu_total)
    steal_history.append(steal_pct)
    net_sent_history.append(sent_s)
    net_recv_history.append(recv_s)

    chart_w = max(tw // 2 - 4, 30)
    chart_h = max(th // 4 - 2, 6)
    cpu_chart = get_cpu_chart(chart_w, chart_h)
    net_chart = get_net_chart(chart_w, chart_h)

    header = Text(" TERMSTATS ", style="bold white on blue")
    header_line = Text.assemble(
        header,
        ("  " + time.strftime("%Y-%m-%d %H:%M:%S"), "dim"),
        (f"  v{__version__}", "dim italic"),
        ("  (bottled 🍻 by Martin Pfeffer - celox.io)", "dim"),
    )

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(
        Panel(cpu_text, title="[bold]CPU[/bold]", border_style="cyan", expand=True),
        Panel(mem_text, title="[bold]Memory[/bold]", border_style="green", expand=True),
    )
    grid.add_row(
        Panel(disk_text, title="[bold]Disk[/bold]", border_style="yellow", expand=True),
        Panel(net_text, title="[bold]Network[/bold]", border_style="blue", expand=True),
    )

    chart_grid = Table.grid(expand=True, padding=(0, 1))
    chart_grid.add_column(ratio=1)
    chart_grid.add_column(ratio=1)
    chart_grid.add_row(
        Panel(cpu_chart, title="[bold]CPU History[/bold]", border_style="cyan"),
        Panel(net_chart, title="[bold]Network History[/bold]", border_style="blue"),
    )

    output = Table.grid(expand=True)
    output.add_column()
    output.add_row(header_line)
    output.add_row(load_text)
    output.add_row(grid)
    output.add_row(chart_grid)
    output.add_row(Panel(top_table, title="[bold]Top Processes[/bold]", border_style="magenta"))
    return output


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


def run_once():
    _prime_measurements()
    time.sleep(1)
    console.print(render_dashboard())


def run_live(interval=1.0):
    _prime_measurements()
    time.sleep(0.5)
    try:
        with Live(render_dashboard(), console=console, refresh_per_second=1, screen=True) as live:
            while True:
                time.sleep(interval)
                live.update(render_dashboard())
    except KeyboardInterrupt:
        pass


def main():
    args = sys.argv[1:]

    if "-h" in args or "--help" in args:
        print(f"termstats v{__version__} - Beautiful terminal server dashboard")
        print()
        print("Usage: termstats [OPTIONS]")
        print()
        print("Options:")
        print("  -l, --live          Live dashboard (Ctrl+C to exit)")
        print("  -i, --interval N    Update interval in seconds (default: 1)")
        print("  -V, --version       Show version")
        print("  -h, --help          Show this help")
        print()
        print("Examples:")
        print("  termstats           Single snapshot")
        print("  termstats -l        Live dashboard")
        print("  termstats -l -i 3   Live, update every 3 seconds")
        print()
        print("Module form:  python -m termstats [OPTIONS]")
        sys.exit(0)

    if "-V" in args or "--version" in args:
        print(f"termstats {__version__}")
        sys.exit(0)

    interval = 1.0
    live_mode = False

    for i, arg in enumerate(args):
        if arg in ("-l", "--live"):
            live_mode = True
        elif arg in ("-i", "--interval") and i + 1 < len(args):
            interval = float(args[i + 1])

    if live_mode:
        run_live(interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
