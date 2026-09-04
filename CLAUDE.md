# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

`termstats` — a single-command terminal system dashboard (CPU, RAM, swap, disk, network, top
processes, live 60-sample history charts). Pure Python, no server, no config file, no state on
disk. Repo `pepperonas/termstats` (public, MIT).

**The rendering layer was rewritten in 0.2.0** (see the Layout section below). `cli.py` is
still one flat module, but it now has four distinct layers: capability detection → colour
ramp → meters/charts → a height-aware `rich.Layout`. Collectors take a **width** and return
rich renderables; they no longer return preformatted strings.

**A bare `termstats` runs the LIVE dashboard** (0.5 s refresh) — but only when stdout is a
terminal. Piped, redirected or under cron it prints one snapshot and exits, because a live
loop there never terminates. `--once/-1` forces the snapshot, `--live/-l` forces the loop.

## Layout: the dashboard must fit the terminal

This is the invariant the 0.2.0 rewrite exists to hold, and the one to re-check after any
change to a panel:

> The rendered dashboard is never taller than the terminal, never wider, has no blank lines,
> and draws no panel it cannot fill.

Before the rewrite `render_dashboard` composed a fixed grid with no idea how tall the screen
was — 79 lines on a 40-line terminal — and rich's alternate screen silently cropped the
overflow, so **the charts and the process list were never on screen** at ordinary sizes.
`tests/test_dashboard.py` sweeps thirteen geometries for all four properties.

How the height is spent, in order: header (1) → CPU row and the memory/network/disk stack →
charts (only with ≥13 spare lines) → processes (only with ≥5). Two rules make it hold:

- **Height is decided before the core columns are.** The CPU panel would rather be one tall
  column of wide bars than two short ones with a hole underneath, so `cpu_wanted` is computed
  first and `core_columns()` then fits the cores into it. Doing it the other way round leaves
  four blank lines in the CPU box on a ten-core machine.
- **The last section on screen takes `ratio=1`, not a fixed size.** Otherwise a dropped
  process panel leaves its lines as a blank strip (measured: 15 lines used out of 18).

`disk_in_stack` moves the disk panel out of the right-hand stack and across the full width
when the CPU column would otherwise be much shorter than the stack — same total height, no
hole. Panels are dropped **whole**; a bordered box with no room for content costs three lines
to say nothing.

⚠️ **The budget is a plan, not a guarantee.** A narrow terminal, a machine with several
mountpoints or Linux's extra steal row can each push the fixed sizes past the height
available, and the trailing `ratio` section is then squeezed to two lines and renders as a
bordered **stump**. `render_dashboard` drops sections from the bottom until the sum fits.
This was found by CI on Linux at 60×20 and could not be reproduced on macOS at all — the
sweep in `test_the_layout_holds_on_any_machine` fakes `IS_LINUX` and the core count for
exactly that reason.

⚠️ **A rich `Group` does not stretch.** It renders its children at their natural height and
leaves the remainder blank, so a `Group` inside a `ratio` section produces dead space. Both
the wide and the narrow branch nest `Layout`s instead, with the last card on `ratio=1`.

⚠️ Every `*_section_rows()` helper must predict exactly what its `get_*_section()` draws.
`render_dashboard` sizes the row from the prediction before the panel exists, so a
disagreement clips content or opens a gap. `test_cpu_height_prediction_matches_what_is_drawn`
pins this.

## Version reset to 0.1.0 (2026-09-04) — read this first

The version went **backwards**, from `1.1.4` to `0.1.0`, in the same release that made live
mode the default. Two reasons, both deliberate:

- termstats has never been on a package index (see the PyPI note below), so no downstream
  install could break.
- 1.x claims a stable interface. A tool that still changes its own default behaviour is a
  0.x tool. Under SemVer's 0.x rule a **minor** bump may break the CLI and a **patch** bump
  may not; 1.0.0 will be the first release that promises stability.

`CHANGELOG.md` (Keep a Changelog) records this, and the pre-reset releases are listed at its
bottom. The git history is untouched — commits still say "v1.1.4". Do not "fix" that.

⚠️ **Do not bump back over 1.0.0 casually.** Reaching 1.0.0 is a promise, not a milestone.

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
│   ├── test_args.py         # flag spellings, mode selection, rejected input
│   ├── test_badges.py       # LOC/test counting rules and the refusals
│   ├── test_charts.py       # plotext guard, degradation, series contents
│   ├── test_collectors.py   # psutil stubs, failure paths, /proc/stat steal
│   ├── test_dashboard.py    # real renders, chart layout, history accounting, encoding
│   ├── test_formatting.py   # bar_horizontal, _fmt_bytes_rate boundaries
│   ├── test_packaging.py    # version/changelog sync, pins, README claims, PayPal link
│   └── test_timing.py       # window label, frame scheduling, run_live/run_once
│   └── helpers.py           # plain(): render a renderable and strip the styling
├── tools/badges.py   # generates .github/badges/*.json (version, LOC, test count)
├── .github/
│   ├── badges/*.json                  # shields.io endpoint payloads, committed by CI
│   └── workflows/tests.yml            # Linux/macOS/Windows + py3.9
│   └── workflows/badges.yml           # refreshes + commits the badge payloads
├── CHANGELOG.md      # Keep a Changelog / SemVer
├── examples/celox-health-report.example.py   # server health report template, NOT packaged
├── pyproject.toml    # metadata + [project.scripts] termstats = "termstats.cli:main"
└── termstats.png     # README hero image (raw.githubusercontent URL)
```

`cli.py` is deliberately one flat module — collectors return `(text, *values)` tuples that
`render_dashboard()` composes into rich `Panel`s. There is no framework and no abstraction
layer; keep it that way unless the file outgrows ~600 lines.

## Version bumps

The version lives in **two** places and both must move together, and two more artefacts
have to follow:

- `pyproject.toml` → `version = "X.Y.Z"`
- `termstats/__init__.py` → `__version__ = "X.Y.Z"`
- `CHANGELOG.md` → a new `## [X.Y.Z] - YYYY-MM-DD` heading above the previous one
- `python tools/badges.py` → rewrites `.github/badges/version.json`

All four are pinned by `test_packaging.py` / `test_badges.py`, so a half-finished bump is a
red test, not a stale badge. Tag the release `vX.Y.Z`.

The dashboard header and `--version` read `__init__.py`; packaging reads `pyproject.toml`. A
mismatch used to be invisible until someone read the header.

**The three headline README badges are generated, never typed.** `tools/badges.py` writes
`.github/badges/{version,loc,tests}.json` in the shields.io *endpoint* schema, the README
points shields at those raw URLs, and `.github/workflows/badges.yml` re-runs the script on
every push to `main` and commits the result when a number moved (`[skip ci]` in the message,
so it cannot retrigger itself). A hard-coded `shields.io/badge/version-…` in the README is
now a **failing test** — that is what used to go stale.

⚠️ `tools/badges.py` **refuses to emit a zero test count.** Run it with an interpreter that
has pytest (`~/.local/pipx/venvs/termstats/bin/python tools/badges.py` on this Mac); the
system python3 has no psutil and the collection fails loudly rather than publishing "0 unit
tests". `--check` exits 1 when the files are stale and writes nothing.

## Local install / running

```bash
pipx install --editable .    # dev: edits take effect immediately, no reinstall
termstats                    # live (in a terminal)
termstats --once             # single snapshot
termstats -i 2               # live, slower refresh
python -m termstats          # same, needs deps importable in that interpreter
```

Installed on this Mac via `pipx install --editable /Users/martin/claude/termstats`
→ `~/.local/bin/termstats` (on PATH). ⚠️ Because it is **editable**, moving or deleting this
checkout breaks the installed command.

`python3 -m termstats` with the *system* Python fails (`No module named 'psutil'`) — the deps
live in the pipx venv, not in Homebrew's Python. That is expected, not a bug.

## Testing

**281 pytest tests** in `tests/` (the badge is generated; this number is prose and may lag),
all pure unit tests — no sleeps, no live terminal, no
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

⚠️ **Two test-writing traps from the 0.2.0 round, both mine:**

- `re.search(r"proc \d+\S", head)` to catch "proc 7080.5s" **always matches**, because `\S`
  happily matches the number's own digits. The character after the run has to be excluded
  explicitly: `proc \d+[^\d\s]`.
- `"x" * 200` is the wrong probe for "does a long process name wrap": rich truncates an
  unbroken run anyway. Real process names contain **spaces**, and those wrap the row onto
  five lines without `no_wrap`. Two mutations passed against the x-string version.

⚠️ **Mutation-test every new pin.** Nineteen mutations have been run against this suite
(dropping `-live`, silencing the unknown-option error, narrowing the chart `except`, relaxing
the plotext pin, re-adding a `stats` alias, desyncing the two version strings, making the
terminal check always say yes, removing `--once`, dropping the live/once conflict, handing
charts to rich as a bare string, widening the chart by one column, hard-coding the chart
title, removing the scheduler resync, sleeping a flat interval, publishing a zero test count,
counting comments as code, staling the version badge, drifting the changelog, claiming
Production/Stable at 0.x). The 0.2.0 rewrite added twenty-two more (height budget, the
process-panel guard, the trailing `ratio`, the `nobrowse` filter, the macOS Data
substitution, a flat-coloured bar, the eighth-blocks, the sliced annotation, the braille
marker, the black chart background, the time axis, the ASCII path, the one-line ASCII chart,
the core-column preference, blind mountpoint cutting, the header collision, charts as a bare
string, the chart width, the heat strip, name wrapping, the inline bar, and a lying height
prediction). All are caught **now** — several were not at first:

**the PayPal pin was green-blind.** The README carries two donate links (headline badge and
Support section) and the test used `re.search`, so it validated whichever it found first;
breaking the *other* one changed nothing. It now checks that *every* `paypal.com/donate` URL
carries the account, that no unchecked PayPal URL exists, and that the Support section holds
one of its own. Four separate mutations confirm it. A test you have not watched fail is not
a guarantee.

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
`termstats | head` never reaches, and so did the `Text.from_ansi` layout bug above. To actually cover it, either drive live mode under a real
pty (rich renders nothing to a plain pipe, so a `subprocess.PIPE` run proves nothing) or
prime the deques and call the collectors directly:

```python
from termstats import cli
for i in range(60):
    cli.cpu_history.append(i); cli.net_sent_history.append(1.0); cli.net_recv_history.append(2.0)
print(cli.get_cpu_chart(70, 12))
```

⚠️ A `pty.fork()` window starts at **80×24 whatever `$COLUMNS` says**, and plotext clamps to
the real window size. Set it explicitly or the charts come back narrow and the titles look
truncated when they are not:

```python
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
```

⚠️ Do not assert on **line widths** from a pty capture either: `Live(screen=True)` repositions
the cursor instead of emitting newlines, so consecutive frames concatenate and every line
looks 300 characters wide. Measure widths against a fixed-width `rich.Console` (which is what
`test_dashboard.py` does); use the pty only for behaviour — refresh cadence, alternate screen,
Ctrl+C, exit status.

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
- **The header carries a wall clock.** Without it a frozen dashboard and an idle machine look
  identical. Its absence after the rewrite was found by watching the live view in a pty, not
  by any unit test — the clock is now pinned.
- ⚠️ **`Text(no_wrap=True, overflow="crop")` is DISCARDED by `Console.print` for a bare
  Text.** `Console._collect_renderables` pushes loose Text objects through `Text.join`, which
  builds a fresh Text and drops both attributes. Inside a `Panel` or `Layout` —
  the only way the dashboard ever renders them — `__rich_console__` is called directly and
  honours them. A test that prints a meter on its own will show it wrapping when it does not;
  `tests/helpers.py::plain` passes the attributes explicitly for that reason.
- ⚠️ **`overflow="crop"` hides an over-wide chart instead of wrapping it.** That makes the
  "one column too wide" defect invisible to any line-width assertion — the plot frame is
  simply cut off the right edge. `test_the_chart_is_sized_to_fit_inside_its_panel` pins the
  arithmetic directly, and a counter-check looks for plotext's own closing corner.
- **plotext has no pure-ASCII mode.** Its markers include `dot`, `at`, `dollar` and single
  characters, but the *axes* are always box-drawing glyphs. There is therefore no marker
  choice that yields ASCII, which is why `_ascii_chart()` draws columns by hand rather than
  the fallback dropping the charts.
- **plotext's `theme("clear")`, not `"dark"`.** "dark" fills the plot with a black rectangle
  that sits inside the panel whatever the terminal's own background is. Check for it on the
  parsed **styles**, not on raw escape text — rich re-encodes the colour for the target
  terminal, so matching a literal `\x1b[48;5;0m` passes by accident.
- **Colour quantisation is rich's job, not ours.** `ramp()` returns truecolor hex and rich
  converts it to the nearest 256- or 16-colour value. ⚠️ Verifying that is easy to get wrong:
  `Style.parse` is `lru_cache`d and `Style.render` caches `self._ansi` from the **first**
  colour system it sees, so a loop over colour systems reusing one Style reports truecolor
  every time. Build a fresh `Style(color=...)` per system.
- **The header string is `" TERMSTATS "`** plus the `(bottled 🍻 by Martin Pfeffer - celox.io)`
  branding — the branding is deliberate, don't strip it as noise.
- **`isatty()` decides the mode, and that must stay true.** A bare `termstats` runs live in a
  terminal and prints one snapshot anywhere else. The CI smoke step passes `--once`
  explicitly *on purpose*: a bare invocation would also snapshot there, but relying on that
  would make the step hang forever the day the terminal check regresses. Screen recorders and
  `script(1)` allocate a pty, so they get the live view — that is correct, use `--once`.
- **`HISTORY_LEN` is a sample count and the chart titles say the real window.** 60 samples
  at 0.5 s is `last 30s`; `_window_label()` computes it and flips to minutes above 90 s. The
  old hard-coded "last 60s" was true for exactly one interval value.
- **Frames are scheduled on a grid, not with a flat sleep.** `_schedule_tick()` returns
  `interval − render time`, and resyncs to *now* if a render overran — otherwise the loop
  banks a backlog and fires a burst of instant redraws to "catch up". A flat sleep drifts by
  the render cost, ~6% at the default interval.
- ⚠️ **plotext output must reach rich as `Text.from_ansi`, never as a plain `str`.** This was
  a live bug for the whole life of the project, found only by rendering into a real pty while
  verifying the new titles. plotext embeds ~190 escape bytes per line; rich counts those as
  printable cells, so a 70-column chart measured 259 wide, got re-wrapped into ragged
  fragments, the axis broke apart and the title was cut mid-word (`CPU Usage (last`). It is
  invisible in the unit tests unless you assert on *line widths* — `test_dashboard.py` now
  does, at five terminal widths.
- **The chart width must leave room for the panel chrome**: `(tw - 1) // 2 - 4` — two columns
  of border, two of padding, and one column of grid gap shared between the pair. `tw // 2 - 4`
  is one too many and triggers the rewrap above even with `from_ansi`.

## Deploy

None. It is a local CLI: no VPS, no Pi, no systemd unit, no service to restart. "Shipping" is
`git push`; users install from the git URL.

The one server-side artefact is `examples/celox-health-report.example.py` — a template that
needs SMTP credentials, is excluded from the package, and must **never** be committed with real
credentials filled in.
