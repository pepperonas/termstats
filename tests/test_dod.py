"""S10 - the Definition of Done, as pins.

Every line here is a sentence from the brief: no hard-coded colours or glyphs in cli.py,
readable with NO_COLOR and in a pipe, every fallback step renders, every theme clears a
contrast minimum against the background it was designed for.
"""

import io
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console
from rich.style import Style

from termstats import cli
from termstats import theme as T
from helpers import plain

CLI_SRC = (Path(__file__).resolve().parent.parent / "termstats" / "cli.py").read_text(encoding="utf-8")
CLI_CODE = "\n".join(l for l in CLI_SRC.splitlines() if not l.lstrip().startswith("#"))
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


# --- no hard-coded colours or glyphs in cli.py -------------------------------------------------

def test_cli_has_no_hard_coded_colours():
    assert not re.search(r"#[0-9a-fA-F]{6}\b", CLI_CODE), "a hex colour in cli.py - it belongs in theme.py"
    assert not re.search(r"\b(?:bright_)?(?:red|green|yellow|blue|magenta|cyan|white)\b\"", CLI_CODE)


def test_cli_has_no_hard_coded_glyphs():
    """Every drawn character comes from a GlyphSet; cli.py may only name the set."""
    literals = re.findall(r"\"([^\"\\n]*)\"", CLI_CODE) + re.findall(r"'([^'\\n]*)'", CLI_CODE)
    drawn = set("".join(literals)) & set(T.GLYPH_PROBE)
    assert not drawn, sorted(drawn)


# --- contrast --------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", T.theme_names())
def test_text_clears_wcag_aa_against_the_theme_background(name):
    theme = T.resolve_theme(name)
    for role in ("text", "soft", "accent"):
        assert T.contrast_ratio(getattr(theme, role), theme.bg) >= 4.5, (name, role)


@pytest.mark.parametrize("name", T.theme_names())
def test_annotation_tones_stay_legible(name):
    theme = T.resolve_theme(name)
    # Floors from the measured table of all six themes, not from a wish: the track is a
    # palette's own quiet surface (nord 1.24, gruvbox 1.27) and must stay visible, not loud.
    for role, floor in (("dim", 3.0), ("muted", 2.2), ("faint", 1.5), ("border", 1.5), ("track", 1.2)):
        assert T.contrast_ratio(getattr(theme, role), theme.bg) >= floor, (name, role)


@pytest.mark.parametrize("name", T.theme_names())
def test_the_whole_ramp_is_readable_on_the_background(name):
    """The ramp colours the VALUE digits too, so even the idle end must read as text on
    the theme's ground. (nord's idle stop was nord3 at 1.69:1, viridis' deep purple 1.19:1
    - a meter at 3 % with a near-invisible number.)"""
    theme = T.resolve_theme(name)
    ramp = T.Ramp(theme.stops)
    for i in range(11):
        floor = 2.0 if i < 4 else 3.0
        assert T.contrast_ratio(ramp.rgb(i / 10), theme.bg) >= floor, (name, i / 10)


def test_contrast_ratio_is_the_wcag_formula():
    assert T.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert T.contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0)
    assert T.contrast_ratio("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)


# --- NO_COLOR and pipes ------------------------------------------------------------------------------

def fresh_styles():
    """rich caches a Style's rendered escape string on the instance the first time it is
    rendered, and Style.parse() hands out the same instance for the same text. One process
    never switches colour systems - this suite does, so the caches are dropped first."""
    for attr in ("parse", "_add", "chain", "combine", "normalize"):
        fn = getattr(Style, attr, None)
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()


def test_no_color_output_has_no_escape_codes(monkeypatch, capsys, primed_history):
    monkeypatch.setattr(cli, "CAPS", T.Capabilities(color="mono", glyphs="braille", nerd=False))
    cli.set_theme("default", color="mono")
    cli.configure_console()
    cli.console.size = (100, 30)               # capsys is not a terminal: no size to read
    fresh_styles()
    cli.console.print(cli.render_dashboard(100, 30))
    out = capsys.readouterr().out
    assert "TERMSTATS" in out and "celox.io" in out
    assert not ANSI.search(out), "escape codes under NO_COLOR"


def test_a_pipe_gets_plain_text(primed_history):
    """A cron mail or `termstats > report.txt` must not carry colour codes."""
    buf = io.StringIO()
    console = Console(file=buf, width=100, height=30)          # not a terminal: rich decides
    console.print(cli.render_dashboard(100, 30))
    assert "TERMSTATS" in buf.getvalue() and not ANSI.search(buf.getvalue())


# --- fallback chains ------------------------------------------------------------------------------------

@pytest.mark.parametrize("level", T.GLYPH_LEVELS)
def test_every_glyph_level_renders_and_fits(level, primed_history):
    cli.set_glyph_level(level)
    out = plain(cli.render_dashboard(80, 24), width=80, height=24)
    rows = out.rstrip("\n").split("\n")
    assert 23 <= len(rows) <= 24 and all(len(r) <= 80 for r in rows)
    if level == "ascii":
        assert out.isascii()


@pytest.mark.parametrize("level, forbidden", [
    ("truecolor", None),
    ("256", "38;2;"),          # no truecolor sequences on a 256-colour terminal
    ("16", "38;5;"),           # no palette indices on a 16-colour terminal
    ("mono", "\x1b["),         # nothing at all
])
def test_every_colour_level_speaks_only_its_own_escapes(level, forbidden, primed_history):
    cli.set_theme("default", color=level)
    system = {"truecolor": "truecolor", "256": "256", "16": "standard", "mono": None}[level]
    fresh_styles()
    buf = io.StringIO()
    console = Console(file=buf, width=100, height=30, force_terminal=system is not None,
                      color_system=system)
    console.print(cli.render_dashboard(100, 30))
    out = buf.getvalue()
    assert "TERMSTATS" in out
    if forbidden:
        assert forbidden not in out, f"{level}: found {forbidden!r}"
    if level == "16":
        assert "38;2;" not in out


def utf8_stream():
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8")


def test_the_glyph_chain_degrades_in_order(monkeypatch):
    """braille -> block -> ascii, chosen by the environment, never by luck. (A bare
    StringIO has no encoding and reads as "cannot draw" - the stream must be a real one.)"""
    env = {"TERM": "xterm-256color"}
    assert T.detect(env=env, stream=utf8_stream()).glyphs == "braille"
    assert T.detect(env={**env, "TERMSTATS_GLYPHS": "block"}, stream=utf8_stream()).glyphs == "block"
    assert T.detect(env={"TERM": "dumb"}, stream=utf8_stream()).glyphs == "ascii"
    assert T.detect(env=env, stream=io.TextIOWrapper(io.BytesIO(), encoding="cp1252")).glyphs == "ascii"


def test_the_colour_chain_degrades_in_order():
    base = {"TERM": "xterm"}
    assert T.detect(env={**base, "COLORTERM": "truecolor"}, stream=io.StringIO()).color == "truecolor"
    assert T.detect(env={"TERM": "xterm-256color"}, stream=io.StringIO()).color == "256"
    assert T.detect(env=base, stream=io.StringIO()).color == "16"
    assert T.detect(env={**base, "NO_COLOR": "1"}, stream=io.StringIO()).color == "mono"


# --- one real process per colour level ------------------------------------------------------------

@pytest.mark.parametrize("term, colorterm, wanted, forbidden", [
    ("xterm", "", "\x1b[3", "38;"),                  # 16 colours: SGR 30-37 only
    ("xterm-256color", "", "38;5;", "38;2;"),        # 256: palette indices, no truecolor
    ("xterm-256color", "truecolor", "38;2;", None),  # truecolor
])
def test_a_fresh_process_speaks_the_terminal_it_is_given(term, colorterm, wanted, forbidden):
    """In-process tests share rich's style caches; a real process has one colour system
    from start to finish. FORCE_COLOR stands in for the tty the pipe is not."""
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "HOME", "TMPDIR")}
    env.update({"TERM": term, "FORCE_COLOR": "1", "COLUMNS": "100", "LINES": "30",
                "PYTHONIOENCODING": "utf-8"})
    if colorterm:
        env["COLORTERM"] = colorterm
    env.pop("NO_COLOR", None)
    r = subprocess.run([sys.executable, "-m", "termstats", "--demo", "--once"],
                       env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "DEMO" in r.stdout and wanted in r.stdout
    if forbidden:
        assert forbidden not in r.stdout
