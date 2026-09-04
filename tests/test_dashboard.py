"""The whole dashboard, rendered against the real machine.

The headline property lives here: **the dashboard fits the terminal**. Before the layout
rewrite it produced 79 lines on a 40-line terminal, so in live mode the charts and the
process list - the two panels the tool exists for - were simply never on screen.
"""

import os
import re

import pytest

from termstats import cli
from helpers import plain

SIZES = [(80, 24), (100, 30), (120, 40), (140, 50), (200, 60), (190, 45), (60, 20),
         (120, 14), (120, 16), (120, 18), (120, 22), (100, 26), (160, 33)]


def dash(width, height):
    return plain(cli.render_dashboard(width, height), width=width, height=height)


def lines(width, height):
    return dash(width, height).rstrip("\n").split("\n")


# --- it has to fit ------------------------------------------------------------------

@pytest.mark.parametrize("width,height", SIZES)
def test_the_dashboard_fits_the_terminal_height(width, height):
    """Anything taller is invisible in live mode: rich's alternate screen crops it."""
    assert len(lines(width, height)) <= height


@pytest.mark.parametrize("width,height", SIZES)
def test_no_line_is_wider_than_the_terminal(width, height):
    for line in lines(width, height):
        assert len(line) <= width, f"overflows {width} columns: {line[:60]!r}"


@pytest.mark.parametrize("width,height", SIZES)
def test_there_is_no_dead_space(width, height):
    """The old grid forced both columns to the height of the taller panel, so 39-61% of
    all lines had a completely empty right half."""
    assert not [line for line in lines(width, height) if not line.strip()]


@pytest.mark.parametrize("width,height", SIZES)
def test_the_dashboard_uses_the_height_it_was_given(width, height):
    """Filling it is the other half of fitting it - a 40-line dashboard on a 60-line
    terminal wastes twenty lines it could have spent on processes."""
    assert len(lines(width, height)) >= height - 1


# --- what is on screen ----------------------------------------------------------------

def test_a_roomy_terminal_shows_every_panel(primed_history):
    out = dash(140, 50)
    for panel in ("cpu", "memory", "disk", "network", "processes"):
        assert panel in out


def test_the_charts_are_on_screen_at_a_normal_size(primed_history):
    """This is the regression that started the rewrite."""
    out = dash(120, 40)
    assert "last" in out, "no chart window label - the history panels are missing"
    assert "processes" in out


@pytest.mark.parametrize("height", [10, 14, 18, 24])
def test_a_short_terminal_drops_panels_instead_of_overflowing(height):
    body = lines(120, height)
    assert len(body) <= height
    assert "cpu" in "\n".join(body), "the CPU panel must survive any size"


def test_a_tiny_terminal_still_renders_something():
    assert dash(40, 8).strip()


@pytest.mark.parametrize("width,height", SIZES)
def test_no_panel_is_drawn_as_a_stump(width, height):
    """A box with borders and no room for content is worse than no box: it costs three
    lines to say nothing. Panels are dropped whole, never squeezed to a frame."""
    body = lines(width, height)
    for i, line in enumerate(body):
        if line.lstrip().startswith(("╭", "+-")):
            rest = body[i + 1:]
            assert rest and not rest[0].lstrip().startswith(("╰", "+-")), \
                f"empty panel frame at line {i}"


@pytest.mark.parametrize("width,height", [(120, 14), (120, 18), (120, 24)])
def test_a_dropped_panel_leaves_its_lines_to_its_neighbour(width, height):
    """When the process list does not fit it must not leave a blank strip behind - the
    last section on screen takes the remainder."""
    assert len(lines(width, height)) == height


# --- the full geometry sweep -----------------------------------------------------------
#
# ⚠️ The parametrised tests above run with THIS machine's core count and platform. CI caught
# a stump on Linux at 60x20 that no macOS run could reproduce: Linux draws an extra steal
# meter, which tips the CPU panel over the height the budget assumed. This sweep fakes both
# axes so a Linux-only layout bug fails on a Mac.

@pytest.mark.parametrize("is_linux", [True, False])
@pytest.mark.parametrize("ncores", [2, 4, 10, 32, 128])
@pytest.mark.parametrize("width,height", [(40, 10), (60, 16), (60, 20), (70, 18), (80, 24),
                                          (92, 20), (100, 30), (120, 14), (120, 40), (200, 60)])
def test_the_layout_holds_on_any_machine(monkeypatch, is_linux, ncores, width, height):
    monkeypatch.setattr(cli, "IS_LINUX", is_linux)
    monkeypatch.setattr(cli.psutil, "cpu_percent",
                        lambda percpu=False: [30.0] * ncores if percpu else 30.0)
    monkeypatch.setattr(cli.psutil, "cpu_count", lambda: ncores)

    body = lines(width, height)
    assert len(body) <= height, "taller than the terminal"
    assert all(len(line) <= width for line in body), "wider than the terminal"
    assert not [line for line in body if not line.strip()], "blank line"
    for i, line in enumerate(body):
        if line.lstrip().startswith("╭"):
            assert i + 1 < len(body) and not body[i + 1].lstrip().startswith("╰"), \
                f"stump panel at line {i}"


# --- the header -------------------------------------------------------------------------

def test_header_carries_the_brand_and_version():
    head = plain(cli.header_line(140), width=140)
    assert "TERMSTATS" in head
    assert f"v{cli.__version__}" in head


def test_header_names_the_host_and_the_platform():
    head = plain(cli.header_line(140), width=140)
    import platform
    assert platform.node()[:24] in head
    assert any(name in head for name in ("Linux", "macOS", "Windows"))


def test_header_reports_load_uptime_and_process_count():
    head = plain(cli.header_line(140), width=140)
    assert "load" in head and "up" in head and "proc" in head


def test_header_shows_a_wall_clock():
    """Without it a frozen dashboard and an idle machine look exactly the same. Its
    absence was found by watching the live view in a pty, not by any unit test."""
    assert re.search(r"\d\d:\d\d:\d\d", plain(cli.header_line(140), width=140))


def test_header_shows_the_refresh_interval():
    cli.sample_interval = 2.0
    assert "2s" in plain(cli.header_line(140), width=140)


def test_header_is_one_line_at_every_width():
    for width in (60, 80, 120, 200):
        assert plain(cli.header_line(width), width=width).rstrip("\n").count("\n") == 0


def test_a_narrow_header_does_not_run_its_words_together():
    """Right-aligning the tail into negative space glued the process count to the refresh
    interval: "proc 7080.5s". It only shows up in a band of widths (87-100 on this
    machine), so the whole plausible range is swept rather than one guessed number.

    The assertion is on the shape, not on a literal: whatever follows "proc <n>" must be
    whitespace or the end of the line.
    """
    for width in range(60, 141):
        head = plain(cli.header_line(width), width=width).rstrip("\n")
        # \S would match the digits themselves ("proc 71" = \d+ -> 7, \S -> 1); the
        # character after the number has to be neither a digit nor whitespace.
        assert not re.search(r"proc \d+[^\d\s]", head), \
            f"the header collides with itself at width {width}: {head[-32:]!r}"


# --- history accounting -------------------------------------------------------------------

def test_each_render_records_exactly_one_history_sample():
    before = len(cli.cpu_history)
    cli.render_dashboard(120, 40)
    assert len(cli.cpu_history) == before + 1
    assert len(cli.net_sent_history) == before + 1


def test_history_is_capped_at_the_window_length():
    for _ in range(cli.HISTORY_LEN + 5):
        cli.cpu_history.append(1.0)
    assert len(cli.cpu_history) == cli.HISTORY_LEN


def test_first_render_shows_the_collecting_notice():
    assert "Collecting data" in dash(140, 50)


def test_charts_appear_once_there_is_history(primed_history):
    assert "Collecting data" not in dash(140, 50)


def test_dashboard_survives_a_dead_chart_backend(monkeypatch, primed_history):
    monkeypatch.setattr(cli, "_PLOTEXT_5", False)
    assert "cpu" in dash(140, 50)


# --- chart layout -------------------------------------------------------------------------
#
# plotext emits ANSI escapes inside the string it returns - roughly 190 bytes per line.
# Handed to rich as a plain str they are counted as printable cells, so a 70-column chart
# measured 259 wide, rich re-wrapped it, the axis broke into fragments and the title was cut
# mid-word ("CPU Usage (last"). from_ansi turns them into real styles.

def test_charts_reach_rich_as_parsed_ansi_not_as_a_string(primed_history):
    from rich.text import Text
    chart = cli._render_chart([(list(range(60)), "x", "cyan")], (0, 100), 60, 8)
    assert isinstance(chart, Text), "a bare str would restore the broken measurement"


def test_a_chart_measures_its_visible_width_not_its_escape_bytes(primed_history):
    chart = cli._render_chart([(list(range(60)), "x", "cyan")], (0, 100), 60, 8)
    for line in plain(chart, width=200).rstrip("\n").split("\n"):
        assert len(line) <= 60


def test_charts_never_rewrap(primed_history):
    """Even on a terminal narrower than the chart, cropping beats a shredded axis."""
    chart = cli._render_chart([(list(range(60)), "x", "cyan")], (0, 100), 60, 8)
    assert chart.no_wrap is True and chart.overflow == "crop"


@pytest.mark.parametrize("width", [80, 100, 120, 150, 200])
def test_the_chart_is_sized_to_fit_inside_its_panel(monkeypatch, width):
    """Two panels side by side: each spends 2 columns on borders and 2 on padding, and
    the pair shares one column of grid gap. One column too many and the plot frame is
    cropped off the right edge - silently, because a chart crops rather than wraps."""
    asked = []
    monkeypatch.setattr(cli, "get_cpu_chart", lambda w, h: asked.append((w, h)) or "x")
    monkeypatch.setattr(cli, "get_net_chart", lambda w, h: "x")
    cli.render_dashboard(width, 50)
    assert asked, "no chart was drawn"
    assert asked[0][0] <= (width - 1) // 2 - 4


def test_a_chart_that_fits_keeps_its_frame(primed_history):
    """Counter-check for the sizing rule: the closing corner of plotext's own frame has
    to survive into the panel."""
    assert "┘" in dash(140, 50), "the plot frame was cropped away"


# --- degrading ------------------------------------------------------------------------------

def test_the_whole_dashboard_is_ascii_when_the_terminal_cannot_do_better(ascii_mode, primed_history):
    """One unencodable glyph anywhere would be a UnicodeEncodeError on that stream."""
    assert dash(100, 28).isascii()


@pytest.mark.parametrize("width,height", [(100, 28), (140, 40)])
def test_ascii_mode_still_fits_and_fills(ascii_mode, width, height):
    body = lines(width, height)
    assert len(body) <= height
    assert all(len(line) <= width for line in body)


def test_ascii_mode_still_draws_the_charts(ascii_mode, primed_history):
    assert "last" in dash(120, 40)


# --- output encoding ---------------------------------------------------------------------------

class FakeStream:
    """Stands in for a redirected Windows stdout."""

    def __init__(self, encoding, reconfigurable=True):
        self.encoding = encoding
        self.reconfigured = None
        self._reconfigurable = reconfigurable

    def reconfigure(self, **kwargs):
        if not self._reconfigurable:
            raise OSError("cannot reconfigure")
        self.reconfigured = kwargs
        self.encoding = kwargs.get("encoding", self.encoding)


def test_a_cp1252_stream_is_widened_to_utf8(monkeypatch):
    """`termstats > out.txt` on Windows: cp1252 cannot encode the block glyphs."""
    out, err = FakeStream("cp1252"), FakeStream("cp1252")
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", err)
    cli._ensure_console_encoding()
    assert out.reconfigured == {"encoding": "utf-8", "errors": "replace"}
    assert err.reconfigured == {"encoding": "utf-8", "errors": "replace"}


def test_a_utf8_stream_is_left_alone(monkeypatch):
    stream = FakeStream("utf-8")
    monkeypatch.setattr(cli.sys, "stdout", stream)
    monkeypatch.setattr(cli.sys, "stderr", stream)
    cli._ensure_console_encoding()
    assert stream.reconfigured is None


def test_an_unreconfigurable_stream_does_not_raise(monkeypatch):
    stream = FakeStream("cp1252", reconfigurable=False)
    monkeypatch.setattr(cli.sys, "stdout", stream)
    monkeypatch.setattr(cli.sys, "stderr", stream)
    cli._ensure_console_encoding()          # must not raise


def test_a_stream_without_reconfigure_does_not_raise(monkeypatch):
    class Bare:
        encoding = "ascii"

    monkeypatch.setattr(cli.sys, "stdout", Bare())
    monkeypatch.setattr(cli.sys, "stderr", Bare())
    cli._ensure_console_encoding()          # pytest's capture objects look like this


def test_a_stream_with_no_encoding_attribute_does_not_raise(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdout", object())
    monkeypatch.setattr(cli.sys, "stderr", object())
    cli._ensure_console_encoding()


def test_every_glyph_the_dashboard_draws_is_in_the_probe():
    """If a new glyph is added to the output, the probe must learn about it."""
    for glyph in ("█", "░", "╭", "\U0001f37b", "▏", "╌"):
        assert glyph in cli._GLYPH_PROBE


def test_capability_detection_follows_the_stream(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdout", FakeStream("cp1252"))
    assert cli.detect_capabilities() is False
    monkeypatch.setattr(cli.sys, "stdout", FakeStream("utf-8"))
    assert cli.detect_capabilities() is True


def test_priming_does_not_raise():
    cli._prime_measurements()
