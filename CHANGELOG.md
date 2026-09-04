# Changelog

All notable changes to termstats are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version is below 1.0.0, a **minor** bump (`0.x.0`) may contain breaking changes
and a **patch** bump (`0.1.x`) never does. The first release that promises a stable command
line will be 1.0.0.

## [Unreleased]

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

[Unreleased]: https://github.com/pepperonas/termstats/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/pepperonas/termstats/releases/tag/v0.1.0
