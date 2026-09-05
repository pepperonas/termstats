# Changelog

All notable changes to termstats are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version is below 1.0.0, a **minor** bump (`0.x.0`) may contain breaking changes
and a **patch** bump (`0.1.x`) never does. The first release that promises a stable command
line will be 1.0.0.

## [Unreleased]

## [0.4.1] - 2026-09-05

### Fixed

- **Windows Terminal ran in 16 colours.** It exports neither `TERM` nor `COLORTERM`, only
  `WT_SESSION` ([microsoft/terminal#11057](https://github.com/microsoft/terminal/issues/11057)
  is still open), and the detection chain read the missing `TERM` as a basic terminal — every
  theme was quantised to 16 bands on a terminal that draws 24-bit colour. `WT_SESSION` is now
  a truecolor signal, ranked with `COLORTERM`, and a completely silent environment on Windows
  (a bare console window) falls back to the Windows build: 24-bit colour from Windows 10 1703
  (build 15063), 16 colours before. `NO_COLOR` and `TERM=dumb` still win over both.
- The emulated Windows load average is asked for during priming, so its five-second
  sampler starts before the first frame rather than with it.

### Added

- A PowerShell smoke test in CI on the Windows runner: `termstats --once > file` under
  `pwsh` must come back as UTF-8 with the bar glyph intact (proves the cp1252 widening and
  PowerShell 7.4's byte-preserving redirection together), and a redirected bare `termstats`
  must still print and exit.

### Documentation

- README: `WT_SESSION` in the environment table and the detection order; the platform table
  says what the load average is on Windows (psutil's emulation, `0.00` for the first five
  seconds — the old "requires Python 3.12+" footnote was wrong), that resize lands with the
  next refresh there, and that the connection count needs root on macOS rather than admin
  on Windows. Troubleshooting entries for flat colours in Windows Terminal, garbled
  redirected files in PowerShell 5.1 / 7.0–7.3, boxes in the classic console window, and
  the PowerShell spelling of the `ts` alias.

## [0.4.0] - 2026-09-05

Visual polish, end to end: one place for every colour and glyph, six themes, a layout that
holds still, and a live session that behaves. Additive throughout — every existing flag
and default is unchanged.

### Added

- **Themes.** `default`, `mono`, `nord`, `gruvbox`, `catppuccin-mocha`, `viridis`; chosen
  with `--theme NAME` or `TERMSTATS_THEME`, listed with `--list-themes`. Each is a four-stop
  ramp interpolated in **OKLab** and mapped back into gamut by reducing chroma, monotone in
  lightness (a higher load is always a brighter cell), and checked to survive quantisation to
  256 and 16 colours without folding two loads into one band. On 16-colour terminals the
  ramp is drawn from four named colours the theme picks itself.
- **Peak markers.** Every meter carries a hairline at the high-water mark of the last 30
  samples; it moves down as the spike leaves the window. In live mode the bar fill eases
  towards its target while the number and the hairline stay raw — motion only where it
  carries data.
- **`--compact`** (no padding inside panels, two more bar cells per row) and
  **`--no-border`** (title rules instead of frames, the body at full width with a gutter
  between the columns). Frame chrome is decided in one function, so the height budget, the
  body widths and the drawn panels cannot disagree.
- **A footer:** `Ctrl+C to exit` in live mode, `© <year> Martin Pfeffer | celox.io`
  right-aligned with the year read from the clock. ASCII mode writes `(c)`.
- **`--demo`:** a deterministic stand-in for psutil with a scripted story — a network
  burst, a CPU spike that takes every core and the disk reads with it, a root disk that
  only fills, processes that react — on a clock of its own, so sixty frames of history are
  played before the first visible one and rates come out as designed. Says **DEMO** in the
  header on every frame. The README screenshots come from it; `tools/demo.tape` records it
  with VHS.
- **Environment:** `TERMSTATS_THEME`, `TERMSTATS_GLYPHS=braille|block|ascii`,
  `TERMSTATS_NERD_FONT`, and `NO_COLOR` / `TERM=dumb` honoured completely (no colour *and*
  no bold/dim).
- **Resize relayouts at once.** A `SIGWINCH` handler (guarded — Windows has none; the
  previous handler is restored) cuts the tick wait short, the dashboard is laid out for the
  new size within a tenth of a second and the cadence restarts from that frame.

### Changed

- **Every colour and glyph lives in `termstats/theme.py`.** `cli.py` names tokens and never
  a hex value or a drawing character; a test reads its source and refuses either.
- **The layout holds still.** Every number has a fixed-width format (`  8.3%`, ` 45.2K/s`,
  `  3d 04h`), the header's fields never move when a value grows, its tail degrades by width
  alone (sparkline + clock, clock, none), the network unit flips with hysteresis, and process
  rows are sorted with the PID as tie-breaker so they do not swap places between frames. A
  20-frame stormy sweep across seven geometries pins that no element changes width or place.
- **One frame for every panel:** the border in the theme's quiet border tone instead of five
  competing panel colours, titles bold in the theme's accent, values bright in the ramp tone
  with their units (`%`, `K/s`) dimmed.
- **Charts:** a vertical gradient fill (brightest at the top edge, fading into the ground),
  no plot frame, ticks and axis in the muted tone, fixed-width axis labels, and a designed
  empty state — a dotted baseline with `collecting · 2 samples needed` — instead of a bare
  sentence. The ASCII fallback fades the same way.
- **The header sparkline sits in the middle** of the free space between the identity fields
  and the clock; its position is a function of the width alone.
- **The cursor is hidden before the priming pause**, not with the alternate screen, and the
  whole live session sits in one `try/except KeyboardInterrupt/finally`: Ctrl+C anywhere
  exits 0 without a traceback, the cursor and the signal handler come back whatever happened.
  An interrupted snapshot exits 130, quietly.
- The dimmed cache segment of the memory bar is dimmed in OKLab lightness (same hue, less
  light) and pinned at a minimum contrast to the used segment; the empty part of a meter is
  drawn in the theme's track tone, between the background and the dim text.

### Fixed

- In the narrow single-column stack the CPU panel was laid out for the height of the
  **whole** stack and then cropped by its layout slot: at 60×20 `cpu8`, `cpu9` and `TOTAL`
  were missing, at 80×24 `TOTAL`. Present since the layout rewrite; every geometry sweep
  now pins `TOTAL`.
- A packed two-column CPU panel used one row fewer than its cap and left it blank under
  `TOTAL`; the panel height is now exact and the row goes to the process list.
- Ctrl+C during the first half second of live mode (the priming pause) printed a traceback.
- The process panel's minimum height counted two frame rows even without a frame.

### Performance

- Render + print at 140×50: 40.9–44.1 ms per frame against 40.0–42.5 ms for 0.3.0, measured
  in three alternating rounds — noise, no measurable cost. Ramp lookups are cached.

### Tests

- 582 → 964 tests. New suites for the design tokens and themes, layout stability, peak
  markers, the frame modes, footer and header, the live lifecycle, `--demo`, and the
  Definition of Done (no hard-coded colours or glyphs, WCAG contrast per theme, `NO_COLOR`
  and pipes, every fallback level in-process and — on Linux/macOS — in a fresh process).
- The suite pins its own terminal capabilities instead of detecting them: the first 0.4.0
  CI run was red in every cell because the fixture took the colour level of the runner's
  environment. It is now green under `TERM=dumb`, `NO_COLOR=1` and without `COLORTERM`.
- The `--no-border` geometry sweep pins what is invariant (no blank line inside the picture,
  `TOTAL` present) rather than a row count that only held on a ten-core notebook.

## [0.3.0] - 2026-09-05

Second visual pass. Everything here is additive; the command line is unchanged.

### Changed

- **Charts are area charts.** The CPU history is drawn as a filled braille area, tinted by
  the ramp at the *mean* load of the window — a chart that has gone amber says the same
  thing an amber meter does. The network chart fills the rx series and draws tx as a line
  over it: two overlapping fills turn to mud where they cross.
- **Chart series colours come from the ramp** (RGB tuples handed to plotext) instead of
  plotext's named `cyan`/`green`/`blue`.
- **Chart titles carry the live values.** `cpu · last 30s · 42%`, and for network a
  legend that doubles as a readout: `▇ rx 3.1MB/s  ━ tx 1.2MB/s · MB/s` — the filled
  glyph for the area, the bar for the line, so the legend shows what you see rather than
  naming colours.
- **The network axis is round and scaled.** Ticks at `0 / 300 / 600` instead of plotext's
  `466.6 / 373.3`, and the unit flips from KB/s to MB/s when the window's peak passes 2 MB/s.
- **The memory bar shows the kernel's cache as its own dimmed segment.** psutil's `percent`
  counts cache as used; the bar now splits that into what processes hold (ramp) and what is
  cached (`▒`, same hue at 45% brightness), with `+5.7G cache` in the note when the panel is
  wide enough. The number stays psutil's — that is the "how full" everyone means.
- **A CPU sparkline in the header** — sixteen block cells, tmux-status-bar style, each the
  *peak* of its slice (a mean would flatten the spike you wanted to see), tinted by the
  ramp. Omitted in ASCII mode; there is no ASCII glyph set with eight heights.
- RSS is printed as `1.2G` above a gigabyte instead of `1234M`.

### Fixed

- The subtitle separator leaked a `·` into ASCII mode.

### Added

- Tests 511 → 582, sixteen of them mutation-checked (two of my own pins were green-blind
  on the first try and were tightened — see CLAUDE.md).


## [0.2.1] - 2026-09-05

### Fixed

- **A panel could be drawn as a bordered stump on Linux.** The height budget is a plan, not
  a guarantee: a narrow terminal, several mountpoints, or Linux's extra steal meter can push
  the fixed section sizes past the height available, and the trailing `ratio` section was
  then squeezed to two lines — a frame with nothing in it. Sections are now dropped from the
  bottom until the plan actually fits, and the single-column layout takes only the stack
  cards that fit, whole. Found by CI on Linux at 60×20; no macOS run could reproduce it.
- **The single-column layout left blank lines.** A rich `Group` renders its children at
  their natural height and leaves the remainder empty, so the trailing `ratio` never
  stretched. The narrow branch nests Layouts now, like the wide one.

### Added

- A geometry sweep across both platforms and 2–128 cores (100 cases), so a Linux-only
  layout defect fails on a Mac. Tests 411 → 511.


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

[Unreleased]: https://github.com/pepperonas/termstats/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/pepperonas/termstats/releases/tag/v0.3.0
[0.2.1]: https://github.com/pepperonas/termstats/releases/tag/v0.2.1
[0.2.0]: https://github.com/pepperonas/termstats/releases/tag/v0.2.0
[0.1.0]: https://github.com/pepperonas/termstats/releases/tag/v0.1.0
