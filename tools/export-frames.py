"""Rendert termstats-Frames in eine aufzeichnende rich-Console und exportiert sie als SVG
ohne rich-Fensterchrome (die Website-Bühne zeichnet den Rahmen selbst)."""
import io, sys, time, subprocess
from rich.console import Console
from rich.text import Text
from rich._export_format import CONSOLE_SVG_FORMAT
from rich.terminal_theme import SVG_EXPORT_THEME as THEME
import termstats.cli as cli

OUT = sys.argv[1]
BG = "#%02x%02x%02x" % THEME.background_color
FORMAT = (CONSOLE_SVG_FORMAT
          .replace('viewBox="0 0 {width} {height}"', 'viewBox="0 0 {terminal_width} {terminal_height}"')
          .replace('{chrome}', '<rect width="{terminal_width}" height="{terminal_height}" fill="' + BG + '"/>')
          .replace('translate({terminal_x}, {terminal_y})', 'translate(0, 0)'))

def console(w, h):
    return Console(record=True, file=io.StringIO(), width=w, height=h, force_terminal=True,
                   color_system="truecolor", legacy_windows=False)

def export(con, name):
    svg = con.export_svg(title="termstats", theme=THEME, code_format=FORMAT, clear=True)
    open(f"{OUT}/{name}.svg", "w").write(svg)
    print(name, len(svg), "bytes")

cli.UNICODE = True
cli.sample_interval = 0.5
cli._prime_measurements()
t0 = time.monotonic()
frames = 0
while time.monotonic() - t0 < 32:
    time.sleep(0.5)
    cli.console = console(132, 40)
    cli.render_dashboard(132, 40); frames += 1
print("frames", frames, "history", len(cli.cpu_history))

for name, (w, h) in {"wide": (132, 40), "compact": (80, 24)}.items():
    cli.console = console(w, h)
    cli.console.print(cli.render_dashboard(w, h))
    export(cli.console, name)

help_txt = subprocess.run([sys.executable, "-m", "termstats", "--help"], capture_output=True, text=True).stdout
con = console(80, help_txt.count("\n") + 1)
con.print(Text(help_txt.rstrip("\n")), highlight=False)
export(con, "help")
