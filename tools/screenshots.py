"""Reproducible screenshots from `termstats --demo`.

    python tools/screenshots.py OUT_DIR [--only view,view,...]

Writes one SVG per view (no rich window chrome - the README shows them bare, the website
draws its own frame) plus OUT_DIR/index.html, which lays them out with ids (#hero, #grid,
#compact, #help, #no-border, #narrow, #snapshot, #list-themes, #glyphs, #colours).
Rasterise that page in a real browser (Playwright element screenshots), see CLAUDE.md
"Screenshots". Everything comes from the demo machine with its default seed and its own
clock, so the pictures are the same every time - and they carry the version from the
header, so run this AFTER a version bump.

Importable: `render(out_dir, names)` and `write_index(out_dir)` do the work, `main(argv)`
is the command line. Each frame is rendered by a PRIVATE copy of termstats.cli, so the
histories, smoother and peak markers start empty and the shared module (the one the test
suite holds) is never touched.
"""
import calendar
import importlib.util
import io
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.text import Text
from rich._export_format import CONSOLE_SVG_FORMAT

import termstats.cli
import termstats.theme as T
from termstats import __version__, demo

HERO = (140, 42)
TILE = (100, 16)     # 16 rows: cpu (two columns), memory, network and disk fill the tile with no
                     # empty rows; flat enough that a 2x3 grid keeps roughly the hero's 5:3 aspect
COMPACT = (80, 24)
WIDE = (120, 36)     # --no-border and the snapshot: a comfortable terminal, every section present
NARROW = (100, 26)   # a laptop terminal: the layout has to drop a section whole
GLYPH_TILE = (100, 34)   # the glyph levels differ in the CHARTS, which the budget only fits from 32 rows on
THEMES_LIST = (60, len(T.theme_names()) + 2)
SHOT_T0 = calendar.timegm((2026, 9, 5, 8, 0, 0))   # the demo clock starts here, not at wall time: same picture every run

_RICH_COLOR_SYSTEM = {"truecolor": "truecolor", "256": "256", "16": "standard", "mono": None}


def svg_format(bg):
    """rich's export format minus the window chrome: the terminal fills the whole viewBox."""
    return (CONSOLE_SVG_FORMAT
            .replace('viewBox="0 0 {width} {height}"', 'viewBox="0 0 {terminal_width} {terminal_height}"')
            .replace('{chrome}', '<rect width="{terminal_width}" height="{terminal_height}" fill="' + bg + '"/>')
            .replace('translate({terminal_x}, {terminal_y})', 'translate(0, 0)'))


def console(w, h, color="truecolor"):
    return Console(record=True, file=io.StringIO(), width=w, height=h, force_terminal=True,
                   color_system=_RICH_COLOR_SYSTEM[color], legacy_windows=False, no_color=False)


def fresh_cli():
    """A private, freshly executed copy of termstats.cli - empty histories, default state."""
    spec = importlib.util.spec_from_file_location("termstats._screenshot_cli", termstats.cli.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def theme_bg(cli):
    rgb = getattr(cli.THEME, "bg", None)
    if isinstance(rgb, tuple):
        return "#%02x%02x%02x" % rgb
    return rgb or "#0d1117"


def write(out, con, name, bg):
    svg = con.export_svg(title="termstats", code_format=svg_format(bg), clear=True)
    path = Path(out) / f"{name}.svg"
    path.write_text(svg, encoding="utf-8")
    print(f"{name}.svg {len(svg)} bytes")
    return path.name


def dashboard(out, name, size, theme=T.DEFAULT_THEME, color="truecolor", glyphs="braille",
              compact=False, no_border=False, prefill=True, interval=0.5):
    """One dashboard frame of the demo machine.

    prefill=True plays 60 frames first so the charts are open and the CPU spike is on screen;
    prefill=False is what `termstats --once` prints - the first frame, charts still collecting.
    interval is both the demo's frame spacing and the module's sample interval, so the header
    ("0.5s") and the chart title ("last 30s") agree with each other - live default 0.5, a
    snapshot samples at 1 s like `run_once` does.
    """
    cli = fresh_cli()
    cli.CAPS = T.Capabilities(color=color, glyphs=glyphs, nerd=False)
    cli.set_glyph_level(glyphs)
    cli.set_theme(theme, color=color)
    cli.set_frame(compact=compact, no_border=no_border)
    source = demo.DemoSource(demo.DEFAULT_SEED, interval)
    source.T0 = SHOT_T0
    cli.sample_interval = interval
    cli.set_demo(source)
    cli._prime_measurements()
    if prefill:
        cli._prefill_history()
    # No pause is needed for the un-prefilled snapshot: rendering the CPU section steps the demo
    # one frame before the rate collectors run, so their seeded previous sample is a second old.
    w, h = size
    frame = cli.render_dashboard(w, h)
    if color == "mono":
        # NO_COLOR: rich drops every escape at the terminal, but an SVG export reads the recorded
        # styles, colours and all. Render to plain text first and record THAT.
        plain = Console(file=io.StringIO(), width=w, height=h, color_system=None, force_terminal=False,
                        no_color=True, legacy_windows=False)
        plain.print(frame)
        frame = Text(plain.file.getvalue().rstrip("\n"), no_wrap=True)
    con = console(w, h, color if color != "mono" else "truecolor")
    cli.console = con
    con.print(frame, highlight=False)
    return write(out, con, name, theme_bg(cli))


AUDIO = (120, 36)
AUDIO_SECONDS = 8.0   # scripted music before the frame: the tempo needs ~5 s to lock


def audio_view(out, mode):
    """One microphone screen fed by the demo synth - no device, same picture every time."""
    from termstats import audio
    cli = fresh_cli()
    cli.set_theme(T.DEFAULT_THEME, color="truecolor")
    source = audio.DemoAudio(seed=7)
    an = audio.Analyzer(source.samplerate, audio.BLOCK)
    while source.now() < AUDIO_SECONDS:
        an.feed(source.read(audio.BLOCK), source.now())
    demo_source = demo.DemoSource(demo.DEFAULT_SEED, 0.5)
    demo_source.T0 = SHOT_T0
    cli.set_demo(demo_source)
    cli._prime_measurements()
    w, h = AUDIO
    con = console(w, h)
    cli.console = con
    con.print(cli.render_audio(mode, an, source.now(), w, h))
    return write(out, con, mode, theme_bg(cli))


def help_view(out):
    text = subprocess.run([sys.executable, "-m", "termstats", "--help"], capture_output=True, text=True).stdout
    cli = fresh_cli()
    width = max(len(line) for line in text.splitlines()) + 1   # natural width: no wrapped lines
    con = console(width, text.count("\n") + 1)
    con.print(Text(text.rstrip("\n")), highlight=False)
    return write(out, con, "help", theme_bg(cli))


def list_themes_view(out):
    cli = fresh_cli()
    cli.set_theme(T.DEFAULT_THEME, color="truecolor")
    w, h = THEMES_LIST
    con = console(w, h)
    cli.console = con
    cli.print_themes()
    return write(out, con, "list-themes", theme_bg(cli))


VIEWS = {
    "hero": lambda out: dashboard(out, "hero", HERO),
    "compact": lambda out: dashboard(out, "compact", COMPACT, compact=True),
    "no-border": lambda out: dashboard(out, "no-border", WIDE, no_border=True),
    "narrow": lambda out: dashboard(out, "narrow", NARROW),
    "snapshot": lambda out: dashboard(out, "snapshot", WIDE, prefill=False, interval=1.0),
    "help": help_view,
    "list-themes": list_themes_view,
    "eq": lambda out: audio_view(out, "eq"),
    "bpm": lambda out: audio_view(out, "bpm"),
    "db": lambda out: audio_view(out, "db"),
}
for _theme in T.theme_names():
    VIEWS[f"theme-{_theme}"] = (lambda t: lambda out: dashboard(out, f"theme-{t}", TILE, theme=t))(_theme)
for _glyphs in T.GLYPH_LEVELS:
    VIEWS[f"glyph-{_glyphs}"] = (lambda g: lambda out: dashboard(out, f"glyph-{g}", GLYPH_TILE, glyphs=g))(_glyphs)
for _color in T.COLOR_LEVELS:
    VIEWS[f"color-{_color}"] = (lambda c: lambda out: dashboard(out, f"color-{c}", TILE, color=c))(_color)


def render(out, names=None):
    """Render the named views (default: all) into out; returns the file names written."""
    names = list(VIEWS) if names is None else list(names)
    unknown = [n for n in names if n not in VIEWS]
    if unknown:
        raise KeyError(f"unknown view(s): {', '.join(unknown)} - known: {', '.join(VIEWS)}")
    Path(out).mkdir(parents=True, exist_ok=True)
    return [VIEWS[name](out) for name in names]


def _figures(names, alt):
    return "\n".join(
        f'<figure><img src="{n}.svg" alt="{alt} {n.split("-", 1)[1]}"><figcaption>{n.split("-", 1)[1]}</figcaption></figure>'
        for n in names)


def write_index(out):
    """The page the browser rasterises: one section per README figure, addressable by id."""
    help_svg = Path(out) / "help.svg"
    help_w = 70
    if help_svg.is_file():
        import re
        m = re.search(r'viewBox="0 0 ([\d.]+)', help_svg.read_text(encoding="utf-8"))
        if m:
            help_w = float(m.group(1)) / 12.2
    help_px = round(help_w * 12.2)
    themes = _figures([f"theme-{t}" for t in T.theme_names()], "termstats theme")
    glyphs = _figures([f"glyph-{g}" for g in T.GLYPH_LEVELS], "termstats glyph level")
    colours = _figures([f"color-{c}" for c in T.COLOR_LEVELS], "termstats colour level")
    html = f"""<!doctype html><meta charset="utf-8"><title>termstats {__version__} screenshots</title>
<style>
  body{{margin:0;padding:24px;background:#fff;font-family:-apple-system,system-ui,sans-serif}}
  img{{display:block}}
  #hero img{{width:1700px}}
  #compact img{{width:980px}}
  #no-border img,#snapshot img,#eq img,#bpm img,#db img{{width:1460px}}
  #narrow img{{width:1220px}}
  #list-themes img{{width:732px}}
  #help img{{width:{help_px}px}}   /* rich draws 12.2 px per cell at its 20 px font; the SVG carries no intrinsic size */
  .tiles{{display:grid;gap:20px 24px;padding:24px;background:#0b0d12;width:max-content}}
  .tiles img{{width:640px;border-radius:6px}}
  #grid{{grid-template-columns:repeat(2,640px)}}
  #glyphs{{grid-template-columns:repeat(3,640px)}}
  #glyphs img{{width:640px}}
  #colours{{grid-template-columns:repeat(2,640px)}}
  figure{{margin:0}} figcaption{{color:#b8bcc8;font-size:15px;margin-top:8px;font-family:ui-monospace,Menlo,monospace}}
  section{{margin-bottom:40px;width:max-content}}
</style>
<section id="hero"><img src="hero.svg" alt="termstats {__version__} dashboard"></section>
<section id="grid" class="tiles">{themes}</section>
<section id="compact"><img src="compact.svg" alt="termstats in 80x24"></section>
<section id="no-border"><img src="no-border.svg" alt="termstats --no-border"></section>
<section id="narrow"><img src="narrow.svg" alt="termstats in a 100x26 terminal"></section>
<section id="snapshot"><img src="snapshot.svg" alt="termstats --once, first frame"></section>
<section id="list-themes"><img src="list-themes.svg" alt="termstats --list-themes"></section>
<section id="glyphs" class="tiles">{glyphs}</section>
<section id="colours" class="tiles">{colours}</section>
<section id="eq"><img src="eq.svg" alt="termstats -eq: the spectrum analyser"></section>
<section id="bpm"><img src="bpm.svg" alt="termstats -bpm: the tempo detector"></section>
<section id="db"><img src="db.svg" alt="termstats -db: the level meter"></section>
<section id="help"><img src="help.svg" alt="termstats --help"></section>
"""
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / "index.html").write_text(html, encoding="utf-8")
    print("index.html written")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0].startswith("--"):
        sys.exit(__doc__.strip().splitlines()[2].strip())
    out = argv[0]
    names = None
    if len(argv) >= 3 and argv[1] == "--only":
        names = [n for n in argv[2].split(",") if n]
    elif len(argv) > 1:
        sys.exit(f"unexpected arguments: {' '.join(argv[1:])}")
    render(out, names)
    write_index(out)


if __name__ == "__main__":
    main()
