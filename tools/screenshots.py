"""Reproducible screenshots from `termstats --demo`.

    python tools/screenshots.py OUT_DIR

Writes one SVG per view (no rich window chrome — the README shows them bare, the
website draws its own frame) plus OUT_DIR/index.html, which lays them out with ids
(#hero, #grid, #compact, #help). Rasterise that page in a real browser (Playwright
element screenshots), see CLAUDE.md "Screenshots". Everything comes from the demo
machine with its default seed, so the pictures are the same every time — and they
carry the version from the header, so run this AFTER a version bump.
"""
import importlib
import io
import subprocess
import sys

from rich.console import Console
from rich.text import Text
from rich._export_format import CONSOLE_SVG_FORMAT

import termstats.theme as T
from termstats import __version__, demo

OUT = sys.argv[1]
HERO = (140, 42)
TILE = (100, 30)
COMPACT = (80, 24)


def svg_format(bg):
    """rich's export format minus the window chrome: the terminal fills the whole viewBox."""
    return (CONSOLE_SVG_FORMAT
            .replace('viewBox="0 0 {width} {height}"', 'viewBox="0 0 {terminal_width} {terminal_height}"')
            .replace('{chrome}', '<rect width="{terminal_width}" height="{terminal_height}" fill="' + bg + '"/>')
            .replace('translate({terminal_x}, {terminal_y})', 'translate(0, 0)'))


def console(w, h):
    return Console(record=True, file=io.StringIO(), width=w, height=h, force_terminal=True,
                   color_system="truecolor", legacy_windows=False, no_color=False)


def fresh_cli():
    """A clean module per frame: histories, smoother and peak markers start empty."""
    import termstats.cli as cli
    return importlib.reload(cli)


def theme_bg(cli):
    rgb = getattr(cli.THEME, "bg", None)
    if isinstance(rgb, tuple):
        return "#%02x%02x%02x" % rgb
    return rgb or "#0d1117"


def dashboard(name, theme, size, compact=False):
    cli = fresh_cli()
    cli.set_theme(theme, color="truecolor")
    cli.set_frame(compact=compact, no_border=False)
    cli.set_demo(demo.DemoSource(demo.DEFAULT_SEED, 1.0))
    cli._prime_measurements()
    cli._prefill_history()           # 60 frames: charts open full, the CPU spike is on screen
    w, h = size
    con = console(w, h)
    cli.console = con
    con.print(cli.render_dashboard(w, h))
    write(con, name, theme_bg(cli))


def write(con, name, bg):
    svg = con.export_svg(title="termstats", code_format=svg_format(bg), clear=True)
    with open(f"{OUT}/{name}.svg", "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"{name}.svg {len(svg)} bytes")


dashboard("hero", T.DEFAULT_THEME, HERO)
for theme in T.theme_names():
    dashboard(f"theme-{theme}", theme, TILE)
dashboard("compact", T.DEFAULT_THEME, COMPACT)

help_txt = subprocess.run([sys.executable, "-m", "termstats", "--help"], capture_output=True, text=True).stdout
cli = fresh_cli()
help_w = max(len(line) for line in help_txt.splitlines()) + 1   # natural width: no wrapped lines
con = console(help_w, help_txt.count("\n") + 1)
con.print(Text(help_txt.rstrip("\n")), highlight=False)
write(con, "help", theme_bg(cli))

help_px = round(help_w * 12.2)
tiles = "\n".join(
    f'<figure><img src="theme-{t}.svg" alt="termstats theme {t}"><figcaption>{t}</figcaption></figure>'
    for t in T.theme_names())
with open(f"{OUT}/index.html", "w", encoding="utf-8") as fh:
    fh.write(f"""<!doctype html><meta charset="utf-8"><title>termstats {__version__} screenshots</title>
<style>
  body{{margin:0;padding:24px;background:#fff;font-family:-apple-system,system-ui,sans-serif}}
  img{{display:block}}
  #hero img{{width:1700px}}
  #compact img{{width:980px}}
  #help img{{width:{help_px}px}}   /* rich draws 12.2 px per cell at its 20 px font; the SVG carries no intrinsic size */
  #grid{{display:grid;grid-template-columns:repeat(2,640px);gap:20px 24px;padding:24px;background:#0b0d12;width:max-content}}
  #grid img{{width:640px;border-radius:6px}}
  figure{{margin:0}} figcaption{{color:#b8bcc8;font-size:15px;margin-top:8px;font-family:ui-monospace,Menlo,monospace}}
  section{{margin-bottom:40px;width:max-content}}
</style>
<section id="hero"><img src="hero.svg" alt="termstats {__version__} dashboard"></section>
<section id="grid">{tiles}</section>
<section id="compact"><img src="compact.svg" alt="termstats in 80x24"></section>
<section id="help"><img src="help.svg" alt="termstats --help"></section>
""")
print("index.html written")
