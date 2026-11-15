#!/usr/bin/env python3
"""A daily mail: what the machine did yesterday, and which computer fits.

Reads the history `termstats record` keeps, sums up one day, adds the current
`termstats recommend --now` report and sends both as one mail. Standard library only
(Python 3.9+); a template, not part of the package.

The mail goes out through msmtp on another host, reached over ssh - handy when a server
already has SMTP set up and the laptop has not:

    TERMSTATS_REPORT_TO=you@example.org            recipient (required to send)
    TERMSTATS_REPORT_SSH=user@server               where msmtp runs (required to send)
    TERMSTATS_REPORT_SSH_KEY=~/.ssh/id_ed25519     optional: the key to use
    TERMSTATS_REPORT_FROM=reports@example.org      optional: From header (default: msmtp's)
    TERMSTATS_NATIVE_BIN=/path/termstats-native    optional: where the native core is

    daily-report.example.py              yesterday, once (a marker remembers what was sent)
    daily-report.example.py --date 2026-11-15      that day, sent regardless of the marker
    daily-report.example.py --print      print the text, send nothing

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
    """(text report, the middle tier's pick as a short string or None)."""
    binary = native_bin()
    if not binary:
        return "termstats-native ist nicht installiert - keine Empfehlung.", None
    text = subprocess.run([binary, "recommend", "--now"], capture_output=True, text=True, timeout=60)
    short = None
    try:
        data = json.loads(subprocess.run([binary, "recommend", "--now", "--json"], capture_output=True, text=True, timeout=60).stdout)
        tiers = next(v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict) and "tier" in v[0])
        pick = tiers[1]["pick"] if len(tiers) > 1 else None
        if pick:
            short = f'{pick["name"]}, {pick["ram_gb"]} GB, {pick["price_eur"]} EUR'
    except (ValueError, StopIteration, KeyError, IndexError, subprocess.SubprocessError):
        pass
    return (text.stdout or text.stderr).rstrip() or "Keine Empfehlung erhalten.", short


def build(day):
    machine, rows = read_day(day)
    if not rows:
        return None
    summary = summarise(machine, rows)
    pairs = lines_of(summary, machine)
    report, short = recommendation()
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
    return subject, plain, rich


def send(subject, plain, rich):
    to, host = os.environ.get("TERMSTATS_REPORT_TO"), os.environ.get("TERMSTATS_REPORT_SSH")
    if not to or not host:
        sys.exit("daily-report: set TERMSTATS_REPORT_TO and TERMSTATS_REPORT_SSH (or use --print)")
    message = EmailMessage()
    message["To"], message["Subject"] = to, subject
    if os.environ.get("TERMSTATS_REPORT_FROM"):
        message["From"] = os.environ["TERMSTATS_REPORT_FROM"]
    message.set_content(plain)
    message.add_alternative(rich, subtype="html")
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]
    if os.environ.get("TERMSTATS_REPORT_SSH_KEY"):
        command += ["-o", "IdentitiesOnly=yes", "-i", os.path.expanduser(os.environ["TERMSTATS_REPORT_SSH_KEY"])]
    command += [host, "msmtp", "-t"]
    done = subprocess.run(command, input=message.as_bytes(), capture_output=True, timeout=120)
    if done.returncode != 0:
        sys.exit(f"daily-report: sending failed ({done.returncode}): {done.stderr.decode(errors='replace').strip()}")


def main(argv):
    day, explicit, just_print = dt.date.today() - dt.timedelta(days=1), False, False
    args = iter(argv)
    for arg in args:
        if arg == "--date":
            day, explicit = dt.date.fromisoformat(next(args, "")), True
        elif arg == "--print":
            just_print = True
        else:
            sys.exit(__doc__)
    marker = state_dir() / "daily-report.sent"
    if not explicit and not just_print and marker.exists() and marker.read_text().strip() >= day.isoformat():
        print(f"daily-report: {day} was already sent")
        return
    built = build(day)
    if built is None:
        print(f"daily-report: no history for {day} - nothing to send")
        return
    subject, plain, rich = built
    if just_print:
        print(subject + "\n\n" + plain)
        return
    send(subject, plain, rich)
    if not explicit:
        marker.write_text(day.isoformat() + "\n")
    print(f"daily-report: sent {day} to {os.environ['TERMSTATS_REPORT_TO']}")


if __name__ == "__main__":
    main(sys.argv[1:])
