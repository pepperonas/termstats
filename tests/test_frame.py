"""S6 - frame, typography, grid: one box style, a quiet border, a clear title hierarchy,
and the two frame modes (--compact, --no-border) that must keep the layout invariants.
"""

import sys

import pytest

from termstats import cli
from termstats import theme as T
from helpers import plain

SIZES = [(80, 24), (100, 30), (120, 40), (140, 50), (60, 20), (120, 16)]


def styles_of(text):
    from rich.style import Style
    return [Style.parse(sp.style) if isinstance(sp.style, str) else sp.style for sp in text.spans]


# --- one frame ------------------------------------------------------------------------------

def test_every_panel_uses_the_same_border_colour(primed_history):
    """Five differently coloured frames were five competing accents. The frame is quiet
    and the same everywhere; the content carries the colour."""
    from rich.console import Console
    console = Console(width=140, height=50, force_terminal=True, color_system="truecolor")
    with console.capture() as cap:
        console.print(cli.render_dashboard(140, 50))
    out = cap.get()
    r, g, b = T.rgb_of(cli.THEME.border)
    assert out.count(f"\x1b[38;2;{r};{g};{b}m╭") >= 5, "not every panel frame is in the border tone"


@pytest.mark.parametrize("name", T.theme_names())
def test_the_border_is_quieter_than_the_labels_in_every_theme(name):
    theme = T.resolve_theme(name)
    assert T.lightness(T.rgb_of(theme.border)) < T.lightness(T.rgb_of(theme.dim))


@pytest.mark.parametrize("name", T.theme_names())
def test_the_accent_stands_out_from_the_border(name):
    theme = T.resolve_theme(name)
    assert T.lightness(T.rgb_of(theme.accent)) - T.lightness(T.rgb_of(theme.border)) > 0.2


def test_panel_titles_are_bold_accent():
    head = cli._title("cpu", "last 30s")
    assert f"[b {cli.THEME.accent}]cpu[/]" in head
    assert cli.MUTED in head and "last 30s" in head


def test_the_unit_is_dimmer_than_the_digits():
    """Value bright in the ramp tone, unit dim: the digits carry the weight."""
    m = cli.meter("tx", 50.0, 60, value=T.fmt_rate(45.2 * 1024), value_w=T.RATE_W, unit_w=3)
    spans = [(m.plain[sp.start:sp.end], st) for sp, st in zip(m.spans, styles_of(m))]
    unit = [st for txt, st in spans if txt == "K/s"]
    digits = [st for txt, st in spans if "45.2" in txt]
    assert unit and digits
    assert unit[0].color.triplet.hex == cli.DIM
    assert digits[0].bold and digits[0].color.triplet.hex != cli.DIM


def test_the_percent_sign_is_dim_by_default():
    m = cli.meter("cpu0", 62.5, 50)
    spans = [(m.plain[sp.start:sp.end], st) for sp, st in zip(m.spans, styles_of(m))]
    assert any(txt == "%" and st.color.triplet.hex == cli.DIM for txt, st in spans)


def test_nerd_font_icons_are_opt_in(monkeypatch):
    monkeypatch.setattr(cli, "CAPS", T.Capabilities(color="truecolor", glyphs="braille", nerd=False))
    assert T.NERD_ICONS["cpu"] not in cli._title("cpu")
    monkeypatch.setattr(cli, "CAPS", T.Capabilities(color="truecolor", glyphs="braille", nerd=True))
    assert T.NERD_ICONS["cpu"] in cli._title("cpu")


# --- chrome is decided in one place ---------------------------------------------------------

def test_chrome_follows_the_frame_mode():
    cli.set_frame()
    assert cli.chrome() == (T.PANEL_CHROME_H, T.PANEL_CHROME_W)
    cli.set_frame(compact=True)
    assert cli.chrome() == (T.PANEL_CHROME_H, T.COMPACT_CHROME_W)
    cli.set_frame(no_border=True)
    assert cli.chrome() == (T.RULE_CHROME_H, T.RULE_CHROME_W)


@pytest.mark.parametrize("mode", ["default", "compact", "no_border"])
@pytest.mark.parametrize("width,height", SIZES)
def test_every_frame_mode_keeps_the_layout_invariants(mode, width, height, primed_history):
    """Fits, fills, no line too wide, no blank line, no stump - in every frame mode."""
    cli.set_frame(compact=(mode == "compact"), no_border=(mode == "no_border"))
    out = plain(cli.render_dashboard(width, height), width=width, height=height)
    rows = out.rstrip("\n").split("\n")
    assert len(rows) <= height, "taller than the terminal"
    assert all(len(r) <= width for r in rows), "wider than the terminal"
    if mode == "no_border":
        # Without frames the budget's slack has nowhere to hide. It may sit at the very
        # bottom (a frame would have wrapped the same rows) - never inside the picture.
        while rows and not rows[-1].strip():
            rows.pop()
        assert len(rows) >= height - 3, "does not fill the terminal"
    else:
        assert len(rows) >= height - 1, "does not fill the terminal"
    # A chart's plot area legitimately has empty rows (the curve does not reach the top);
    # inside a frame they carried a border, without one they are blank. Exempt them.
    chart = [i for i, r in enumerate(rows) if "last 30s" in r or "-15s" in r]
    body = set(range(chart[0], chart[-1] + 1)) if len(chart) == 2 else set()
    assert not [r for i, r in enumerate(rows) if not r.strip() and i not in body], \
        "blank line inside the dashboard"
    assert any("TOTAL" in r for r in rows), "the cpu section lost its TOTAL row"


def test_no_border_draws_rules_instead_of_boxes(primed_history):
    cli.set_frame(no_border=True)
    out = plain(cli.render_dashboard(120, 40), width=120, height=40)
    assert not any(ch in out for ch in "╭╮╰╯│"), "box glyphs in --no-border"
    assert "─" in out, "no title rules"
    assert "cpu" in out and "processes" in out


def test_no_border_keeps_a_gutter_between_the_columns(primed_history):
    """Rules instead of frames must not let the two columns run into each other."""
    cli.set_frame(no_border=True)
    rows = plain(cli.render_dashboard(100, 30), width=100, height=30).split("\n")
    rule = rows[1]
    # Left gutter before the first title, and two gutter cells (left column's right,
    # right column's left) between the end of the cpu rule and the memory title.
    assert rule.startswith(" cpu "), rule
    assert "─  memory" in rule, rule


def test_no_border_gives_the_body_more_width_than_a_frame(primed_history):
    def bar_len(rows):
        row = next(r for r in rows if "cpu0" in r)
        return sum(row.count(ch) for ch in (cli.BAR_FULL, cli.BAR_EMPTY) + tuple(cli.BAR_PARTIALS))
    cli.set_frame()
    framed = bar_len(plain(cli.render_dashboard(120, 40), width=120, height=40).split("\n"))
    cli.set_frame(no_border=True)
    ruled = bar_len(plain(cli.render_dashboard(120, 40), width=120, height=40).split("\n"))
    assert ruled > framed


def test_compact_removes_the_inner_padding(primed_history):
    """The label ends one column further left: the padding cell is gone, the border stays."""
    cli.set_frame()
    normal = plain(cli.render_dashboard(120, 40), width=120, height=40).split("\n")
    cli.set_frame(compact=True)
    compact = plain(cli.render_dashboard(120, 40), width=120, height=40).split("\n")
    n_row = next(r for r in normal if "cpu0" in r)
    c_row = next(r for r in compact if "cpu0" in r)
    assert n_row.startswith("│") and c_row.startswith("│")
    assert n_row.index("cpu0") == c_row.index("cpu0") + 1


def test_compact_gains_bar_cells(primed_history):
    def bar_len(rows):
        row = next(r for r in rows if "cpu0" in r)
        return sum(row.count(ch) for ch in (cli.BAR_FULL, cli.BAR_EMPTY) + tuple(cli.BAR_PARTIALS))
    cli.set_frame()
    normal = bar_len(plain(cli.render_dashboard(120, 40), width=120, height=40).split("\n"))
    cli.set_frame(compact=True)
    compact = bar_len(plain(cli.render_dashboard(120, 40), width=120, height=40).split("\n"))
    assert compact > normal


def test_no_border_is_ascii_safe(ascii_mode, primed_history):
    cli.set_frame(no_border=True)
    assert plain(cli.render_dashboard(100, 30), width=100, height=30).isascii()


# --- flags -----------------------------------------------------------------------------------

@pytest.fixture
def run(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "run_live", lambda interval=cli.DEFAULT_INTERVAL: seen.update(mode="live"))
    monkeypatch.setattr(cli, "run_once", lambda: seen.update(mode="once"))
    monkeypatch.setattr(cli, "_stdout_is_interactive", lambda: False)

    def _run(*args):
        monkeypatch.setattr(sys, "argv", ["termstats", *args])
        cli.main()
        return seen

    return _run


@pytest.mark.parametrize("flag", ["--compact", "-compact"])
def test_compact_flag(run, flag):
    run(flag)
    assert cli.COMPACT is True and cli.NO_BORDER is False


@pytest.mark.parametrize("flag", ["--no-border", "-no-border"])
def test_no_border_flag(run, flag):
    run(flag)
    assert cli.NO_BORDER is True


def test_both_frame_flags_together_are_allowed(run):
    run("--compact", "--no-border")
    assert cli.COMPACT and cli.NO_BORDER


def test_frame_flags_are_documented(run, capsys):
    with pytest.raises(SystemExit):
        run("--help")
    out = capsys.readouterr().out
    assert "--compact" in out and "--no-border" in out


def test_defaults_are_unchanged(run):
    run()
    assert cli.COMPACT is False and cli.NO_BORDER is False


def test_the_chart_subtitle_dims_its_unit_like_every_meter(primed_history):
    """`42%` in the cpu chart title: digits in the ramp tone, the `%` dim - the meters do
    the same, and a title that shouts its unit while the rows whisper it is a second rule."""
    from rich.text import Text
    text = Text.from_markup(cli.cpu_chart_subtitle())
    spans = [(text.plain[sp.start:sp.end], st) for sp, st in zip(text.spans, styles_of(text))]
    unit = [st for txt, st in spans if txt == "%"]
    assert unit, "the % is not styled on its own"
    assert unit[0].color.triplet.hex == cli.DIM
