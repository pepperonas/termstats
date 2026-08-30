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
├── tests/
│   ├── conftest.py          # autouse state reset + chart-capture fixtures
│   ├── test_args.py         # flag spellings, rejected input, help/version
│   ├── test_charts.py       # plotext guard, degradation, series contents
│   ├── test_collectors.py   # psutil stubs, failure paths, /proc/stat steal
│   ├── test_dashboard.py    # one real render, history accounting, brand line
│   ├── test_formatting.py   # bar_horizontal, _fmt_bytes_rate boundaries
│   └── test_packaging.py    # version sync, dependency pins, the rename, README claims
├── .github/workflows/tests.yml               # Linux/macOS/Windows + py3.9
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
mismatch used to be invisible until someone read the header — `test_packaging.py` now fails
on it, and on a stale README version badge or `# -> termstats X.Y.Z` line. **The unit-test
count in the README badge is NOT checked** (parametrised cases make an exact pin brittle);
update `unit%20tests-<n>` by hand when you add tests.

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

**159 pytest tests** in `tests/`, all pure unit tests — no sleeps, no live terminal, no
network, well under a second:

```bash
pip install -e ".[dev]" && pytest        # or: ~/.local/pipx/venvs/termstats/bin/pytest
```

pytest is injected into the pipx venv on this Mac (`pipx inject termstats pytest`), so the
suite runs against the same resolved dependency set the command itself uses — which is the
point, given that the last two bugs were a dependency resolve and an argument parser.

`tests/conftest.py` carries the one fixture everything depends on: **`clean_module_state` is
autouse and resets the four history deques, the two `_steal_last_*` globals and the
`_last_io`/`_last`/`_last_time` function attributes.** Rate state survives between calls by
design, so without it a collector test passes or fails depending on what ran before it.

⚠️ **Mutation-test every new pin.** Thirteen mutations were run against this suite
(dropping `-live`, silencing the unknown-option error, narrowing the chart `except`, relaxing
the plotext pin, re-adding a `stats` alias, desyncing the two version strings, …) and all
thirteen were caught. A test you have not watched fail is not a guarantee — this repo's
sibling projects have shipped grün-blind pins more than once.

⚠️ Two traps hit while writing these: an anchor string whose indentation did not match
reports "ANCHOR MISSING" and quietly proves nothing, and `f"p{i}" in output` matched `p2`
inside `p29` — count rows (`Table.row_count`), not name substrings.

CI (`.github/workflows/tests.yml`) runs the suite on Linux/macOS/Windows plus Python 3.9, and
prints the resolved psutil/plotext/rich versions — a fresh resolve is exactly how the
plotext 6.0.0 break arrived.

The manual checks still matter for anything the suite deliberately does not touch (real
terminal output, the alternate screen):

```bash
termstats --version && termstats --help
COLUMNS=150 LINES=45 termstats | head -60   # renders all panels non-interactively
```

⚠️ Snapshot mode does **not** exercise the chart code — both charts short-circuit to
`Collecting data...`. The `clear_figure` crash of 2026-08-30 lived entirely in a path that
`termstats | head` never reaches. To actually cover it, either drive live mode under a real
pty (rich renders nothing to a plain pipe, so a `subprocess.PIPE` run proves nothing) or
prime the deques and call the collectors directly:

```python
from termstats import cli
for i in range(60):
    cli.cpu_history.append(i); cli.net_sent_history.append(1.0); cli.net_recv_history.append(2.0)
print(cli.get_cpu_chart(70, 12))
```

⚠️ When driving live mode under a pty, **keep draining the master fd while waiting for the
child to exit**. Stop reading and the pty buffer fills; the child then blocks in `write()`
during rich's screen teardown and never processes the `SIGINT` — the harness hangs and it
looks like the app ignores Ctrl+C. It does not.

Two shell traps that cost time here: **zsh does not word-split unquoted parameters**, so
`for a in "-l -i abc"; do termstats $a; done` passes one 11-character argument and every case
comes back "unknown option"; and `wait %1` in a non-interactive shell returns 127 — drive
multi-step process tests from Python, not from a shell loop.

Snapshot mode always shows `Collecting data...` in both charts — rates need two samples, and a
snapshot only takes one after priming. That is correct behaviour; do not "fix" it.

## Gotchas

- **plotext is pinned `<6` — do not relax it.** plotext **6.0.0** (PyPI 2026-08-23, labelled
  beta upstream) is a full rewrite: the 5.x top-level API the charts are written against
  (`clear_figure`, `plot`, `ylim`, `plotsize`, `build`) is gone, replaced by a `plt.figure`
  object with `line`/`signal`/`clear`/`build`. With the old unpinned `plotext>=5.2`, a fresh
  install picked 6.0.0 and `termstats --live` died with
  `AttributeError: module 'plotext' has no attribute 'clear_figure'` (fixed in 1.1.1). The
  latest usable release is **5.3.2**. Porting to the 6.x API is a real option later, but not
  while it is beta — and it would break every 5.x environment.
- **Charts fail soft.** `_render_chart()` checks `_PLOTEXT_5` (the five 5.x attributes) and
  wraps the build in `try/except`, so a library break costs a chart, not the dashboard —
  the same rule as the OS collectors below. Verify that path by stubbing
  `sys.modules["plotext"]` with an empty module before importing `termstats.cli`.
- **Windows redirects stdout as cp1252 — widen it before printing.** The dashboard is drawn
  from `█ ░ ╭` and the header carries 🍻; none of that exists in cp1252, so `termstats > out.txt`
  died with `UnicodeEncodeError` on Windows while a real console was fine (rich reaches a
  console through the win32 API). `_ensure_console_encoding()` runs first in `main()` and
  reconfigures only a stream that provably cannot carry `_GLYPH_PROBE`, with
  `errors="replace"` so it can never raise. **Add any new non-ASCII output glyph to
  `_GLYPH_PROBE`** — a test enforces that the four current ones are in it. Found by CI on its
  very first run, which is the argument for having CI at all.
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
- **Argument handling is a hand-rolled `sys.argv` loop, not `argparse` — so it validates by
  hand.** Until 1.1.2 it matched only the exact flag spellings and **silently ignored
  everything else**: `termstats -live` ran a *snapshot*, which shows `Collecting data...` in
  both history panels and reads as "live mode is broken" (user report, 2026-08-30). Two
  siblings of the same defect: `-i` as the last argument was dropped, and `-i abc` raised an
  uncaught `ValueError`. Now every token must match something, long options are accepted with
  one dash too (`-live`, `-interval`, `-version`, `-help`), and bad input goes to stderr with
  exit code 2 via `_fail()`. If you add a flag, add it to the matching `_*_FLAGS` tuple —
  anything not in a tuple is now a hard error, which is the point.
- **The header string is `" TERMSTATS "`** plus the `(bottled 🍻 by Martin Pfeffer - celox.io)`
  branding — the branding is deliberate, don't strip it as noise.

## Deploy

None. It is a local CLI: no VPS, no Pi, no systemd unit, no service to restart. "Shipping" is
`git push`; users install from the git URL.

The one server-side artefact is `examples/celox-health-report.example.py` — a template that
needs SMTP credentials, is excluded from the package, and must **never** be committed with real
credentials filled in.
