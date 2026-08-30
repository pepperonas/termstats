# termstats

<div align="center">

**Beautiful terminal server dashboard with real-time charts.**

[![PyPI version](https://img.shields.io/pypi/v/stats-dashboard?color=blue&label=PyPI)](https://pypi.org/project/stats-dashboard/)
[![Python](https://img.shields.io/pypi/pyversions/stats-dashboard?logo=python&logoColor=white)](https://pypi.org/project/stats-dashboard/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey?logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZD0iTTQgNGgxNnYxMkg0eiIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjZmZmIiBzdHJva2Utd2lkdGg9IjIiLz48cGF0aCBkPSJNMSAyMGgyMiIgc3Ryb2tlPSIjZmZmIiBzdHJva2Utd2lkdGg9IjIiLz48L3N2Zz4=)](https://github.com/pepperonas/termstats)

[![Linux](https://img.shields.io/badge/Linux-supported-success?logo=linux&logoColor=white)](https://github.com/pepperonas/termstats)
[![macOS](https://img.shields.io/badge/macOS-supported-success?logo=apple&logoColor=white)](https://github.com/pepperonas/termstats)
[![Windows](https://img.shields.io/badge/Windows-supported-success?logo=windows&logoColor=white)](https://github.com/pepperonas/termstats)

[![psutil](https://img.shields.io/badge/psutil-%E2%89%A55.9-orange?logo=python&logoColor=white)](https://github.com/giampaolo/psutil)
[![rich](https://img.shields.io/badge/rich-%E2%89%A513.0-purple?logo=python&logoColor=white)](https://github.com/Textualize/rich)
[![plotext](https://img.shields.io/badge/plotext-%E2%89%A55.2-blue?logo=python&logoColor=white)](https://github.com/piccolomo/plotext)

[![Code Style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/pepperonas/termstats/pulls)
[![GitHub stars](https://img.shields.io/github/stars/pepperonas/termstats?style=social)](https://github.com/pepperonas/termstats)
[![GitHub issues](https://img.shields.io/github/issues/pepperonas/termstats)](https://github.com/pepperonas/termstats/issues)
[![GitHub last commit](https://img.shields.io/github/last-commit/pepperonas/termstats)](https://github.com/pepperonas/termstats/commits)

</div>

---

<div align="center">
<img src="https://raw.githubusercontent.com/pepperonas/termstats/main/stats.png" alt="termstats dashboard screenshot" width="800"/>
</div>

---

A single-command system dashboard that renders CPU, memory, disk, network, top processes, and live history charts directly in your terminal. No browser, no GUI, no config needed.

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

### pip (recommended)

```bash
pip install stats-dashboard
```

### pipx (isolated install)

```bash
pipx install stats-dashboard
```

### From source

```bash
git clone https://github.com/pepperonas/termstats.git
cd termstats
pip install .
```

## Usage

```bash
# Single snapshot
stats

# Live dashboard (updates every second, Ctrl+C to exit)
stats --live

# Live with custom interval
stats --live --interval 3

# Short flags
stats -l -i 2
```

### Options

| Flag | Description |
|------|-------------|
| `-l`, `--live` | Live updating dashboard |
| `-i N`, `--interval N` | Update interval in seconds (default: 1) |
| `-V`, `--version` | Show version |
| `-h`, `--help` | Show help |

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

## Requirements

- Python 3.9+
- [psutil](https://github.com/giampaolo/psutil) — cross-platform system metrics
- [rich](https://github.com/Textualize/rich) — terminal formatting
- [plotext](https://github.com/piccolomo/plotext) — terminal plots

## Screenshots

### Live Mode (`stats -l`)

Live mode renders real-time CPU and network history as line charts, auto-updating every second. Press `Ctrl+C` to exit.

## Health Report (optional)

`stats` includes a companion script for automated server health monitoring. When deployed on a server, it can generate a PDF health report and send it via email on a schedule.

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

When a threshold is breached, the email subject is prefixed with `[WARNING]` or `[CRITICAL]` for easy filtering.

**Setup:** The health report script requires SMTP credentials and is not included in the pip package. See [examples/celox-health-report.example.py](examples/celox-health-report.example.py) for a template. Pair it with a systemd timer or cron job for automated scheduling.

## How It Works

`stats` uses [psutil](https://github.com/giampaolo/psutil) to collect system metrics, [rich](https://github.com/Textualize/rich) for terminal rendering with colored panels and tables, and [plotext](https://github.com/piccolomo/plotext) for live line charts. On Linux, CPU steal time is read directly from `/proc/stat`.

## License

[MIT](LICENSE) — Martin Pfeffer

## Author

**Martin Pfeffer**

- Website: [celox.io](https://celox.io)
- GitHub: [@pepperonas](https://github.com/pepperonas)
