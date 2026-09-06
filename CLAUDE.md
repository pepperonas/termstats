# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

`termstats` — a single-command terminal system dashboard (CPU, RAM, swap, disk, network, top
processes, live history charts). Pure Python, no server, no config file, no state on disk.
Repo `pepperonas/termstats` (public, MIT). **Current version 0.5.1**, 1207 tests.

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
via the PyPI JSON API on 2026-08-30 — both return `{"message": "Not Found"}`). Install docs
point at the git URL. Do not re-add PyPI badges or `pip install termstats` until a release is
actually uploaded — `pypi.org/project/<name>/` returns **200 for everything** through this
network path, so a status code is not proof; query `https://pypi.org/pypi/<name>/json`.

## The version reset (2026-09-05)

`1.1.4` became **`0.1.0`** when live mode became the default: nothing downstream could break
(never on a package index), and 1.x claimed a stability the command line had not earned.
0.x → 0.4.0 in one day: live default + badges (0.1.0), first visual pass (0.2.x), area charts
(0.3.0), the full visual-polish programme S1–S10 (0.4.0). Every release is a git tag `vX.Y.Z`.

## Layout

```
termstats/
├── termstats/
│   ├── __init__.py   # __version__ — the single source the header and --version read
│   ├── __main__.py   # python -m termstats
│   ├── cli.py        # collectors, meters, charts, layout budget, run modes, arg parsing, main()
│   ├── theme.py      # EVERY colour and glyph: GlyphSets, spacing/format tokens, OKLab, 6 themes
│   ├── demo.py       # --demo: a deterministic psutil stand-in with a scripted story
│   ├── audio.py      # -eq/-bpm/-db DSP: dBFS, log bands, peak hold, tempo, demo synth (numpy)
│   └── capture.py    # the microphone via sounddevice, lazy import, actionable errors
├── tests/            # 1207 pytest tests, pure unit tests, ~3 s (three real-process DoD checks)
├── tools/badges.py   # writes .github/badges/{version,loc,tests}.json (shields endpoint)
├── tools/screenshots.py  # renders every README picture from --demo (importable, tested)
├── docs/screenshots/     # the PNGs the README embeds (compact, no-border, narrow, snapshot, list-themes, glyphs, colours, help)
├── tools/demo.tape   # VHS script: `vhs tools/demo.tape` -> termstats-demo.gif
├── .github/workflows/tests.yml   # Linux/macOS/Windows + py3.9; badges.yml refreshes the JSON
├── examples/celox-health-report.example.py   # server health report template, NOT packaged
├── termstats.png / termstats-themes.png       # README images, rendered from --demo
└── pyproject.toml    # metadata + [project.scripts] termstats = "termstats.cli:main"
```

`cli.py` is one flat module (collectors return `(text, *values)` tuples that `render_dashboard()`
composes into rich `Panel`s / `Layout`s). **It names tokens and never a colour or a drawing
character** — `tests/test_dod.py` reads its source and refuses any `#rrggbb` or any character
from `theme.GLYPH_PROBE`. If you need a new glyph or colour, add it to `theme.py` first.

## Version bumps

The version lives in **two** places and both must move together:

- `pyproject.toml` → `version = "X.Y.Z"`
- `termstats/__init__.py` → `__version__ = "X.Y.Z"`

Then: CHANGELOG entry (top released heading must equal the version), README `# -> termstats X.Y.Z`
line, `python tools/badges.py` (the committed `version.json` must match — `test_badges` is red
otherwise), `git tag vX.Y.Z`. The three headline README badges are **generated JSON**, never
typed; the test count on the badge comes from `pytest --collect-only`.

## Local install / running

```bash
pipx install --editable .    # dev: edits take effect immediately, no reinstall
termstats                    # live (default in a terminal), Ctrl+C exits 0
termstats --once             # snapshot; automatic when stdout is not a tty
termstats --demo             # scripted machine — screenshots, trying themes
termstats --theme nord --no-border --compact
```

Installed on this Mac via `pipx install --editable /Users/martin/claude/termstats`
→ `~/.local/bin/termstats`; the shell alias **`ts`** (`~/.zshrc`) runs it. ⚠️ Because it is
**editable**, moving or deleting this checkout breaks the installed command.
`python3 -m termstats` with the *system* Python fails (`No module named 'psutil'`) — expected.

## Testing

```bash
~/.local/pipx/venvs/termstats/bin/pytest -q      # the venv the command itself uses
```

pytest is injected into the pipx venv (`pipx inject termstats pytest`), so the suite runs against
the same resolved dependency set as the command — the point, given that two past bugs were a
dependency resolve and an argument parser.

`tests/conftest.py` carries the one fixture everything depends on: **`clean_module_state` is
autouse** and resets the four history deques, the steal globals, `sample_interval`, the
glyph level, the theme, `SMOOTHING`, `LIVE`, the frame mode (`set_frame`), the demo source
(`set_demo(None)`), the resize event, the smoother, the peak tracker, the net unit hysteresis
and the disk/net rate attributes. Rate state survives between calls by design; without the
reset a collector test passes or fails depending on what ran before it.

⚠️ **A mutation batch needs a timeout, a cache purge and a `git` check.** Three ways a probe
has already gone wrong in this repo: a mutation that removes a loop's exit condition makes
pytest run FOREVER (use `subprocess.run(..., timeout=90)`, and bound the fake `Live` in the
test so a broken break fails fast); a mutation of the SAME LENGTH written in the same second
leaves Python's `.pyc` valid, so the interpreter keeps executing the mutant after the source
is restored (`PYTHONDONTWRITEBYTECODE=1`, and `find . -name __pycache__ -prune -exec rm -rf
{} +` afterwards); and a run that dies mid-batch leaves the mutant in the working tree — so
after every batch, check `git diff` rather than trusting the harness's own checksum.

⚠️ **Mutation-test every new pin.** Every stage of the S1–S10 programme ran 7–13 mutations
against its new tests (border in accent, unit in ramp tone, chrome() ignoring --no-border,
budget forgetting the footer, hasattr guard removed, seed ignored, …) and watched each go red.
Several pins were grün-blind on the first try and had to be rewritten: a "rows on screen"
count that also matched chart ticks, a gutter check that a too-narrow body also satisfied, a
compact check that a right-aligned label always passed, a relayout check that could not tell
"render now" from "render after the wait". **A test you have not watched fail is not a guarantee.**

⚠️ **The fixture PINS the capabilities** (`cli.CAPS = Capabilities("truecolor", "braille",
False)` + `set_theme("default", color="truecolor")`), it does not detect them. The first CI
run of 0.4.0 went red in all four cells because `set_theme("default")` took the colour level
`detect()` found at import — 256/16 on a runner without `COLORTERM` — and `ramp()` returned
`color(242)` / `cyan` where the pins expected hex. The same suite was green on the Mac, whose
shell exports `COLORTERM`. Reproduce locally with `env -u COLORTERM TERM=dumb pytest`; the
suite must now pass under `TERM=dumb`, `TERM=xterm`, `TERM=xterm-256color` and `NO_COLOR=1`.
Any test that reads the real environment (`detect_capabilities()`, `_color_from_env()`)
sets `TERM` itself via `monkeypatch.setenv` — and since 0.4.1 a test that pins a silent
environment to 16 colours must also pin the platform (`monkeypatch.setattr(T,
"_windows_build", lambda: None)`): on the Windows runner `detect()` consults the real build
and a silent environment IS truecolor there. Reproduce locally with a one-file pytest
plugin that fakes `sys.getwindowsversion`. Two more CI-only lessons: **rich honours
`NO_COLOR` and `legacy_windows` from the real environment even on a Console built with
`color_system="truecolor"`** — a test Console that wants colour passes `force_terminal=True,
no_color=False, legacy_windows=False` (on a Windows pipe rich otherwise renders through the
win32 API and swaps `╭` for `┌`); and how much slack the `--no-border` sweep may leave at
the bottom is a property of the MACHINE (cores, mounts, swap), not of the frame mode — a
4-core Linux runner drops the disk section at 60×20 and leaves five rows, so the sweep pins
"no blank line inside the picture", not a row count.

Traps hit while writing these (all real): `f"p{i}" in output` matched `p2` inside `p29` — count
rows; an anchor string whose indentation did not match "proved" nothing; `KeyboardInterrupt`
escaping a test aborts pytest with rc 2 and no `FAILED` line — convert it to `pytest.fail`;
a bare `io.StringIO()` has no `encoding` and reads as "cannot draw" in `theme.detect()`; and
**rich caches a Style's rendered escape string on the instance** (`Style._ansi`) while
`Style.parse` hands out the same instance for the same text — one process never switches
colour systems, but the suite does, so `test_dod.fresh_styles()` clears the lru caches, and
three subprocess tests check the real thing (`FORCE_COLOR=1` stands in for the tty; skipped
on Windows, where rich renders a pipe in legacy mode through the win32 API and no ANSI
reaches the parent).

CI (`.github/workflows/tests.yml`) runs Linux/macOS/Windows plus Python 3.9 and prints the
resolved psutil/plotext/rich versions — a fresh resolve is how the plotext 6.0.0 break arrived.

Manual checks the suite deliberately leaves out:

```bash
termstats --version && termstats --help && termstats --list-themes
COLUMNS=100 LINES=30 termstats --demo --once           # every panel, full charts, no tty needed
```

Driving live mode under a pty (resize + Ctrl+C) is scripted in the session scratchpad pattern:
`pty.fork()`, `TIOCSWINSZ`, **keep draining the master fd while waiting** — a full pty buffer
blocks the child in `write()` during rich's screen teardown and looks like Ctrl+C is ignored.
Never measure widths from a pty capture (`screen=True` repositions the cursor; rows concatenate).
Two shell traps: zsh does not word-split unquoted parameters, and `wait %1` returns 127
non-interactively — drive multi-step process tests from Python.

## Design tokens and themes (S1–S2)

- `theme.py` owns: `GLYPH_SETS` (braille/block/ascii, each a `GlyphSet` NamedTuple incl.
  `rule` and `copyright`), `GLYPH_PROBE` (every non-ASCII glyph the dashboard can draw — **add
  any new glyph here**, `test_dashboard` collects drawn characters and requires them in it),
  `Capabilities` + `detect()` (`NO_COLOR` > `TERM=dumb` > `COLORTERM` > `TERM`; glyphs via
  `TERMSTATS_GLYPHS` / `TERM=dumb` / stream encoding probe; `TERMSTATS_NERD_FONT`), spacing
  constants (`LABEL_W`, `VALUE_W`, `RATE_W`, `SPARK_W`, `PEAK_WINDOW`, `SMOOTH_ALPHA`,
  `CHART_*`, `PROC_MIN_H`, `PANEL_*`/`COMPACT_*`/`RULE_*` chrome), fixed-width formatters
  (`fmt_pct` 6 cells, `fmt_gb` 6, `fmt_rate` 8, `fmt_uptime` 8, …), OKLab (`rgb_to_oklab`,
  `oklab_to_rgb_in_gamut` by chroma bisection, `Ramp`, `dim_hex`, `BandedRamp`, `bands()`),
  `contrast_ratio()` (WCAG), and `THEMES`.
- A `Theme` is: `stops` (4–5 OKLab-interpolated stops), text/soft/dim/muted/faint, `track`
  (the empty part of a meter — between bg and dim), `border` (ONE frame tone), `accent`
  (panel titles), wordmark colours, `bg` (what the contrast tests measure against), `bands16`.
- Every theme must stay **monotone in lightness** (a test checks; viridis is exempt as a fixed
  scientific map) and survive **256/16 quantisation** without folding bands. Contrast floors in
  `test_dod.py` come from the measured table of all six themes: text/soft/accent ≥ 4.5, dim ≥ 3,
  muted ≥ 2.2, faint ≥ 1.5, border ≥ 1.5, track ≥ 1.2, **ramp t < 0.4 ≥ 2.0, t ≥ 0.4 ≥ 3.0** —
  the ramp colours the value digits, so its idle end must read as text (nord's `#4c566a` and
  viridis' `#440154` failed that at 1.69 / 1.19 and were raised to `#616e88` / `#414487`).
- Red is darker than yellow at usable sRGB chroma; the warm stops were lowered so "hot" still
  rises in lightness. Ramp lookups are cached on millionths (thousandths shifted channels ±1).

## Layout, stability, chrome (S3, S6, S7)

- `render_dashboard(width, height)` budgets **`body_h = th - 2`** (header + footer are fixed
  rows), decides `chrome()` → `(rows, cols)` per panel in ONE place (`PANEL_CHROME`,
  `--compact` = border only, `--no-border` = a title `Rule` + one gutter column per side —
  without the gutter two columns of meters run into each other), computes the CPU panel
  height **exactly** from `cpu_section_rows()` (a packed 2-column panel used one row fewer
  than the cap), `proc_min = ch + 3`, drops sections from the bottom, gives the last section
  `ratio=1`, then appends the footer.
- ⚠️ In the narrow stack `core_rows` derives from `cpu_wanted`, in the wide row from `top_h`.
  Deriving from `top_h` in both cases (the 0.1.0–0.3.0 code) laid the cores out for the whole
  stack's height and the Layout **cropped** the panel — at 60×20 `cpu8`, `cpu9`, `TOTAL` were
  missing. Every geometry sweep now pins `TOTAL`.
- In `--no-border` mode the budget's slack is visible: it may sit at the bottom of the body
  above the footer (≤ 3 rows), never inside the picture; chart plot rows are exempt from the
  "no blank line" invariant (a curve that does not reach the top leaves the row empty).
- Nothing may change width on a value change: fixed-width formats, header fields fixed, tail
  tiers by width alone (sparkline centred + clock / clock / none), net unit hysteresis
  (KB→MB→GB), stable process sort `(-cpu, pid)`. `test_stability` runs a 20-frame stormy
  sweep with 2 warm-up frames (frame 1 is "collecting").
- Value digits bold in the ramp tone, the **unit in its own dim span** (`meter(unit_w=…)`, the
  chart subtitle the same) — tests that match value text must match the digits, not `"32.0%"`.
- Footer: `Ctrl+C to exit` (LIVE only) + `© <year> Martin Pfeffer | celox.io`, year via
  `_current_year()` (patchable). The old `(bottled 🍻 …)` header branding is gone since 0.1.0;
  the brand test pins `TERMSTATS` + version in the header.

## Meters, peaks, charts (S4–S5)

- `bar()` = gradient cells + eighth partials + dimmed secondary (cache) + **peak hairline**
  (`GLYPHS.peak`) at `PeakTracker`'s 30-sample high-water mark, drawn only if `peak_cell >
  filled`. Live mode eases the *fill* (`Smoother`, EMA, `SMOOTHING` True only in `run_live`);
  numbers and the hairline are always raw. Snapshot mode never smooths.
- Charts: plotext 5.x braille/hd marker, `theme("clear")` then `ticks_color()` (order matters),
  `frame(False)`, fixed-width `fmt_axis` ticks, `axis_w` passed per chart (`AXIS_W_PCT` /
  `AXIS_W_RATE` — deriving it from the value range made the rate chart jump), vertical
  gradient via `Style(color=…)` spans, `_collecting()` skeleton (`⠒⠒⠒…` + `collecting · 2
  samples needed`, height-aware). The ASCII chart fades by row too.
- **plotext is pinned `<6` — do not relax it.** 6.0.0 (2026-08-23, beta upstream) removed the
  5.x API (`clear_figure`, `plot`, `ylim`, `plotsize`, `build`); `_render_chart()` guards on
  `_PLOTEXT_5` and fails soft (a library break costs a chart, not the dashboard).

## Lifecycle (S8)

- `run_live`: cursor hidden **before** the priming pause, whole session in
  `try/except KeyboardInterrupt/finally` (Ctrl+C in the first 0.5 s used to print a traceback),
  `finally` restores cursor + the previous `SIGWINCH` handler. rich's `Live(screen=True)`
  restores the alternate screen itself. Ctrl+C → exit 0; interrupted `--once` → exit 130.
- Resize: `_on_resize` sets `_resized` (Event); `_sleep_until(deadline)` sleeps in
  `RESIZE_SLICE_S = 0.1` slices and returns True when set; the loop then relayouts at once and
  **resyncs the cadence from that frame** (`next_tick = monotonic()`), rather than rendering an
  extra frame on the old grid. `hasattr(signal, "SIGWINCH")` guard for Windows.
- The timing harness (`test_timing.live_harness`) charges `RENDER_COST` **per render** and
  interrupts at the sixth render so every recorded (sliced) wait is whole; `per_frame()` adds
  the slices back up.

## --demo (S9)

`demo.DemoSource(seed, interval)` carries the psutil surface `cli.py` uses (`test_demo`
extracts every `psutil.<name>` from the source and requires it), advances one frame per
`cpu_percent(percpu=True)`, and has **its own clock** (`now()`); `cli._now()` returns it in
demo mode so the rate collectors and uptime are wall-clock independent — `_prefill_history()`
plays 60 frames in a tight loop before the first visible one. Story period 150: burst 22–42,
spike 46–74, disk fills, processes react; the first visible frame (63) lands on the spike.
`set_demo(source)` rebinds the module-level `psutil`. A **DEMO** badge sits in the header's
fixed fields. Snapshots set the demo interval to 1 s (the chart title says `last 60s`).

## Microphone modes (0.5.0)

`-eq` / `--equalizer`, `-bpm`, `-db`, plus `-d/--device NAME` and `--list-devices`. Three files:

- **`termstats/audio.py`** — pure DSP, numpy only, no I/O, no rich. `dbfs()` (RMS, clamped to
  `DB_FLOOR = -80`; a quiet room on the MacBook mic measured median −67 / p90 −63 — at the
  earlier −60 floor the level meter sat pinned in a quiet room), `Spectrum` (28 log bands
  40 Hz–16 kHz from a ROLLING 4096-sample Hann window hopped by one 1024 block — a 1024-point
  FFT has 43 Hz bins and bands below 200 Hz would share one; peak bin per band in dB; a 60 dB
  window under a slow GLOBAL ceiling so quiet and loud both fill the panel while bands keep
  their proportions; attack .55 / release .18; peak hold 6 blocks then −0.012/block),
  `BpmAnalyzer` (the inspector-rust/disco estimator: kick band 30–110 Hz, onset = energy >
  1.4× 3 s average AND > peak of a lagged window ×1.04 [SuperFlux], 0.30 s refractory, IOI
  median folded into 60–200, octave snap, 4 s display mean; **stale = no ONSET for 4 s once
  the window thinned** — counting from the last estimate kept a stopped track's tempo on
  screen 8 s+), `Analyzer` (one `feed(block, now)` per block; `music` = smoothed dB > −45
  gates onsets; db/bpm histories as deques; ONE FFT serves bands and kick band), `DemoAudio`
  (deterministic synth: kick 55 Hz decaying sine on the beat, off-beat hi-hat noise, two-bar
  bass, pad, room noise, tanh soft clip; own clock `now()`).
- **`termstats/capture.py`** — `MicSource(device)` (mono float32, `audio.BLOCK`, the DEVICE's
  own sample rate, callback errors swallowed — the PortAudio thread must never die),
  `resolve_device` (case-insensitive substring, else the system default input),
  `list_devices`, `AudioUnavailable` with three actionable messages (no numpy/sounddevice →
  the install line; PortAudio missing → `apt install libportaudio2`; no match → the inputs).
  sounddevice is imported lazily; the Linux wheel does not bundle PortAudio.
- **`cli.py`** — `render_audio(mode, an, now, w, h)` (header with badge via
  `header_line(width, badge=)`, ONE `_panel`, footer, in a `Layout`), `eq_body` (columns =
  `eq_columns(width, bands)`: 2 cells + 1 gap, bands FOLDED into fewer columns when narrow so
  the range stays 40 Hz–16 kHz; top cell from `GLYPHS.spark` eighths, `GLYPHS.vpeak` marker
  only when the peak cell is above the bar; frequency axis via band edges), `db_body`
  (`meter()` with `peak_of("audio.db")`, `_audio_chart` from `db_history` when ≥ 6 rows are
  left), `bpm_body` (`---` before a tempo, `GLYPHS.beat_on` for `BEAT_LIT_S` = 120 ms after an
  onset), `audio_hud` shared by all three, `run_audio(mode, interval, source, once)` — push
  (mic thread + lock) or pull (`DemoAudio.read` pumped `interval` seconds per frame after an
  8 s prefill), `AUDIO_INTERVAL` = 0.05, `--once`/pipe = listen 1.5 s and print one frame.

Tests pin the DSP with phase-CONTINUOUS tones (`Tone` in `test_audio.py`): restarting the
phase every block puts a click at each boundary — broadband energy that lit every band and
hid the tone, which looked like a resolution bug and was the test's fault. The audio suites
`importorskip("numpy")`; CI installs `.[dev]`, which carries numpy and sounddevice.
Live-checked: pty run of `--demo -eq` (98 frames/3 s, alt screen once, exit 0 on Ctrl+C), real
mic `-db --once` and `-bpm --once --device MacBook`, the wrong-device message.

## Leaving a session (0.5.0)

`Esc` and `q` end a live session; `Ctrl+C` still does. `KeyWatcher` puts the terminal into
**cbreak** for the session (`_set_cbreak`/`_restore_tty`, restored in the same `finally` as
the cursor) and `_read_ready` polls without blocking - `select` on POSIX, `msvcrt.kbhit` on
Windows. `_sleep_until` checks it once per 0.1 s slice, sets `_quit` and returns True; both
loops then `break` instead of drawing one more frame. ⚠️ **Esc is the first byte of every
arrow and function key**, so `is_quit_key` treats a lone `\x1b` as quit and `\x1b[`/`\x1bO`
as a sequence - the whole burst arrives in one read, which is the only reason they can be
told apart. Inactive when stdin is not a terminal (`--live < /dev/null`, a pipe), and
`stop()` is idempotent.

## The headline number (0.5.0)

`-db` and `-bpm` draw their value five rows tall from `theme.BIG_FONT` (3x5 per glyph,
`#`/`.`, scaled `BIG_DIGIT_SCALE = 2` wide because a terminal cell is half as wide as it is
tall - unscaled it reads as a scratch). `cli.big_digits` paints it in `GLYPHS.bar_full`, so
cli.py still names no glyph and ASCII degrades with everything else. Three behaviours:
`shown_level`/`shown_tempo` ease through the existing `Smoother` (a lost tempo calls
`Smoother.forget`, so the next one appears rather than counting up from the stale value),
`readout()` holds the drawn value for `AUDIO_READOUT_S` = 0.2 s (20 fps is right for a bar
and a blur for a digit), and `tempo_tone` flares on every beat. **Nothing is eased or held
when `SMOOTHING` is off**, so a snapshot still prints exactly what was measured, and the HUD
carries the raw sample in every mode.

⚠️ **Two seams here are invisible to a text search**: the headline is glyphs, not a number,
and the chart is plotted. Both mutation probes came back green until they got pins of their
own (`test_the_headline_digits_are_the_shown_value`, `cli.level_history` + its pin).

## The level scale (0.5.0)

The screens show **dBFS + `audio.SPL_OFFSET` (100)**: a quiet room ~40, loud music ~80, the
range a phone app shows. Measured on this machine to choose it: a quiet room is around
-67 dBFS, music at a normal level around -20. It is an **estimate, not a calibration** - the
README says so, and the panel subtitle says "uncalibrated estimate". ⚠️ **Only the display is
shifted.** The analysis, `db_history`, the extremes and the -45 dBFS music gate stay in dBFS,
because a shifted value is not dBFS any more (the same rule disco-controller follows). The
conversion sits in `_shown_db` and `level_history`; the x-axis labels stay negative on
purpose - they are seconds ago, not levels, and the pin that forbids negative numbers
excludes them explicitly.

## Docs sync (0.4.2)

`tests/test_docs_sync.py` ties README, `--help` and CHANGELOG to the code: every `--long`
flag in the `_*_FLAGS` tuples has an Options-table row and a help line (and every table row
names a real flag); every `TERMSTATS_*` name in the source and every key `_color_from_env`
reads is in the README; every theme has a table row, a Features mention and a place on the
`--theme` help line; every README `<img>` exists on disk and every PNG under `docs/screenshots/`
plus the root is shown (orphans are red); the `## Contents` TOC lists every h2 and every link
resolves (GitHub slug rule: lowercase, drop punctuation, spaces to hyphens — "Naming &
history" is `naming--history`); the changelog's released headings carry ISO dates, are unique
and descending, `[Unreleased]` comes first and `###` sections use Keep a Changelog names (the
folded 1.1.x history's version sub-headings are allowed). Adding a flag, env var, theme,
heading or picture without its documentation is a red test, not a reader's discovery.

## Windows (0.4.1)

Windows was "supported" by CI and never looked at in PowerShell. Two things came out of it:

- **Windows Terminal exports neither `TERM` nor `COLORTERM`, only `WT_SESSION`**
  (microsoft/terminal#11057, still open). `_color_from_env` read the empty `TERM` as a
  16-colour terminal and `configure_console()` forced rich onto `standard` — every theme ran
  in 16 bands on a terminal that draws 24-bit colour, and rich alone would have detected it.
  `WT_SESSION` now ranks with `COLORTERM`; a completely silent environment on Windows (bare
  conhost) is decided by `_windows_build()` (`sys.getwindowsversion().build`, ≥ 15063 =
  Windows 10 1703 = truecolor). `NO_COLOR` and `TERM=dumb` still win. `detect()` passes the
  build in; `_color_from_env(env, windows_build=None)` stays a pure function for the tests.
- The Windows CI step runs under **Git Bash** (`defaults: run: shell: bash`). A second step
  under `pwsh` now redirects `termstats --once` to a file and requires the bar glyph back in
  UTF-8 — that proves `_ensure_console_encoding()` AND PowerShell 7.4's byte-preserving
  redirection in one go, and a redirected bare `termstats` must still print and exit.

Things that stay Windows-shaped and are documented, not worked around: PowerShell 5.1 and
7.0–7.3 decode native output through `[Console]::OutputEncoding` (OEM code page) and write
mojibake for braille/blocks (user fix: set it to UTF-8, or `TERMSTATS_GLYPHS=ascii`); the
classic conhost fonts have no braille (`TERMSTATS_GLYPHS=block`); no `SIGWINCH`, so a resize
lands with the next tick; `psutil.getloadavg()` is emulated there and reads `0.00` for its
first five seconds — `_prime_measurements()` asks for it early so that clock starts before
the first frame (the README used to blame "Python 3.12+", which was wrong); `net_connections()`
does not need admin on Windows — it needs **root on macOS**, where the row is omitted.
Pins: `tests/test_windows.py` (7 mutations caught on the first run).

## Screenshots

Every README picture is rendered from `--demo`, never captured. **`tools/screenshots.py OUT_DIR
[--only view,view]`** (run with the pipx venv python) is importable — `render(out_dir, names)`,
`write_index(out_dir)`, `main(argv)` — and `tests/test_screenshots_tool.py` drives it. Views:
`hero` (140×42), `theme-<name>` (100×16 tiles — 16 rows fill the tile without empty panels
and the 2×3 grid keeps roughly the hero's 5:3 aspect), `compact` (80×24), `no-border` and
`snapshot` (120×36; the snapshot is the un-prefilled first frame with the collecting skeleton,
sampled at 1 s like `run_once`), `narrow` (100×26 — the charts are dropped whole),
`list-themes`, `glyph-{braille,block,ascii}` (100×34 — the glyph levels differ in the CHARTS,
which the budget only fits from 32 rows on), `color-{truecolor,256,16,mono}` (100×16),
`help` (at its natural width), the microphone screens `eq` / `bpm` / `db` (120×36, fed eight
seconds of `DemoAudio` so a tempo is locked), and the four 0.5.1 additions: `db-small`
(80×12, the one-line fallback), `eq-ascii` (the `ascii` glyph level), `bpm-quiet`
(`seconds=0` — silence, so the screen shows `---` / `quiet` / `waiting for music`) and
`devices`. ⚠️ **`devices` feeds `cli.print_devices` an INVENTED sound card** (`DEMO_DEVICES`):
rendering the real one would publish whatever audio software this machine has and change with
every install; a test asserts the private names are absent. `write_index` lays them out under
`#hero #grid #compact #no-border #narrow #snapshot #list-themes #glyphs #colours #eq #bpm #db
#db-small #eq-ascii #bpm-quiet #devices #help`.

Rasterise that page in a real browser: `python3 -m http.server 8901` in OUT_DIR, then Playwright
element screenshots (`locator('#hero').screenshot({scale:'css'})`); `file://` is blocked in the
MCP browser, and ImageMagick's SVG renderer is not to be trusted with these. PNG destinations:
`termstats.png` (hero), `termstats-themes.png` (grid), `docs/screenshots/{compact,no-border,
narrow,snapshot,list-themes,glyphs,colours,help}.png`. `test_docs_sync` requires every README
image to exist AND every PNG on disk to be shown — an orphan or a dangling link is red.

Three things the tool had to learn (all found by its tests):
- **Each frame comes from a PRIVATE copy of `termstats.cli`** (`spec_from_file_location`), not
  `importlib.reload` — reloading re-executes the module the whole test suite holds.
- **The header clock is the demo clock** (`time.localtime(_now())` in `header_line`, 0.4.1+),
  and the tool pins `source.T0 = SHOT_T0`. Before, the header read wall time and two renders
  straddling a second boundary differed — the "reproducible" claim was false by one field.
- **The `mono` tile is recorded from plain text.** rich strips colour at the terminal, but
  `export_svg` reads the *recorded* styles, colours and all — a `color_system=None` console
  exported a fully coloured picture. Render to a plain console first, record THAT.
- The tool's demo interval and `cli.sample_interval` must agree (0.5 live, 1.0 snapshot) or the
  header says `0.5s` under a chart titled `last 60s`.

⚠️ Testing SVG text: rich writes spaces as `&#160;` and escapes `<>&`, so `"last 30s" in svg`
is ALWAYS false — a pin built on it passed vacuously. `svg_text()` in the tool tests joins the
`<text>` nodes, unescapes, and maps NBSP to space. ⚠️ Run the tool AFTER the version bump — the
header carries the version, and a stale one shipped once. The celox.io project page consumes
the four original PNGs (`website/scripts/projekt-bilder.sh termstats`). Remove
`.playwright-mcp/` before committing.

## Gotchas (older, still true)

- **Windows redirects stdout as cp1252 — widen it before printing.** `_ensure_console_encoding()`
  runs first in `main()` and reconfigures only a stream that cannot carry `GLYPH_PROBE`.
- **Rates need two samples — and the collectors keep the previous one in FUNCTION ATTRIBUTES**
  (`get_network_section._last/_last_time`, `get_disk_section._last_io/_last_time`).
  `_prime_measurements()` seeds exactly those (0.4.2) and then the snapshot sleeps 1 s / live
  0.5 s before the first render. Until 0.4.1 priming only *read* the counters, so the single
  render of a snapshot was the collectors' first call: every `--once` printed `0.0B/s` and
  `n/a` — found only because the screenshot tool rendered a first frame and the caption
  claimed otherwise. Seeding must NOT touch the history deques (a 2-point chart is not a chart). `HISTORY_LEN = 60` is a sample
  count; the chart title computes the window from the interval.
- **Steal time is Linux-only** (`/proc/stat` field 8 delta); elsewhere the bar is not drawn.
- **Never let a collector raise.** An unreadable partition is skipped; `AccessDenied` on
  `net_connections()` omits the row.
- **Argument handling is a hand-rolled `sys.argv` loop** — every token must match a `_*_FLAGS`
  tuple (long options also with one dash), unknown input exits 2 via `_fail()`. Adding a flag
  means: tuple + parse branch + `print_help()` + README options table.
- Snapshot mode always shows the collecting skeleton in both charts — correct, do not "fix".
- Below ~50 columns the 2-column CPU packing crops values (`core_columns` knows rows, not
  width); 80×24 is the readability floor and holds.

## Deploy

None. It is a local CLI: "shipping" is `git push origin main --follow-tags`; users install from
the git URL. The one server-side artefact is `examples/celox-health-report.example.py` — a
template that needs SMTP credentials, is excluded from the package, and must **never** be
committed with real credentials filled in.
