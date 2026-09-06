# Changelog

All notable changes to termstats are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version is below 1.0.0, a **minor** bump (`0.x.0`) may contain breaking changes
and a **patch** bump (`0.1.x`) never does. The first release that promises a stable command
line will be 1.0.0.

## [Unreleased]

## [0.6.0] - 2026-09-06

### Added

- **A motion layer for the microphone screens** (`termstats/motion.py`). Every quantity the
  screens draw is now a function of elapsed time: bars ease towards their target (20 ms
  attack, 120 ms release), a falling bar leaves a dim afterglow that falls under gravity,
  peak markers hold 400 ms then fall under gravity, the level meter has VU ballistics, and a
  beat is an impulse decaying to 1/e in 250 ms that every reaction reads — the dot lights hot
  and cools through two tones, the tempo digits flare and fade, every bar is nudged up by up
  to 12 %. The tempo screen gained a **metronome**: a head sweeping between two beats at the
  detected tempo. Frame-rate independent by construction; a long pause is clamped to one
  short step. None of it runs in a snapshot.

### Changed

- The microphone screens refresh **30 times a second** (was 20), and the loop paints every
  frame itself — rich's own refresh thread is off, which had painted each frame about twice.
- Live, a history chart is built on a **worker thread** twice a second; the frame draws the
  last finished one and never waits. A failed rebuild keeps the chart that is up.

### Fixed

- **The level screen ran at about ten frames a second**, not twenty: plotext needs ~37 ms for
  its chart and the frame paid that every time (87 ms median per frame, measured). Now 2 ms.
- The beat dot and the tempo flare used to switch on and off; both fade.

## [0.5.1] - 2026-09-06

### Changed

- **`--list-devices` is a rendered listing, not a print statement.** Names are aligned in a
  column, the channel count and sample rate sit in their own fields, and the input that would
  be used without `--device` is marked `default`. It goes through one function (`print_devices`)
  so the picture in the README shows what the command prints.

### Fixed

- Two colour pins matched a literal escape sequence, which rich caches per Style instance —
  so the form depended on TERM and on which test ran first. Green locally, red on all four CI
  cells. They compare the colour itself now.

### Documentation

- Four new rendered pictures of what 0.5.0 added: the device listing, the tempo screen in a
  silent room (dashes, `quiet`, `waiting for music`), the level meter on a short terminal
  (the one-line fallback), and the equalizer drawn in ASCII only.
- The screenshot tool grew the matching views (`devices`, `bpm-quiet`, `db-small`,
  `eq-ascii`); the device view feeds the real renderer an **invented** sound card, so no
  machine's audio software ends up in the README and the picture is the same everywhere.

## [0.5.0] - 2026-09-05

### Added

- **Microphone modes.** `termstats -eq` (also `--equalizer`) is a 28-band spectrum analyser
  from 40 Hz to 16 kHz with peak-hold markers and a frequency axis; `-bpm` a tempo detector
  (kick-band energy onsets with the SuperFlux rule, inter-onset median folded to 60–200 BPM,
  confidence, beat dot, kick-band meter, tempo history); `-db` a level meter (one level per block,
  peak hairline, smoothed value, session min/max, level history). Every mode shares one HUD
  line — beat dot, BPM, level, confidence — and the dashboard's header, theme, footer and
  fallbacks; the header carries an `EQ`/`BPM`/`DB` badge. Nothing is recorded or stored.
- **The headline number is drawn five rows tall, centred**, in the same bar glyph as every
  meter (so it degrades to `#` with the rest). It eases towards the measurement instead of
  snapping, refreshes on its own ~5 Hz clock (a digit redrawn 20 times a second is a blur
  while the bars need every frame), and the tempo flares on every detected beat. The HUD
  keeps the raw sample beside it; a snapshot eases and holds nothing.
- **The level is shown on a positive scale** — a quiet room reads about 40, loud music about
  80, the range a phone's sound-level app shows. Internally everything stays dBFS; the
  display adds a fixed offset of 100, which is an estimate rather than a calibration.
- **`Esc` (or `q`) ends a live session**, alongside `Ctrl+C` — the footer says so. The
  terminal is put into cbreak mode for the session and restored in the same `finally` that
  restores the cursor; with stdin not a terminal nothing is read. An `Esc` that introduces an
  arrow key is not a quit.
- `--device NAME` picks the microphone by any part of its name, `--list-devices` lists the
  inputs; the analyser runs at the device's own sample rate.
- The modes refresh 20 times a second by default (`-i` still wins); `--once` or a pipe listens
  for 1.5 s and prints one frame; `--demo` with an audio mode plays eight seconds of scripted
  music (kick, bass, hi-hats, pad — deterministic per seed) instead of opening a microphone.
- **Optional `audio` extra** (`pip install 'termstats[audio]'`: numpy + sounddevice). The
  system dashboard stays free of both; an audio mode without them exits 2 with the install
  line, a missing PortAudio or device is explained in one sentence.
- New modules `termstats/audio.py` (pure DSP, no I/O) and `termstats/capture.py` (the
  microphone, imported lazily); new glyphs `vpeak`, `beat_on`, `beat_off` in every glyph set.
- Three new rendered README pictures (`-eq`, `-bpm`, `-db` from `--demo`); the screenshot
  tool has `eq`, `bpm`, `db` views.

### Changed

- `--help` lists the audio options, `Esc` and the install line for the extra.

### Fixed

- **Charts were clamped to 80 columns.** plotext limits a plot to the terminal size *it*
  detects — 80 in a pipe, a test or a screenshot render — so a 116-column chart came back 80
  wide and stopped two thirds across its panel while the meter above ran to the edge. The
  renderer has already measured the slot (`limit_size(False, False)`).
- **A filled chart filled towards zero, not towards its floor.** With `ylim` (−80, 0) the
  level chart filled downward from silence at the top. Values are plotted relative to the
  floor now and labelled in the caller's units; a 0-based chart is unchanged.
- **A history chart's x axis showed the dashboard's window**, `-30s`, whatever the data
  actually spanned. `_render_chart` takes the span it is drawing.
- **The equalizer's frequency axis could lose its labels**, depending on how the installed
  numpy rounded the band edges: 40 Hz and 16 kHz are the outermost edges themselves, and an
  unclamped lookup found no band for them, fell back to the last one and drew "40" on top of
  "16k". Green on macOS, red on Linux until `_band_of` clamped both ends.

### Tests

- 1035 → +98: `tests/test_audio.py` (dBFS, bands, peak hold, tempo, demo synth),
  `tests/test_audio_render.py` (every mode at every size, badges, HUD, bars, peaks, ASCII,
  level and tempo screens), `tests/test_audio_args.py` (flags, exclusivity, device, demo,
  once, list-devices, the missing-extra message, help), `tests/test_capture.py` (a fake
  sounddevice: inputs, resolution, stream parameters, idempotent stop, thread safety, the
  three failure messages), `tests/test_keys.py` (which bytes mean quit, the watcher's states,
  both loops leaving, the footer and help naming `Esc`). The audio suites skip where numpy is
  not installed. Every new pin was mutation-probed; the two seams a text search cannot see —
  the headline is glyphs, the chart is plotted — got their own pins after the first probe
  came back green.

## [0.4.2] - 2026-09-05

### Fixed

- **A snapshot never showed a rate.** `termstats --once` (and every redirected run) printed
  `0.0B/s` for the network and `n/a` for disk I/O, in every version since 0.1.0. The priming
  step read the counters but did not hand them to the collectors, whose "previous sample" lives
  in function attributes — so the one render after the one-second pause was their first call
  and had nothing to subtract. Priming now seeds those attributes (and only those: the chart
  history stays empty, the collecting skeleton is still correct for a snapshot).
- In `--demo` mode the header clock showed the wall clock while every other number came from
  the demo's own clock; two renders of the same frame could differ by one field, which made the
  "reproducible" screenshots reproducible only within the same second. The header now reads the
  demo clock (`time.localtime(_now())`); outside demo mode nothing changes.
- `--help`: the Environment block's descriptions start in one column again (`TERMSTATS_THEME`
  was one cell off).

### Documentation

- README: a table of contents; seven new rendered pictures — `--help`, `--list-themes`, the
  first frame of a redirected run (charts still collecting), `--no-border` at 120×36, a 100×26
  terminal dropping a section whole, and two fallback galleries (glyph levels braille / block /
  ascii, colour levels truecolor / 256 / 16 / mono); a "Compared with other dashboards" section
  (htop, btop, glances — dashboard vs process manager, config file, one-shot output, charts);
  "Mutation testing" and "Screenshots" under Development; the tests table lists every suite.

### Tools

- `tools/screenshots.py` is importable: `render(out_dir, names)`, `write_index(out_dir)`,
  `main(argv)` with `--only view,view`. Fourteen named views plus one per theme. Each frame
  comes from a private copy of the CLI module (the shared one is never touched), the demo clock
  is pinned to one instant so a render is byte-identical on repeat, and the `mono` tile is
  recorded from plain text — an SVG export reads recorded styles, so rich's own colour
  stripping never reached the picture.

### Tests

- `tests/test_docs_sync.py`: every parser flag has a README row and a `--help` line and every
  README row names a real flag; every `TERMSTATS_*` variable and every key the colour chain
  reads is documented; every theme has a table row, a features mention and a place on the
  `--theme` help line; every README image exists and every screenshot on disk is shown; the
  table of contents matches the `##` headings and every link resolves; the changelog is dated,
  unique, descending, Unreleased first, and uses Keep a Changelog section names; the help
  columns are aligned.
- `tests/test_screenshots_tool.py`: importing renders nothing, one SVG per view, unknown views
  refused, the hero carries the version, no window chrome, byte-identical on repeat, the ASCII
  tile is 7-bit, the braille tile has braille and the block tile none, colour levels degrade
  in distinct fill counts, the snapshot shows the skeleton and the hero does not, `--no-border`
  has no box corners, the narrow view is missing something the hero has, the index page has a
  section per figure, the script entry point renders a selection.

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
