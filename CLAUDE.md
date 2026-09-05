# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

`termstats` — a single-command terminal system dashboard (CPU, RAM, swap, disk, network, top
processes, live history charts). Pure Python, no server, no config file, no state on disk.
Repo `pepperonas/termstats` (public, MIT). **Current version 0.4.0**, 964 tests.

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
│   └── demo.py       # --demo: a deterministic psutil stand-in with a scripted story
├── tests/            # 964 pytest tests, pure unit tests, ~3 s (three real-process DoD checks)
├── tools/badges.py   # writes .github/badges/{version,loc,tests}.json (shields endpoint)
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
sets `TERM` itself via `monkeypatch.setenv`. Two more CI-only lessons: **rich honours
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
three subprocess tests check the real thing (`FORCE_COLOR=1` stands in for the tty).

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

## Screenshots

`termstats.png` (hero, 140×42) and `termstats-themes.png` (six themes, 100×30 each) are
rendered from `--demo`: rich `Console(record=True)` → `export_svg` (background swapped to the
theme's `bg`) → an HTML page served by `python3 -m http.server 8901` from the scratchpad →
Playwright MCP element screenshot (`#hero` / `#grid`, `scale: css`). ⚠️ Regenerate the SVGs
**after** the version bump — the header carries the version, and a stale one shipped once.
Remove `.playwright-mcp/` before committing.

## Gotchas (older, still true)

- **Windows redirects stdout as cp1252 — widen it before printing.** `_ensure_console_encoding()`
  runs first in `main()` and reconfigures only a stream that cannot carry `GLYPH_PROBE`.
- **Rates need two samples.** First call always yields 0 — hence `_prime_measurements()` plus
  a 1 s sleep (snapshot) / 0.5 s (live) before the first render. `HISTORY_LEN = 60` is a sample
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
