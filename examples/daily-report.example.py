#!/usr/bin/env python3
"""A daily mail: what the machine did yesterday, and which computer fits.

Reads the history `termstats record` keeps, sums up one day, adds the current
`termstats recommend --now` report and sends both as one mail - with a PDF attached that
shows the day in charts (the report is built as HTML with inline SVG and printed to PDF by
a headless Chrome/Chromium/Edge/Brave; without one the mail goes out without the PDF).
Standard library only (Python 3.9+); a template, not part of the package.

The mail goes out through msmtp on another host, reached over ssh - handy when a server
already has SMTP set up and the laptop has not:

    TERMSTATS_REPORT_TO=you@example.org            recipient (required to send)
    TERMSTATS_REPORT_SSH=user@server               where msmtp runs (required to send)
    TERMSTATS_REPORT_SSH_KEY=~/.ssh/id_ed25519     optional: the key to use
    TERMSTATS_REPORT_FROM=reports@example.org      optional: From header (default: msmtp's)
    TERMSTATS_NATIVE_BIN=/path/termstats-native    optional: where the native core is
    TERMSTATS_REPORT_BROWSER=/path/to/chrome       optional: the browser that prints the PDF

    daily-report.example.py              yesterday, once (a marker remembers what was sent)
    daily-report.example.py --date 2026-11-15      that day, sent regardless of the marker
    daily-report.example.py --print      print the text, send nothing
    daily-report.example.py --pdf out.pdf          write the PDF there, send nothing

Run it once a day (cron, a launchd agent with StartCalendarInterval, a systemd timer).
Note what it sends: the names of your busiest programs leave the machine with this mail.
"""

import datetime as dt
import html
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from email.message import EmailMessage

COLUMNS = ["ts", "n", "gap_s", "cpu_avg", "cpu_p95", "cpu_max", "busy_avg", "busy_max", "single", "mem_avg_mb",
           "mem_max_mb", "demand_mb", "swap_mb", "pressure", "gpu_avg", "gpu_max", "disk_used_gb", "disk_total_gb",
           "read_mb", "write_mb", "rx_mb", "tx_mb", "ac", "battery", "thermal", "displays", "top_cpu", "top_mem"]


def state_dir():
    if os.environ.get("TERMSTATS_STATE_DIR"):
        return pathlib.Path(os.environ["TERMSTATS_STATE_DIR"])
    if os.environ.get("XDG_STATE_HOME"):
        return pathlib.Path(os.environ["XDG_STATE_HOME"]) / "termstats"
    return pathlib.Path.home() / ".local" / "state" / "termstats"


def native_bin():
    choice = os.environ.get("TERMSTATS_NATIVE_BIN")
    if choice:
        return choice
    for candidate in (shutil.which("termstats-native"), "~/.cargo/bin/termstats-native", "~/.local/bin/termstats-native"):
        if candidate and os.access(os.path.expanduser(candidate), os.X_OK):
            return os.path.expanduser(candidate)
    return None


def read_day(day):
    """(machine, rows) of one day file. A field the machine did not report is None."""
    path = state_dir() / "history" / f"{day.isoformat()}.tsv"
    machine, rows = {}, []
    if not path.exists():
        return machine, rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split("\t")
        if line.startswith("#termstats-history"):
            machine = dict(f.split("=", 1) for f in fields[1:] if "=" in f)
            continue
        if line.startswith("#") or len(fields) < 26:
            continue
        fields += [""] * (len(COLUMNS) - len(fields))
        row = {}
        try:
            for name, value in zip(COLUMNS, fields):
                row[name] = value if name.startswith("top_") else (float(value) if value != "" else None)
        except ValueError:
            continue                    # a damaged line costs that line only
        rows.append(row)
    return machine, rows


def percentile(values, share):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))] if ordered else None


def consumers(rows, column, combine):
    """Programs of a `name:value|name:value` column, combined over the day, largest first."""
    totals = {}
    for row in rows:
        for part in filter(None, row[column].split("|")):
            name, _, value = part.rpartition(":")
            try:
                totals[name] = combine(totals.get(name, 0.0), float(value))
            except ValueError:
                pass
    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:5]


def summarise(machine, rows):
    """The day in numbers; None where nothing was reported."""
    def col(name):
        return [r[name] for r in rows if r[name] is not None]

    def mean(name):
        values = col(name)
        return sum(values) / len(values) if values else None

    def share(name, test):
        values = col(name)
        return sum(1 for v in values if test(v)) / len(values) if values else None

    installed = float(machine.get("mem_mb", 0)) or None
    last = rows[-1]
    return {
        "minutes": len(rows),
        "first": dt.datetime.fromtimestamp(rows[0]["ts"]),
        "last": dt.datetime.fromtimestamp(rows[-1]["ts"]),
        "gap_h": sum(col("gap_s")) / 3600,
        "cpu_avg": mean("cpu_avg"), "cpu_p95": percentile(col("cpu_p95"), 0.95), "cpu_max": max(col("cpu_max"), default=None),
        "cpu_busy_min": sum(1 for v in col("cpu_avg") if v >= 80),
        "busy_max": max(col("busy_max"), default=None),
        "mem_avg": mean("mem_avg_mb"), "mem_max": max(col("mem_max_mb"), default=None),
        "demand_p99": percentile(col("demand_mb"), 0.99), "demand_max": max(col("demand_mb"), default=None),
        "installed": installed,
        "over_min": sum(1 for v in col("demand_mb") if installed and v > installed),
        "swap_max": max(col("swap_mb"), default=None),
        "pressure": share("pressure", lambda v: v >= 1),
        "gpu_avg": mean("gpu_avg"), "gpu_max": max(col("gpu_max"), default=None),
        "disk_used": last["disk_used_gb"], "disk_total": last["disk_total_gb"],
        "disk_delta": (last["disk_used_gb"] - rows[0]["disk_used_gb"]) if None not in (last["disk_used_gb"], rows[0]["disk_used_gb"]) else None,
        "read_gb": sum(col("read_mb")) / 1024, "write_gb": sum(col("write_mb")) / 1024,
        "rx_gb": sum(col("rx_mb")) / 1024, "tx_gb": sum(col("tx_mb")) / 1024,
        "battery": share("ac", lambda v: v < 0.5),
        "thermal": share("thermal", lambda v: v >= 1),
        "displays": share("displays", lambda v: v >= 1),
        "top_cpu": consumers(rows, "top_cpu", lambda a, b: a + b),
        "top_mem": consumers(rows, "top_mem", max),
    }


def de(value, digits=1, unit=""):
    if value is None:
        return "nicht gemeldet"
    return f"{value:.{digits}f}".replace(".", ",") + unit


def lines_of(s, machine):
    """The summary as (label, text) pairs - one source for the plain and the HTML part."""
    gb = 1024.0
    hours, minutes = divmod(s["minutes"], 60)
    out = [
        ("Rechner", f'{machine.get("model", "?")}, {machine.get("chip", "?")}, {machine.get("ncpu", "?")} Kerne, '
                    f'{de((s["installed"] or 0) / gb, 0)} GB'),
        ("Erfasst", f'{hours} h {minutes:02d} min zwischen {s["first"]:%H:%M} und {s["last"]:%H:%M} Uhr'
                    + (f', dazu {de(s["gap_h"])} h Ruhezustand' if s["gap_h"] >= 0.1 else "")),
        ("Prozessor", f'im Mittel {de(s["cpu_avg"], 0)} %, 95 % der Zeit unter {de(s["cpu_p95"], 0)} %, Spitze {de(s["cpu_max"], 0)} %; '
                      f'{s["cpu_busy_min"]} min bei 80 % und mehr; bis zu {de(s["busy_max"], 0)} Kerne gleichzeitig beschäftigt'),
        ("Speicher", f'belegt im Mittel {de(s["mem_avg"] / gb if s["mem_avg"] else None)} GB, Spitze {de(s["mem_max"] / gb if s["mem_max"] else None)} GB; '
                     f'gebraucht hätte der Rechner bis zu {de(s["demand_max"] / gb if s["demand_max"] else None)} GB '
                     f'(99. Perzentil {de(s["demand_p99"] / gb if s["demand_p99"] else None)} GB); '
                     f'{s["over_min"]} min über dem verbauten Speicher; Swap bis {de(s["swap_max"] / gb if s["swap_max"] is not None else None)} GB'
                     + (f'; {de(s["pressure"] * 100, 0)} % der Zeit unter Speicherdruck' if s["pressure"] is not None else "")),
        ("Grafik", "nicht gemeldet" if s["gpu_avg"] is None else f'im Mittel {de(s["gpu_avg"], 0)} %, Spitze {de(s["gpu_max"], 0)} %'),
        ("Datenträger", f'{de(s["disk_used"], 0)} von {de(s["disk_total"], 0)} GB belegt'
                        + (f' ({de(100 * s["disk_used"] / s["disk_total"], 0)} %)' if s["disk_used"] and s["disk_total"] else "")
                        + (f', {"+" if s["disk_delta"] >= 0 else "−"}{de(abs(s["disk_delta"]))} GB an diesem Tag' if s["disk_delta"] is not None else "")
                        + f'; gelesen {de(s["read_gb"])} GB, geschrieben {de(s["write_gb"])} GB'),
        ("Netz", f'empfangen {de(s["rx_gb"], 2)} GB, gesendet {de(s["tx_gb"], 2)} GB'),
        ("Betrieb", ("kein Akku gemeldet" if s["battery"] is None else f'{de(s["battery"] * 100, 0)} % der Zeit im Akkubetrieb')
                    + ("" if s["displays"] is None else f'; {de(s["displays"] * 100, 0)} % mit externem Monitor')
                    + ("" if s["thermal"] is None else f'; {de(s["thermal"] * 100, 0)} % thermisch gedrosselt')),
        ("Meiste CPU-Zeit", ", ".join(f"{name} {de(sec / 60, 0)} min" for name, sec in s["top_cpu"]) or "nichts erfasst"),
        ("Größter Speicher", ", ".join(f"{name} {de(mb / gb)} GB" for name, mb in s["top_mem"]) or "nichts erfasst"),
    ]
    return out


def recommendation():
    """(text report, the middle tier's laptop and desktop as a short string or None, the JSON report or {})."""
    binary = native_bin()
    if not binary:
        return "termstats-native ist nicht installiert - keine Empfehlung.", None, {}
    text = subprocess.run([binary, "recommend", "--now"], capture_output=True, text=True, timeout=60)
    short, data = None, {}
    try:
        data = json.loads(subprocess.run([binary, "recommend", "--now", "--json"], capture_output=True, text=True, timeout=60).stdout)
        fit = data["tiers"][1]
        parts = [f'{label} {pick["name"]}, {pick["ram_gb"]} GB, {pick["price_eur"]} EUR'
                 for label, pick in (("mobil", fit.get("laptop")), ("Desktop", fit.get("desktop"))) if pick]
        short = "; ".join(parts) or None
    except (ValueError, KeyError, IndexError, TypeError, subprocess.SubprocessError):
        pass
    return (text.stdout or text.stderr).rstrip() or "Keine Empfehlung erhalten.", short, data


# --- the PDF: HTML with inline SVG, printed by a headless browser ---------------------------

INK, SOFT, MUTED, GRID = "#0b0b0b", "#52514e", "#8a897f", "#e4e3dd"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"     # categorical slots 1-3, fixed order
CHART_W, HALF_W, PAD_L, PAD_R, PAD_T, PAD_B = 720, 352, 46, 14, 10, 24
GAP_S = 150                                              # more than this between two lines: the line breaks


def nice_max(value):
    """The next round axis end at or above a value."""
    if not value or value <= 0:
        return 1.0
    power = 10.0 ** (len(str(int(value))) - 1) if value >= 1 else 0.1
    for step in (1.2, 2, 4, 6, 8, 10):          # each a quarter of which reads well on an axis
        if step * power >= value:
            return step * power
    return 10 * power


def num(value):
    if float(value).is_integer() or value >= 100:
        return f"{value:.0f}"
    return (f"{value:.1f}" if value >= 1 else f"{value:.2f}".rstrip("0")).replace(".", ",")


def time_ticks(t0, t1, most=9):
    span = t1 - t0
    step = next((s for s in (900, 1800, 3600, 7200, 10800, 14400, 21600) if span / s <= most), 43200)
    first = (int(t0) // step + (1 if t0 % step else 0)) * step
    # Whole steps of LOCAL time: an hour grid must sit on local hours.
    offset = dt.datetime.fromtimestamp(first).utcoffset() or dt.timedelta(0)
    return [t for t in range(first, int(t1) + 1, step)], offset


def runs(rows, key, scale):
    """The series as unbroken runs of (ts, value): sleep and missing values break the line."""
    out, current, last_ts = [], [], None
    for row in rows:
        value = row[key]
        if value is None or (last_ts is not None and row["ts"] - last_ts > GAP_S):
            if current:
                out.append(current)
            current = []
        if value is not None:
            current.append((row["ts"], value * scale))
        last_ts = row["ts"]
    if current:
        out.append(current)
    return out


def line_chart(rows, series, unit, y_max=None, reference=None, height=150, width=CHART_W):
    """One y axis, time on x. series = [(column, label, colour, scale, filled)]."""
    t0 = (int(rows[0]["ts"]) // 3600) * 3600
    t1 = max(t0 + 3600, -(-int(rows[-1]["ts"]) // 3600) * 3600)
    drawn = [(label, colour, filled, runs(rows, key, scale)) for key, label, colour, scale, filled in series]
    top = max([v for _, _, _, rr in drawn for r in rr for _, v in r] + ([reference[1]] if reference else []) + [0])
    y_max = y_max or nice_max(top * 1.05)
    plot_w, plot_h = width - PAD_L - PAD_R, height - PAD_T - PAD_B

    def x(ts):
        return PAD_L + (ts - t0) / (t1 - t0) * plot_w

    def y(value):
        return PAD_T + plot_h - min(value, y_max) / y_max * plot_h

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for i in range(5):
        value = y_max * i / 4
        parts.append(f'<line x1="{PAD_L}" x2="{width - PAD_R}" y1="{y(value):.1f}" y2="{y(value):.1f}" stroke="{GRID}" stroke-width="1"/>'
                     f'<text x="{PAD_L - 6}" y="{y(value) + 3.5:.1f}" text-anchor="end" font-size="10" fill="{MUTED}">{num(value)}{" " + unit if i == 4 else ""}</text>')
    ticks, _ = time_ticks(t0, t1, 9 if width > 500 else 5)
    for tick in ticks:
        parts.append(f'<text x="{x(tick):.1f}" y="{height - 7}" text-anchor="middle" font-size="10" fill="{MUTED}">{dt.datetime.fromtimestamp(tick):%H:%M}</text>')
    base = y(0)
    for _label, colour, filled, series_runs in drawn:
        for run in series_runs:
            points = " ".join(f"{x(ts):.1f},{y(v):.1f}" for ts, v in run)
            if filled and len(run) > 1:
                parts.append(f'<polygon points="{x(run[0][0]):.1f},{base:.1f} {points} {x(run[-1][0]):.1f},{base:.1f}" fill="{colour}" fill-opacity="0.16"/>')
            if len(run) > 1:
                parts.append(f'<polyline points="{points}" fill="none" stroke="{colour}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
            else:
                parts.append(f'<circle cx="{x(run[0][0]):.1f}" cy="{y(run[0][1]):.1f}" r="2" fill="{colour}"/>')
    if reference:
        label, value = reference
        parts.append(f'<line x1="{PAD_L}" x2="{width - PAD_R}" y1="{y(value):.1f}" y2="{y(value):.1f}" stroke="{INK}" stroke-width="1" stroke-dasharray="5 4"/>'
                     f'<text x="{width - PAD_R - 4}" y="{y(value) - 5:.1f}" text-anchor="end" font-size="10" fill="{SOFT}">{html.escape(label)}</text>')
    parts.append("</svg>")
    legend = ""
    if len(series) > 1:
        legend = '<div class="legend">' + "".join(
            f'<span><i style="background:{colour}"></i>{html.escape(label)}</span>' for _key, label, colour, _scale, _filled in series) + "</div>"
    return legend + "".join(parts)


def bar_chart(items, unit, colour=BLUE, value_text=num, width=HALF_W):
    """Horizontal bars, largest first; the name and the value are text, the bar carries the size."""
    if not items:
        return '<p class="none">nichts erfasst</p>'
    row_h, label_w, value_w = 24, 138, 58
    height = row_h * len(items) + 4
    top = max(value for _name, value in items) or 1
    plot_w = width - label_w - value_w
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for i, (name, value) in enumerate(items):
        yy = 2 + i * row_h
        shown = name if len(name) <= 24 else name[:23] + "…"
        bar = max(2.0, value / top * plot_w)
        parts.append(f'<text x="{label_w - 8}" y="{yy + 15}" text-anchor="end" font-size="11" fill="{INK}">{html.escape(shown)}</text>'
                     f'<rect x="{label_w}" y="{yy + 4}" width="{bar:.1f}" height="14" rx="3" fill="{colour}"/>'
                     f'<text x="{label_w + bar + 6:.1f}" y="{yy + 15}" font-size="11" fill="{SOFT}">{value_text(value)} {unit}</text>')
    parts.append("</svg>")
    return "".join(parts)


def day_columns(days, key, unit, colour, reference=None, width=HALF_W):
    """One column per day (date, value): the days side by side."""
    height, pad_b = 150, 24
    values = [v for _d, v in days if v is not None]
    y_max = nice_max(max(values + ([reference[1]] if reference else []) + [0]) * 1.05)
    plot_w, plot_h = width - PAD_L - PAD_R, height - PAD_T - pad_b
    slot = plot_w / len(days)
    bar_w = max(2.0, min(30.0, slot - 4))
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for i in range(5):
        value = y_max * i / 4
        yy = PAD_T + plot_h - i / 4 * plot_h
        parts.append(f'<line x1="{PAD_L}" x2="{width - PAD_R}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{GRID}" stroke-width="1"/>'
                     f'<text x="{PAD_L - 6}" y="{yy + 3.5:.1f}" text-anchor="end" font-size="10" fill="{MUTED}">{num(value)}{" " + unit if i == 4 else ""}</text>')
    every = max(1, -(-len(days) // 7))
    for i, (day, value) in enumerate(days):
        cx = PAD_L + slot * (i + 0.5)
        if value is not None:
            h = value / y_max * plot_h
            parts.append(f'<rect x="{cx - bar_w / 2:.1f}" y="{PAD_T + plot_h - h:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="3" fill="{colour}"/>')
            if len(days) <= 8:
                parts.append(f'<text x="{cx:.1f}" y="{PAD_T + plot_h - h - 4:.1f}" text-anchor="middle" font-size="10" fill="{SOFT}">{num(value)}</text>')
        if i % every == 0:
            parts.append(f'<text x="{cx:.1f}" y="{height - 7}" text-anchor="middle" font-size="10" fill="{MUTED}">{day:%d.%m.}</text>')
    if reference:
        label, value = reference
        yy = PAD_T + plot_h - value / y_max * plot_h
        parts.append(f'<line x1="{PAD_L}" x2="{width - PAD_R}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{INK}" stroke-width="1" stroke-dasharray="5 4"/>'
                     f'<text x="{width - PAD_R - 4}" y="{yy - 5:.1f}" text-anchor="end" font-size="10" fill="{SOFT}">{html.escape(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def earlier_days(day, limit=14):
    """(date, demand p99 in GB, mean CPU %) for the days on file up to `day`."""
    out = []
    for back in range(limit - 1, -1, -1):
        date = day - dt.timedelta(days=back)
        _machine, rows = read_day(date)
        if rows:
            demand = percentile([r["demand_mb"] for r in rows if r["demand_mb"] is not None], 0.99)
            cpu = [r["cpu_avg"] for r in rows if r["cpu_avg"] is not None]
            out.append((date, demand / 1024 if demand else None, sum(cpu) / len(cpu) if cpu else None))
    return out


def size(gb):
    return f"{gb // 1000} TB" if gb >= 1000 and gb % 1000 in (0, 24, 48, 96) else f"{gb} GB"


def unfold(report, limit=118):
    """Long lines of the text report broken at their semicolons, so a page does not wrap them blindly."""
    out = []
    for line in report.splitlines():
        if len(line) <= limit or "; " not in line:
            out.append(line)
            continue
        first, *rest = line.split("; ")
        out.append(first + ";")
        out += [" " * 16 + part + (";" if i < len(rest) - 1 else "") for i, part in enumerate(rest)]
    return "\n".join(out)


TIER_DE = {"just enough": "reicht knapp", "fits": "passt", "with reserve": "mit Reserve"}
SHORT_DE = {"memory": "Arbeitsspeicher", "storage": "Datenträger", "processor": "Prozessor", "graphics": "Grafik"}
RAM_FACTOR, SSD_FACTOR = (1.10, 1.30, 1.80), (1.25, 1.5, 2.0)     # the tiers' headroom, as termstats applies it


SETUP_DE = (("laptop", "Mobil (MacBook)"), ("desktop", "Schreibtisch (Desktop)"))


def euro(value):
    return f"{value:,.0f} €".replace(",", ".")


def pick_text(pick):
    return (f'{pick["vendor"]} {pick["name"]} – {pick["chip"]}, {pick["ram_gb"]} GB Speicher, {size(pick["ssd_gb"])} SSD – {euro(pick["price_eur"])}'
            + ("" if pick.get("verified", True) else " (Preis nicht beim Hersteller bestätigt)"))


def german(data):
    """The recommendation in German, from the JSON report: what the history says, whether this
    machine does, and why each level asks for what it asks."""
    pr, machine, hist = data["profile"], data["machine"], data["history"]

    def pct(share):
        return de(share * 100, 0, " %")

    first, last = dt.date.fromtimestamp(hist["first"]), dt.date.fromtimestamp(hist["last"])
    facts = [
        ("Verlauf", f'{first:%d.%m.} bis {last:%d.%m.%Y}: {hist["days"]} Tage, {hist["minutes"]} Minuten erfasst '
                    f'({pct(hist["awake_share"])} dieser Zeit war der Rechner wach)'),
        ("Speicher", f'gebraucht {de(pr["ram_p99_gb"])} GB (99. Perzentil), Spitze {de(pr["ram_max_gb"])} GB, verbaut {machine["mem_gb"]} GB; '
                     f'an {pr["days_over_ram"]} von {max(1, hist["days"])} Tagen über dem verbauten Speicher'
                     + ("" if pr["pressure_share"] is None else f'; {pct(pr["pressure_share"])} der Zeit unter Speicherdruck')),
        ("Prozessor", f'95 % der Zeit unter {de(pr["cpu_p95"], 0)} % Last auf {machine["ncpu"]} Kernen; {pct(pr["saturated_share"])} der Zeit bei 80 % und mehr; '
                      f'{pr["sustained_runs"]} anhaltende Spitzen von zehn Minuten oder länger; {pct(pr["single_share"])} der Zeit an einen einzelnen Kern gebunden'),
        ("Grafik", "von diesem Rechner nicht gemeldet, nicht bewertet" if pr["gpu_p95"] is None else f'95 % der Zeit unter {de(pr["gpu_p95"], 0)} %'),
        ("Datenträger", f'{de(pr["disk_used_gb"], 0)} von {de(pr["disk_total_gb"], 0)} GB belegt'
                        + (f' ({de(100 * pr["disk_used_gb"] / pr["disk_total_gb"], 0)} %)' if pr["disk_total_gb"] else "")
                        + f', Zuwachs {de(pr["disk_growth_gb_day"], 2)} GB je Tag'),
        ("Mobilität", ("kein Akku" if pr["battery_share"] is None else f'{pct(pr["battery_share"])} der Zeit im Akkubetrieb')
                      + ("" if pr["display_share"] is None else f'; {pct(pr["display_share"])} mit externem Monitor')
                      + ("" if pr["thermal_share"] is None else f'; {pct(pr["thermal_share"])} thermisch gedrosselt')),
    ]
    if "fits" not in machine:
        verdict = ""
    elif machine["fits"]:
        verdict = "Dieser Rechner reicht für das Gemessene aus. Die Tabelle zeigt, was dieselbe Arbeit neu kosten würde."
    else:
        verdict = "Dieser Rechner ist zu knapp bei: " + ", ".join(SHORT_DE.get(name, name) for name in machine.get("short", [])) + "."
    if not machine.get("chip_known", True):
        verdict += (f' Sein Chip ({machine["chip"]}) steht nicht im Katalog; Prozessor und Grafik ließen sich deshalb nicht vergleichen, '
                    "die Auswahl stützt sich nur auf Speicher, Datenträger und Mobilität.")
    form = ("Genannt sind immer beide Aufbauten, mobil und für den Schreibtisch: "
            "wo der nächste Auftrag erledigt wird, steht nicht im Verlauf.")
    tiers, setups = [], {"laptop": [], "desktop": []}
    for i, tier in enumerate(data["tiers"]):
        need = tier["need"]
        why = [f'Speicher {de(need["ram_gb"], 0)} GB = {de(pr["ram_p99_gb"])} GB × {de(RAM_FACTOR[i], 2)}',
               f'Datenträger {de(need["ssd_gb"], 0)} GB = {de(pr["disk_used_gb"], 0)} GB × {de(SSD_FACTOR[i], 2)} plus Zuwachs']
        if machine.get("multi"):
            why.append(f'Prozessor mindestens {de(need["multi"] * 100 / machine["multi"], 0)} % des jetzigen {machine["chip"]}')
        label = TIER_DE.get(tier["tier"], tier["tier"])
        tiers.append((label, "; ".join(why)))
        for kind in setups:
            setups[kind].append((label, tier.get(kind)))
    linux = data.get("linux") or {}
    for kind in setups:
        setups[kind].append(("unter Linux (passt)", linux.get(kind)))
    fit = data["tiers"][1] if len(data["tiers"]) > 1 else {}
    display = "" if pr["display_share"] is None else f'{pct(pr["display_share"])} der Zeit hing ein externer Monitor am Rechner'
    compare = []
    if fit.get("laptop") and fit.get("desktop"):
        gap = fit["laptop"]["price_eur"] - fit["desktop"]["price_eur"]
        compare.append(f'In der Stufe „passt“ kostet das MacBook {euro(abs(gap))} {"mehr" if gap >= 0 else "weniger"} als der Desktop.')
    compare.append("Mobil: " + ("der Verlauf stammt von einem Rechner ohne Akku" if pr["battery_share"] is None
                                else f'im Verlauf lief der Rechner {pct(pr["battery_share"])} der Zeit im Akkubetrieb')
                   + (f'; {display}' if display else "") + ". Die Auswahl hängt davon nicht ab.")
    if any(pick and pick.get("form") == "desktop" for _label, pick in setups["desktop"]):
        compare.append("Desktop: der Preis gilt für den Rechner allein. Monitor, Tastatur und Maus kommen dazu, soweit sie nicht schon da sind"
                       + (f' ({display})' if display else "") + ".")
    if fit.get("need", {}).get("no_fanless"):
        compare.append("Rechner ohne Lüfter sind in beiden Aufbauten ausgeschlossen: die Last hielt über längere Zeit an.")
    try:
        stand = f'{dt.date.fromisoformat(data["catalogue"]):%d.%m.%Y}'
    except ValueError:
        stand = data["catalogue"]
    notes = [f'Genannt ist je Stufe die günstigste Ausstattung, die den Bedarf deckt. Listenpreise in Euro laut Katalog vom {stand}.',
             "Prozessor und Grafik werden über veröffentlichte Benchmark-Werte verglichen: eine Näherung, gut für Klassen, nicht für Prozente.",
             "Nicht gemessen: Software, die ein bestimmtes System braucht, Anschlüsse, Bildschirmgröße, Geschmack."]
    if data.get("catalogue_age_days", 0) > 120:
        notes.insert(0, f'Der Katalog ist {data["catalogue_age_days"]} Tage alt: Modelle und Preise können sich geändert haben, termstats aktualisieren.')
    provisional = "" if data.get("ready", True) else (f'Vorläufig: erst {hist["days"]} von 14 Tagen sind aufgezeichnet. '
                                                      "Die Empfehlung kann sich noch deutlich ändern.")
    return {"facts": facts, "verdict": verdict, "form": form, "tiers": tiers, "setups": setups, "compare": compare, "notes": notes,
            "provisional": provisional}


def german_plain(g):
    width = max(len(label) for label, _ in g["facts"]) + 2
    out = ([g["provisional"], ""] if g["provisional"] else []) + ["Was der Verlauf sagt"]
    out += [f"  {label + ':':<{width}}{text}" for label, text in g["facts"]]
    out += ["", g["verdict"], g["form"], ""]
    for kind, title in SETUP_DE:
        out.append(title)
        out += [f"  {label}:  " + (pick_text(pick) if pick else "nichts im Katalog erfüllt das") for label, pick in g["setups"][kind]]
        out.append("")
    out.append("Was jede Stufe verlangt (für beide Aufbauten gleich)")
    out += [f"  {label}:  {why}" for label, why in g["tiers"]]
    out += ["", "Die beiden Aufbauten im Vergleich"] + [f"  {line}" for line in g["compare"]]
    return "\n".join(out + [""] + g["notes"])

CSS = """
@page { size: A4; margin: 14mm 14mm 15mm; }
* { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; font-family: -apple-system, "Helvetica Neue", "Segoe UI", Arial, sans-serif; font-size: 10.5pt; color: #0b0b0b; background: #fff; }
h1 { font-size: 19pt; margin: 0 0 2px; letter-spacing: -0.01em; }
.sub { color: #52514e; margin: 0 0 14px; }
h2 { font-size: 12pt; margin: 15px 0 2px; }
h3 { font-size: 10.5pt; margin: 12px 0 3px; }
table.picks { table-layout: fixed; }
table.picks th:nth-child(1) { width: 18%; } table.picks th:nth-child(2) { width: 27%; } table.picks th:nth-child(3) { width: 29%; }
table.picks th:nth-child(4) { width: 10%; } table.picks th:nth-child(5) { width: 7%; } table.picks th:nth-child(6) { width: 9%; }
section p { margin: 3px 0; }
.note { color: #52514e; font-size: 9pt; margin: 0 0 6px; }
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 0 0 6px; }
.tile { border: 1px solid #e4e3dd; border-radius: 8px; padding: 9px 11px; }
.tile b { display: block; font-size: 17pt; font-weight: 650; letter-spacing: -0.02em; }
.tile span { color: #52514e; font-size: 8.5pt; }
.tile small { display: block; color: #52514e; font-size: 8.5pt; margin-top: 2px; }
section { break-inside: avoid; }
section.flow { break-inside: auto; }
.legend { font-size: 9pt; color: #52514e; margin: 2px 0 2px 0; }
.legend span { margin-right: 16px; white-space: nowrap; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 5px; vertical-align: -1px; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; }
th { text-align: left; color: #52514e; font-weight: 600; border-bottom: 1px solid #c9c8c0; padding: 5px 8px 5px 0; }
td { border-bottom: 1px solid #e4e3dd; padding: 6px 8px 6px 0; vertical-align: top; }
td.r, th.r { text-align: right; padding: 6px 0 6px 14px; white-space: nowrap; font-variant-numeric: tabular-nums; }
td.n { white-space: nowrap; }
tr.has td { border-bottom: 0; padding-bottom: 1px; }
td.why { color: #52514e; font-size: 8.5pt; padding-top: 0; }
.lead { margin: 6px 0 8px; }
.facts td:first-child { color: #52514e; white-space: nowrap; width: 1%; padding-right: 16px; }
.warn { border: 1px solid #e4e3dd; border-left: 4px solid #eda100; border-radius: 6px; padding: 7px 10px; font-size: 9.5pt; margin: 6px 0 8px; }
pre { font-family: Menlo, Consolas, monospace; font-size: 7pt; line-height: 1.45; white-space: pre-wrap; background: #f6f6f3; border-radius: 6px; padding: 9px 10px; margin: 6px 0 0; }
.none { color: #52514e; font-size: 9.5pt; }
footer { color: #8a897f; font-size: 8pt; margin-top: 16px; }
"""


def report_html(day, machine, rows, summary, pairs, report, g):
    gb = 1024.0
    esc = html.escape
    installed_gb = (summary["installed"] or 0) / gb
    hours, minutes = divmod(summary["minutes"], 60)
    disk_pct = 100 * summary["disk_used"] / summary["disk_total"] if summary["disk_used"] and summary["disk_total"] else None
    tiles = [
        (de(summary["cpu_avg"], 0, " %"), "Prozessor im Mittel", f'Spitze {de(summary["cpu_max"], 0)} %'),
        (de(summary["demand_max"] / gb if summary["demand_max"] else None, 1, " GB"), "Speicherbedarf, Spitze", f'verbaut {de(installed_gb, 0)} GB'),
        (de(disk_pct, 0, " %"), "Datenträger belegt", f'{de(summary["disk_used"], 0)} von {de(summary["disk_total"], 0)} GB'),
        (f"{hours} h {minutes:02d}", "erfasste Zeit", f'{summary["first"]:%H:%M} bis {summary["last"]:%H:%M} Uhr'),
    ]
    out = [f'<!doctype html><html lang="de"><head><meta charset="utf-8"><title>termstats {day.isoformat()}</title><style>{CSS}</style></head><body>',
           f'<h1>Ressourcenverbrauch am {day:%d.%m.%Y}</h1>',
           f'<p class="sub">{esc(machine.get("host", ""))} · {esc(machine.get("model", "?"))} · {esc(machine.get("chip", "?"))} · {esc(str(machine.get("ncpu", "?")))} Kerne · {de(installed_gb, 0)} GB</p>',
           '<div class="tiles">' + "".join(f'<div class="tile"><span>{esc(label)}</span><b>{esc(value)}</b><small>{esc(extra)}</small></div>' for value, label, extra in tiles) + "</div>"]

    out.append('<section><h2>Prozessor</h2><p class="note">Auslastung aller Kerne in Prozent, je Minute. Lücken sind Ruhezustand.</p>'
               + line_chart(rows, [("cpu_avg", "Mittel der Minute", BLUE, 1, True), ("cpu_max", "Spitze der Minute", ORANGE, 1, False)], "%", y_max=100) + "</section>")
    out.append('<section><h2>Arbeitsspeicher</h2><p class="note">Belegt ist, was im Speicher lag. Gebraucht ist, was der Rechner ohne Komprimieren und Auslagern belegt hätte.</p>'
               + line_chart(rows, [("mem_avg_mb", "belegt", BLUE, 1 / gb, True), ("demand_mb", "gebraucht", ORANGE, 1 / gb, False)], "GB",
                            reference=(f"verbaut {de(installed_gb, 0)} GB", installed_gb) if installed_gb else None) + "</section>")
    if summary["gpu_avg"] is not None:
        out.append('<section><h2>Grafik</h2><p class="note">Auslastung in Prozent, Spitze je Minute.</p>'
                   + line_chart(rows, [("gpu_max", "Spitze der Minute", AQUA, 1, True)], "%", y_max=100, height=112) + "</section>")
    out.append('<section><h2>Datenträger und Netz</h2><div class="two"><div><p class="note">Datenträger, Megabyte je Minute</p>'
               + line_chart(rows, [("read_mb", "gelesen", BLUE, 1, False), ("write_mb", "geschrieben", ORANGE, 1, False)], "MB", height=136, width=HALF_W)
               + '</div><div><p class="note">Netz, Megabyte je Minute</p>'
               + line_chart(rows, [("rx_mb", "empfangen", BLUE, 1, False), ("tx_mb", "gesendet", ORANGE, 1, False)], "MB", height=136, width=HALF_W)
               + "</div></div></section>")
    out.append('<section><h2>Größte Verbraucher</h2><div class="two"><div><p class="note">CPU-Zeit an diesem Tag, in Minuten</p>'
               + bar_chart([(name, sec / 60) for name, sec in summary["top_cpu"]], "min")
               + '</div><div><p class="note">Speicher, höchster Stand in GB</p>'
               + bar_chart([(name, mb / gb) for name, mb in summary["top_mem"]], "GB", value_text=lambda v: de(v))
               + "</div></div></section>")

    days = earlier_days(day)
    if len(days) >= 2:
        out.append(f'<section><h2>Die letzten {len(days)} Tage</h2><div class="two"><div><p class="note">Speicherbedarf (99. Perzentil) in GB</p>'
                   + day_columns([(d, ram) for d, ram, _cpu in days], "ram", "GB", ORANGE,
                                 reference=(f"verbaut {de(installed_gb, 0)} GB", installed_gb) if installed_gb else None)
                   + '</div><div><p class="note">Prozessor im Tagesmittel in Prozent</p>'
                   + day_columns([(d, cpu) for d, _ram, cpu in days], "cpu", "%", BLUE) + "</div></div></section>")

    out.append('<section><h2>Der Tag in Zahlen</h2><table class="facts">'
               + "".join(f"<tr><td>{esc(label)}</td><td>{esc(text)}</td></tr>" for label, text in pairs[1:]) + "</table></section>")

    out.append('<section><h2>Empfehlung</h2><p class="note">Aus dem gesamten Verlauf (höchstens vier Wochen), nicht nur aus diesem Tag.</p>')
    if not g:
        out.append(f'<pre>{esc(unfold(report))}</pre></section>')
    else:
        if g["provisional"]:
            out.append(f'<div class="warn"><b>Vorläufig.</b> {esc(g["provisional"].split(": ", 1)[1][:1].upper() + g["provisional"].split(": ", 1)[1][1:])}</div>')
        out.append(f'<p class="lead">{esc(g["verdict"])} {esc(g["form"])}</p>')
        for kind, title in SETUP_DE:
            out.append(f'<h3>{esc(title)}</h3><table class="picks"><tr><th>Stufe</th><th>Gerät</th><th>Chip</th><th class="r">Speicher</th><th class="r">SSD</th>'
                       '<th class="r">Preis</th></tr>')
            for label, pick in g["setups"][kind]:
                if pick:
                    out.append(f'<tr><td class="n"><b>{esc(label)}</b></td><td>{esc(pick["vendor"] + " " + pick["name"])}</td><td>{esc(pick["chip"])}</td>'
                               f'<td class="r">{pick["ram_gb"]} GB</td><td class="r">{size(pick["ssd_gb"])}</td><td class="r">{esc(euro(pick["price_eur"]))}'
                               + ("" if pick.get("verified", True) else " *") + '</td></tr>')
                else:
                    out.append(f'<tr><td class="n"><b>{esc(label)}</b></td><td colspan="5">nichts im Katalog erfüllt das</td></tr>')
            out.append("</table>")
        if any(pick and not pick.get("verified", True) for kind in g["setups"] for _label, pick in g["setups"][kind]):
            out.append('<p class="note">* Preis nicht beim Hersteller bestätigt.</p>')
        out.append('<h3>Was jede Stufe verlangt</h3><p class="note">Für beide Aufbauten gleich.</p><table class="facts">'
                   + "".join(f"<tr><td>{esc(label)}</td><td>{esc(why)}</td></tr>" for label, why in g["tiers"]) + "</table>")
        out.append('<h3>Die beiden Aufbauten im Vergleich</h3>' + "".join(f"<p>{esc(line)}</p>" for line in g["compare"]) + "</section>")
        out.append('<section><h2>Was der Verlauf sagt</h2><table class="facts">'
                   + "".join(f"<tr><td>{esc(label)}</td><td>{esc(text)}</td></tr>" for label, text in g["facts"]) + "</table>"
                   + "".join(f'<p class="note">{esc(note)}</p>' for note in g["notes"]) + "</section>")
    out.append(f'<footer>Erzeugt von termstats am {dt.datetime.now():%d.%m.%Y %H:%M} auf diesem Rechner aus dem lokalen Verlauf.</footer></body></html>')
    return "".join(out)


BROWSERS = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"]


def browser():
    choice = os.environ.get("TERMSTATS_REPORT_BROWSER")
    for candidate in ([choice] if choice else BROWSERS):
        path = candidate if os.path.isabs(candidate) else shutil.which(candidate)
        if path and os.access(path, os.X_OK):
            return path
    return None


def to_pdf(page):
    """The page printed to PDF by a headless browser; None when there is none or it fails."""
    binary = browser()
    if not binary:
        return None
    with tempfile.TemporaryDirectory(prefix="termstats-report-") as work:
        source, target = os.path.join(work, "report.html"), os.path.join(work, "report.pdf")
        pathlib.Path(source).write_text(page, encoding="utf-8")
        try:
            subprocess.run([binary, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                            f"--user-data-dir={os.path.join(work, 'profile')}", "--no-pdf-header-footer",
                            f"--print-to-pdf={target}", pathlib.Path(source).as_uri()],
                           capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return None
        data = pathlib.Path(target).read_bytes() if os.path.exists(target) else b""
        return data if data.startswith(b"%PDF") else None


def build(day):
    machine, rows = read_day(day)
    if not rows:
        return None
    summary = summarise(machine, rows)
    pairs = lines_of(summary, machine)
    report, short, data = recommendation()
    try:
        g = german(data) if data else None
    except (KeyError, TypeError, IndexError, ValueError):
        g = None                        # an older termstats-native: its own (English) text is used
    if g:
        report = german_plain(g)
    title = f"Ressourcenverbrauch am {day:%d.%m.%Y}"
    width = max(len(label) for label, _ in pairs)
    plain = "\n".join([title, "=" * len(title), ""] + [f"{label + ':':<{width + 2}}{text}" for label, text in pairs]
                      + ["", "Empfehlung (aus dem gesamten Verlauf, höchstens vier Wochen)", "-" * 60, report, "",
                         "Erzeugt von termstats auf diesem Rechner. Abbestellen: den täglichen Auftrag entfernen."])
    table = "".join(f'<tr><td style="padding:4px 14px 4px 0;vertical-align:top;white-space:nowrap;color:#555"><b>{html.escape(label)}</b></td>'
                    f'<td style="padding:4px 0">{html.escape(text)}</td></tr>' for label, text in pairs)
    rich = (f'<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:14px;color:#1a1a1a;max-width:860px">'
            f'<h2 style="margin:0 0 12px">{html.escape(title)}</h2><table style="border-collapse:collapse;font-size:14px">{table}</table>'
            f'<h3 style="margin:22px 0 8px">Empfehlung <span style="font-weight:normal;color:#555">(aus dem gesamten Verlauf, höchstens vier Wochen)</span></h3>'
            f'<pre style="font-family:Menlo,Consolas,monospace;font-size:12px;line-height:1.45;background:#f4f4f6;color:#1a1a1a;padding:12px;border-radius:8px;white-space:pre-wrap">{html.escape(report)}</pre>'
            f'<p style="color:#555;font-size:12px">Erzeugt von termstats auf diesem Rechner. Abbestellen: den täglichen Auftrag entfernen.</p></div>')
    subject = (f'termstats {day:%d.%m.}: CPU Ø {de(summary["cpu_avg"], 0)} %, '
               f'RAM-Bedarf bis {de(summary["demand_max"] / 1024 if summary["demand_max"] else None)} GB'
               + (f' - passt: {short}' if short else ""))
    pdf = to_pdf(report_html(day, machine, rows, summary, pairs, report, g))
    attached = ("Der Bericht mit Diagrammen hängt als PDF an." if pdf
                else "Ohne PDF: auf diesem Rechner wurde kein Browser gefunden, der es drucken kann.")
    plain = plain.replace("\n\n", f"\n\n{attached}\n\n", 1)
    rich = rich.replace("</h2>", f'</h2><p style="margin:0 0 12px;color:#555">{html.escape(attached)}</p>', 1)
    return subject, plain, rich, pdf, f"termstats-{day.isoformat()}.pdf"


def send(subject, plain, rich, pdf=None, pdf_name="termstats.pdf"):
    to, host = os.environ.get("TERMSTATS_REPORT_TO"), os.environ.get("TERMSTATS_REPORT_SSH")
    if not to or not host:
        sys.exit("daily-report: set TERMSTATS_REPORT_TO and TERMSTATS_REPORT_SSH (or use --print)")
    message = EmailMessage()
    message["To"], message["Subject"] = to, subject
    if os.environ.get("TERMSTATS_REPORT_FROM"):
        message["From"] = os.environ["TERMSTATS_REPORT_FROM"]
    message.set_content(plain)
    message.add_alternative(rich, subtype="html")
    if pdf:
        message.add_attachment(pdf, maintype="application", subtype="pdf", filename=pdf_name)
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]
    if os.environ.get("TERMSTATS_REPORT_SSH_KEY"):
        command += ["-o", "IdentitiesOnly=yes", "-i", os.path.expanduser(os.environ["TERMSTATS_REPORT_SSH_KEY"])]
    command += [host, "msmtp", "-t"]
    done = subprocess.run(command, input=message.as_bytes(), capture_output=True, timeout=120)
    if done.returncode != 0:
        sys.exit(f"daily-report: sending failed ({done.returncode}): {done.stderr.decode(errors='replace').strip()}")


def main(argv):
    day, explicit, just_print, pdf_path = dt.date.today() - dt.timedelta(days=1), False, False, None
    args = iter(argv)
    for arg in args:
        if arg == "--date":
            day, explicit = dt.date.fromisoformat(next(args, "")), True
        elif arg == "--print":
            just_print = True
        elif arg == "--pdf":
            pdf_path = next(args, None) or sys.exit(__doc__)
        else:
            sys.exit(__doc__)
    marker = state_dir() / "daily-report.sent"
    if not explicit and not just_print and not pdf_path and marker.exists() and marker.read_text().strip() >= day.isoformat():
        print(f"daily-report: {day} was already sent")
        return
    built = build(day)
    if built is None:
        print(f"daily-report: no history for {day} - nothing to send")
        return
    subject, plain, rich, pdf, pdf_name = built
    if pdf_path:
        if not pdf:
            sys.exit("daily-report: no PDF - no Chrome, Chromium, Edge or Brave found (TERMSTATS_REPORT_BROWSER)")
        pathlib.Path(pdf_path).write_bytes(pdf)
        print(f"daily-report: wrote {pdf_path} ({len(pdf) // 1024} KB)")
        return
    if just_print:
        print(subject + "\n\n" + plain)
        return
    send(subject, plain, rich, pdf, pdf_name)
    if not explicit:
        marker.write_text(day.isoformat() + "\n")
    print(f"daily-report: sent {day} to {os.environ['TERMSTATS_REPORT_TO']}")


if __name__ == "__main__":
    main(sys.argv[1:])
