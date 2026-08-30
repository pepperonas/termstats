# termstats

<div align="center">

**Beautiful terminal server dashboard with real-time charts.**

[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey?logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZD0iTTQgNGgxNnYxMkg0eiIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjZmZmIiBzdHJva2Utd2lkdGg9IjIiLz48cGF0aCBkPSJNMSAyMGgyMiIgc3Ryb2tlPSIjZmZmIiBzdHJva2Utd2lkdGg9IjIiLz48L3N2Zz4=)](https://github.com/pepperonas/termstats)

[![Linux](https://img.shields.io/badge/Linux-supported-success?logo=linux&logoColor=white)](https://github.com/pepperonas/termstats)
[![macOS](https://img.shields.io/badge/macOS-supported-success?logo=apple&logoColor=white)](https://github.com/pepperonas/termstats)
[![Windows](https://img.shields.io/badge/Windows-supported-success?logo=windows&logoColor=white)](https://github.com/pepperonas/termstats)

[![psutil](https://img.shields.io/badge/psutil-%E2%89%A55.9-orange?logo=python&logoColor=white)](https://github.com/giampaolo/psutil)
[![rich](https://img.shields.io/badge/rich-%E2%89%A513.0-purple?logo=python&logoColor=white)](https://github.com/Textualize/rich)
[![plotext](https://img.shields.io/badge/plotext-5.x-blue?logo=python&logoColor=white)](https://github.com/piccolomo/plotext)

[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/pepperonas/termstats/pulls)
[![GitHub stars](https://img.shields.io/github/stars/pepperonas/termstats?style=social)](https://github.com/pepperonas/termstats)
[![GitHub issues](https://img.shields.io/github/issues/pepperonas/termstats)](https://github.com/pepperonas/termstats/issues)
[![GitHub last commit](https://img.shields.io/github/last-commit/pepperonas/termstats)](https://github.com/pepperonas/termstats/commits)

</div>

---

<div align="center">
<img src="https://raw.githubusercontent.com/pepperonas/termstats/main/termstats.png" alt="termstats dashboard screenshot" width="800"/>
</div>

---

A single-command system dashboard that renders CPU, memory, disk, network, top processes, and
live history charts directly in your terminal. No browser, no GUI, no config needed.

```bash
termstats        # one snapshot
termstats -l     # live dashboard
```

## Features

- **CPU** — per-core usage bars + total + steal time (Linux)
- **Memory** — RAM & swap with used/total/available
- **Disk** — partition usage bars + read/write throughput
- **Network** — TX/RX throughput + total transferred + connections
- **Top Processes** — sorted by CPU, with color-coded thresholds
- **Live Charts** — CPU & network history as terminal line graphs (last 60s)
- **Cross-Platform** — Linux, macOS, Windows
- **Zero Config** — just install and run

## Installation

The command installed by every method below is **`termstats`**.

> **Note:** termstats is not on PyPI (yet) — install it from this repository.

### pipx (recommended — isolated, no venv juggling)

```bash
pipx install git+https://github.com/pepperonas/termstats.git
```

### pip (user install)

```bash
pip install --user git+https://github.com/pepperonas/termstats.git
```

### From source

```bash
git clone https://github.com/pepperonas/termstats.git
cd termstats
pipx install --editable .    # or: pip install -e .
```

An editable install points at the checkout, so `git pull` (or your own edits)
take effect immediately — no reinstall needed.

### Verify

```bash
termstats --version   # -> termstats 1.1.1
```

If your shell says `command not found`, see [Troubleshooting](#troubleshooting).

## Usage

```bash
# Single snapshot (samples for ~1s, prints once, exits)
termstats

# Live dashboard (full-screen, updates every second, Ctrl+C to exit)
termstats --live

# Live with custom interval
termstats --live --interval 3

# Short flags
termstats -l -i 2

# Module form (equivalent, requires the package + deps importable)
python -m termstats -l
```

### Options

| Flag | Description |
|------|-------------|
| `-l`, `--live` | Live updating dashboard (full-screen, alternate buffer) |
| `-i N`, `--interval N` | Update interval in seconds (default: 1) |
| `-V`, `--version` | Show version |
| `-h`, `--help` | Show help |

Flags may be combined in any order. Unknown arguments are ignored.

### Exit

Live mode runs until you press `Ctrl+C`; the alternate screen buffer is restored on exit,
so your scrollback stays intact.

## What the dashboard shows

| Panel | Content |
|-------|---------|
| **Header** | Hostname, OS, load average (1/5/15 min), CPU count, uptime, process count. The load figure turns yellow above `1× CPUs` and red above `2× CPUs`. |
| **CPU** | One bar per logical core, plus a total bar. On Linux an additional **Steal** bar shows hypervisor-stolen time, computed as a delta from `/proc/stat` between refreshes — the single most useful metric on an oversold VPS. |
| **Memory** | RAM used/total plus *available* (which, unlike "free", accounts for reclaimable cache). Swap is only shown when the machine actually has swap configured. |
| **Disk** | Usage bar per mounted partition (pseudo filesystems like `tmpfs`, `devtmpfs`, `squashfs`, `overlay` and `devfs` are skipped, duplicate mountpoints collapsed), plus system-wide read/write throughput measured between refreshes. |
| **Network** | TX/RX throughput measured between refreshes, lifetime totals, and the open connection count. |
| **CPU History** | Line chart of total CPU over the last 60 samples; the steal series is overlaid whenever it is non-zero. |
| **Network History** | Line chart of TX/RX in KB/s over the last 60 samples. |
| **Top Processes** | The 8 hungriest processes by CPU, with RSS. CPU values are tinted yellow above 10% and red above 50%. |

All usage bars share one color scale: **green** up to 70%, **yellow** 70–90%, **red** above 90%.

Throughput and steal are *rates*, so they need two samples. A single snapshot primes the
counters, waits one second, and then prints — which is why `termstats` takes about a second.
The two history charts say `Collecting data...` until the second sample arrives, so they stay
empty in snapshot mode by design; use `-l` to see them fill.

## Platform Support

| Feature | Linux | macOS | Windows |
|---------|:-----:|:-----:|:-------:|
| CPU per-core | Yes | Yes | Yes |
| CPU steal time | Yes | — | — |
| Memory & swap | Yes | Yes | Yes |
| Disk usage | Yes | Yes | Yes |
| Disk I/O speed | Yes | Yes | Yes |
| Network throughput | Yes | Yes | Yes |
| Connection count | Yes | Yes | Requires admin |
| Top processes | Yes | Yes | Yes |
| Live charts | Yes | Yes | Yes |
| Load average | Yes | Yes | Yes* |

\* Windows `getloadavg()` requires Python 3.12+

Panels that cannot be filled degrade quietly rather than failing: an unreadable partition is
skipped, and a denied connection count is omitted instead of shown as zero.

## Requirements

- Python 3.9+
- [psutil](https://github.com/giampaolo/psutil) — cross-platform system metrics
- [rich](https://github.com/Textualize/rich) — terminal formatting
- [plotext](https://github.com/piccolomo/plotext) — terminal plots (**5.x only**, see below)

plotext is pinned to `>=5.2,<6`. plotext **6.0.0** (released 2026-08-23, labelled beta
upstream) is a full rewrite that removed the 5.x top-level API — `clear_figure`, `plot`,
`ylim`, `plotsize`, `build` — which the charts are written against. An unpinned install picks
up 6.0.0 and the charts cannot be drawn.

A terminal of roughly 120×40 or larger gives the intended two-column layout; the panels
reflow on narrower terminals, and the charts scale with the window.

## Health Report (optional)

termstats includes a companion script for automated server health monitoring. When deployed on
a server, it can generate a PDF health report and send it via email on a schedule.

**What the health report covers:**

- Overall status with traffic-light ratings (OK / WARNING / CRITICAL)
- CPU, steal time, load, RAM, swap, disk — each evaluated against configurable thresholds
- SAR trend data (CPU & steal over time)
- Per-core CPU breakdown
- Network totals and connection count
- Top processes by CPU usage
- Docker container resource usage

**Default thresholds:**

| Metric | Warning | Critical |
|--------|---------|----------|
| CPU | 80% | 95% |
| Steal | 5% | 20% |
| RAM | 85% | 95% |
| Swap | 50% | 80% |
| Disk | 80% | 90% |
| Load | 2x CPUs | 4x CPUs |

When a threshold is breached, the email subject is prefixed with `[WARNING]` or `[CRITICAL]`
for easy filtering.

**Setup:** The health report script requires SMTP credentials and is not part of the installed
package. See [examples/celox-health-report.example.py](examples/celox-health-report.example.py)
for a template. Pair it with a systemd timer or cron job for automated scheduling.

## How It Works

termstats uses [psutil](https://github.com/giampaolo/psutil) to collect system metrics,
[rich](https://github.com/Textualize/rich) for terminal rendering with colored panels and
tables, and [plotext](https://github.com/piccolomo/plotext) for live line charts. On Linux,
CPU steal time is read directly from `/proc/stat`.

Rates (CPU, disk I/O, network) are deltas between consecutive samples, kept in module-level
`deque`s capped at 60 entries — that cap is what "last 60s" means at the default interval.
Live mode renders into rich's alternate-screen `Live` view at one frame per second.

### Project layout

```
termstats/
├── termstats/
│   ├── __init__.py      # version
│   ├── __main__.py      # python -m termstats
│   └── cli.py           # collectors, renderers, arg handling, entry point main()
├── examples/            # optional server health-report template (not packaged)
├── pyproject.toml       # metadata + the `termstats` console script
└── README.md
```

## Development

```bash
git clone https://github.com/pepperonas/termstats.git
cd termstats
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

termstats -l             # run it
python -m termstats -l   # same thing without the console script
```

Build distributables:

```bash
pip install build
python -m build          # -> dist/termstats-<version>-py3-none-any.whl + .tar.gz
```

When bumping the version, change it in **both** `pyproject.toml` and
`termstats/__init__.py` — the header and `--version` read the latter.

Uninstall:

```bash
pipx uninstall termstats     # or: pip uninstall termstats
```

## Naming & history

This project was called **`stats`** until August 2026 and had to give up that name: a
different, unrelated project (a self-hosted VPS security monitor) took over the `stats`
repository slot. Everything user-facing was renamed in one pass:

| | before | now |
|---|---|---|
| Repository | `pepperonas/stats` | `pepperonas/termstats` |
| Command | `stats` | `termstats` |
| Python package | `stats` | `termstats` |
| Distribution name | `stats-dashboard` | `termstats` |
| Module form | `python -m stats` | `python -m termstats` |

**Migrating from an old checkout:** remove the old install first, then install fresh —
the old and new packages are unrelated as far as pip is concerned and would otherwise
coexist.

```bash
pipx uninstall stats-dashboard 2>/dev/null || pip uninstall -y stats-dashboard
pipx install git+https://github.com/pepperonas/termstats.git
```

There is deliberately **no `stats` alias command**: reintroducing that name would bring back
exactly the ambiguity the rename resolved.

## Troubleshooting

**`termstats: command not found` after installing** — the install directory is not on your
`PATH`. pipx puts binaries in `~/.local/bin`; run `pipx ensurepath` (then open a new shell),
or add it yourself:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && exec zsh
```

**`ModuleNotFoundError: No module named 'psutil'` with `python -m termstats`** — you are
running a Python that does not have the dependencies. Use the console script, or activate the
environment termstats was installed into.

**Charts stay on `Collecting data...`** — expected in snapshot mode; they need two samples.
Run `termstats -l`.

**`AttributeError: module 'plotext' has no attribute 'clear_figure'`** — plotext 6.x is in
your environment. It removed the 5.x API the charts use. Fixed in 1.1.1, which pins
`plotext<6`; upgrade termstats, or downgrade the library in place:

```bash
pipx runpip termstats install 'plotext<6'    # or: pip install 'plotext<6'
```

**Charts read `Charts need plotext 5.x`** — same cause, but termstats 1.1.1+ now degrades to
this note instead of crashing. Same fix as above.

**Connection count missing on Windows** — `psutil.net_connections()` needs administrator
rights there. The row is omitted rather than shown as zero.

**Layout looks cramped or wrapped** — widen the terminal; the two-column grid assumes roughly
120 columns.

## License

[MIT](LICENSE) — Martin Pfeffer

## Author

**Martin Pfeffer**

- Website: [celox.io](https://celox.io)
- GitHub: [@pepperonas](https://github.com/pepperonas)
