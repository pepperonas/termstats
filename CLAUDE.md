# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

`termstats` — a single-command terminal system dashboard (CPU, RAM, swap, disk, network, top
processes, live 60-sample history charts). Pure Python, no server, no config file, no state on
disk. Repo `pepperonas/termstats` (public, MIT).

## The rename (2026-08-30) — read this first

The project was called **`stats`** and had to surrender that name to an unrelated project (the
celox VPS security monitor, now `pepperonas/stats`). Renamed in one pass:

| | before | now |
|---|---|---|
| Repo | `pepperonas/stats` | `pepperonas/termstats` |
| Command | `stats` | `termstats` |
| Package dir | `stats/` | `termstats/` |
| Dist name | `stats-dashboard` | `termstats` |
| Screenshot | `stats.png` | `termstats.png` |

⚠️ **There is intentionally no `stats` alias console script.** Adding one back would restore
exactly the ambiguity the rename removed. If a `stats` command is ever wanted again, it needs a
deliberate decision, not a convenience commit.

⚠️ **Neither `stats-dashboard` nor `termstats` was ever actually published to PyPI** (verified
via the PyPI JSON API on 2026-08-30 — both return `{"message": "Not Found"}`). The old README
led with `pip install stats-dashboard`, which never worked for anybody. Install docs therefore
point at the git URL. Do not re-add PyPI badges or `pip install termstats` until a release is
actually uploaded — `pypi.org/project/<name>/` returns **200 for everything** through this
network path, so a status code is not proof; query `https://pypi.org/pypi/<name>/json`.

## Layout

```
termstats/
├── termstats/
│   ├── __init__.py   # __version__ — the single source the header and --version read
│   ├── __main__.py   # python -m termstats
│   └── cli.py        # everything: collectors, rendering, arg parsing, main()
├── examples/celox-health-report.example.py   # server health report template, NOT packaged
├── pyproject.toml    # metadata + [project.scripts] termstats = "termstats.cli:main"
└── termstats.png     # README hero image (raw.githubusercontent URL)
```

`cli.py` is deliberately one flat module — collectors return `(text, *values)` tuples that
`render_dashboard()` composes into rich `Panel`s. There is no framework and no abstraction
layer; keep it that way unless the file outgrows ~600 lines.

## Version bumps

The version lives in **two** places and both must move together:

- `pyproject.toml` → `version = "X.Y.Z"`
- `termstats/__init__.py` → `__version__ = "X.Y.Z"`

The dashboard header and `--version` read `__init__.py`; packaging reads `pyproject.toml`. A
mismatch is invisible until someone reads the header.

## Local install / running

```bash
pipx install --editable .    # dev: edits take effect immediately, no reinstall
termstats                    # snapshot
termstats -l                 # live
python -m termstats -l       # same, needs deps importable in that interpreter
```

Installed on this Mac via `pipx install --editable /Users/martin/claude/termstats`
→ `~/.local/bin/termstats` (on PATH). ⚠️ Because it is **editable**, moving or deleting this
checkout breaks the installed command.

`python3 -m termstats` with the *system* Python fails (`No module named 'psutil'`) — the deps
live in the pipx venv, not in Homebrew's Python. That is expected, not a bug.

## Testing

There is **no test suite**. Verification is manual and must actually be run, not assumed:

```bash
termstats --version && termstats --help
COLUMNS=150 LINES=45 termstats | head -60   # renders all panels non-interactively
```

Snapshot mode always shows `Collecting data...` in both charts — rates need two samples, and a
snapshot only takes one after priming. That is correct behaviour; do not "fix" it.

## Gotchas

- **Rates need two samples.** CPU %, disk I/O and network throughput are deltas held in
  function attributes (`get_disk_section._last_io`, `get_network_section._last`) and
  module-level `deque(maxlen=60)`s. First call always yields 0 — hence `_prime_measurements()`
  plus a 1 s sleep before the first render. `HISTORY_LEN = 60` is what "last 60s" means at the
  default interval; changing the interval silently changes the window the charts cover.
- **Steal time is Linux-only** and is read by hand from `/proc/stat` field 8 as a delta; on
  macOS/Windows `_read_steal_pct()` returns 0.0 and the bar is not drawn.
- **Never let a collector raise.** Everything that touches the OS is wrapped: an unreadable
  partition is skipped, `psutil.AccessDenied` on `net_connections()` (Windows without admin)
  omits the row rather than printing 0. A dashboard that dies on one bad mountpoint is worse
  than one missing a line.
- **Live mode uses rich's alternate screen** (`Live(..., screen=True)`); `KeyboardInterrupt` is
  caught so the terminal is restored and scrollback survives.
- **`-i` with a non-numeric value raises an uncaught `ValueError`**, and `-i` as the final
  argument is silently ignored. Known, pre-existing, unfixed — argument handling is a hand-
  rolled `sys.argv` loop, not `argparse`.
- **The header string is `" TERMSTATS "`** plus the `(bottled 🍻 by Martin Pfeffer - celox.io)`
  branding — the branding is deliberate, don't strip it as noise.

## Deploy

None. It is a local CLI: no VPS, no Pi, no systemd unit, no service to restart. "Shipping" is
`git push`; users install from the git URL.

The one server-side artefact is `examples/celox-health-report.example.py` — a template that
needs SMTP credentials, is excluded from the package, and must **never** be committed with real
credentials filled in.
