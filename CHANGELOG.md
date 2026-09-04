# Changelog

All notable changes to termstats are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version is below 1.0.0, a **minor** bump (`0.x.0`) may contain breaking changes
and a **patch** bump (`0.1.x`) never does. The first release that promises a stable command
line will be 1.0.0.

## [Unreleased]

## [0.2.0] - 2026-09-05

A visual rewrite of the rendering layer. The command line is unchanged; what the dashboard
looks like, and how much of it you can actually see, is not.

### Fixed

- **The dashboard now fits the terminal.** It used to compose a fixed grid with no idea how
  tall the screen was: 59 lines at 140×50, **79 at 120×40, 102 at 100×30**. In live mode
  rich's alternate screen crops the overflow, so on an ordinary terminal the history charts
  and the process list — the two panels the tool exists for — were **never on screen**.
  Layout is now height-aware; panels are dropped whole when they do not fit, never squeezed
  to an empty frame, and the last one on screen takes the remaining lines.
- **The dead right-hand column is gone.** A shared grid row forced both columns to the
  height of the taller panel, so a 2-line network panel sat beside a 16-line disk panel and
  **39–61% of all lines had a completely empty right half**. Memory, network and disk are
  now a vertical stack that packs.
- **plotext output reached rich as a plain string**, so rich counted the ~190 escape bytes
  per line as printable cells: a 70-column chart measured 259 wide, was re-wrapped into
  ragged fragments, the axis broke apart and the title was cut mid-word. Charts now go
  through `Text.from_ansi`. This had been broken for the life of the project.
- **macOS listed nine partitions for one physical disk** (Preboot, Update, VM, xarts,
  iSCPreboot, Hardware, Data …), four of them reporting the same total because APFS shares
  space across a container. Volumes carrying Apple's own `nobrowse`/`dontbrowse` flag are
  hidden, and `/` reports the Data volume of its APFS group — otherwise the sealed
  read-only system snapshot shows 11G used on a disk that is 98% full.
- Long process names no longer wrap the row onto five lines.
- A meter too narrow for its annotation now drops it instead of slicing it: a cut-off
  "421.4G/460." still looks like a number.
- The header no longer glues the process count to the refresh interval ("proc 7080.5s")
  on terminals between roughly 87 and 100 columns wide.

### Changed

- **Charts are drawn in Unicode braille** (2×4 dots per cell — four times the vertical
  resolution of the old block markers, and what btop uses), on a transparent plotext theme
  so the plot no longer paints a black rectangle inside the panel.
- **The x axis is labelled in time** (`-30s … now`) instead of sample indices
  (`1.0 15.8 30.5 45.2 60.0`), and the window label moved from inside the plot — where it
  overlapped the data — into the panel title.
- **One colour ramp for everything**: cool → warm → hot, applied per cell so a bar reads as
  a scale rather than a block, and reused for the load average and the process table.
  Truecolor is emitted and rich quantises it down on 256- and 16-colour terminals.
- Meters are one line instead of two, sized to the panel instead of a fixed 40 cells, and
  accurate to an eighth of a character.
- Per-core meters prefer a single tall column, split into up to four when they would not
  fit, and collapse to a one-line heat strip on machines with too many cores to list.
- The process table gained an inline CPU bar in the space the name column was wasting.
- The header carries a wall clock again — without it a frozen dashboard and an idle machine
  look identical.

### Added

- **An ASCII fallback for terminals that cannot draw the glyphs.** plotext frames every plot
  in box-drawing characters, so no marker choice yields pure ASCII; the charts are drawn by
  hand in that mode rather than dropped.
- Tests grew from 281 to 410, including a size sweep that pins the fit at thirteen terminal
  geometries.


## [0.1.0] - 2026-09-04

The version was deliberately reset from `1.1.4` to `0.1.0`. termstats has never been
published to a package index, so nothing downstream can break; and a 1.x number promised a
stability guarantee the command line had not earned — this release changes its default
behaviour, which is exactly what SemVer's 0.x range exists for. The pre-reset history stays
in the git log.

### Changed

- **`termstats` now runs the live dashboard by default.** Previously a bare invocation
  printed a single snapshot and `-l` was needed for the live view.
- **Snapshot mode is chosen automatically when stdout is not a terminal.** Pipes,
  redirects and cron get one snapshot and an exit rather than an endless loop, so
  `termstats > report.txt` still terminates.
- **Default refresh interval is now 0.5 s** (was 1 s).
- **Chart titles state the real history window.** `HISTORY_LEN` counts samples, not
  seconds, so the hard-coded "last 60s" was correct for exactly one interval value. The
  titles are now computed — `last 30s` at the default, `last 3m` at `-i 3`.
- **Frames are scheduled on a fixed cadence.** Sleeping a flat interval after each render
  made the real period `interval + render time`; the loop now subtracts the render cost and
  resynchronises instead of banking a backlog when a render overruns.
- rich's `refresh_per_second` follows the interval instead of sitting at 1.
- `Development Status` classifier moved from *Production/Stable* to *Beta*, matching 0.x.

### Added

- **`-1` / `--once` / `-once`** — force a single snapshot even in a terminal.
- Combining `--live` and `--once` is rejected with exit code 2 instead of one silently
  winning.
- **`tools/badges.py`** — generates the shields.io endpoint JSON for the version, lines of
  code and unit-test badges; CI regenerates and commits it on every push to `main`.
- **CHANGELOG.md** (this file).
- Test suites `tests/test_timing.py` and `tests/test_badges.py`.
- PayPal donation link in the README.

## Pre-reset history

These releases were tagged only in the commit log; the numbering restarted at 0.1.0.

### 1.1.4

- Fixed `UnicodeEncodeError` on Windows when stdout was redirected: the legacy cp1252
  codec cannot encode the block characters the bars are drawn with. Found by CI on its
  first run.

### 1.1.3

- Added the pytest suite and the GitHub Actions matrix (Linux/macOS/Windows, Python 3.9).
- README badges.

### 1.1.2

- Fixed the argument parser silently ignoring unknown options, which made `termstats -live`
  run a snapshot that looked like a broken live mode. Long options are now accepted with a
  single dash, and unrecognised input exits 2.
- Rejected non-finite intervals (`nan`, `inf`).

### 1.1.1

- Pinned `plotext<6`. plotext 6.0.0 removed the entire 5.x top-level API and
  `termstats --live` died with `AttributeError: … has no attribute 'clear_figure'`.
- Charts now degrade to a note instead of taking the dashboard down with them.

### 1.1.0 and earlier

- Renamed from `stats` to `termstats` (command, package, distribution and repository).
- Original dashboard: CPU, memory, disk, network, top processes, history charts.

[Unreleased]: https://github.com/pepperonas/termstats/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/pepperonas/termstats/releases/tag/v0.2.0
[0.1.0]: https://github.com/pepperonas/termstats/releases/tag/v0.1.0
