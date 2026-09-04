"""Chart building: the plotext-6 guard, braille rendering, and the ASCII fallback.

Cover for the 1.1.1 defect (plotext 6.0.0 removed the 5.x top-level API, so the first live
render died with AttributeError) and for the two things the redesign added: braille markers
and a hand-drawn chart for terminals that cannot draw plotext's box characters at all.
"""

import re

import pytest

from termstats import cli
from helpers import plain

ONE = [(list(range(60)), "x", "cyan")]


# --- guard --------------------------------------------------------------------------

def test_installed_plotext_has_the_five_api_names():
    """If this fails, the environment resolved plotext 6.x despite the <6 pin."""
    assert cli._PLOTEXT_5, "plotext 5.x API missing - check the pin in pyproject.toml"


def test_guard_checks_every_name_the_renderer_calls():
    import plotext as plt
    for name in ("clear_figure", "plot", "ylim", "plotsize", "build"):
        assert hasattr(plt, name)


def test_missing_api_degrades_to_a_note_instead_of_raising(monkeypatch):
    monkeypatch.setattr(cli, "_PLOTEXT_5", False)
    assert "plotext" in plain(cli._render_chart(ONE, None, 40, 8))


def test_the_note_tells_the_user_how_to_fix_it():
    assert "plotext<6" in cli._CHART_NEEDS_PLOTEXT_5


def test_a_raising_plotext_costs_a_chart_not_the_dashboard(monkeypatch):
    """House rule: a collector may return junk, it may not take the process down."""
    def boom(*_a, **_k):
        raise RuntimeError("plotext exploded")

    monkeypatch.setattr(cli.plt, "clear_figure", boom)
    assert "Chart unavailable" in plain(cli._render_chart(ONE, None, 40, 8))


def test_a_raising_plotext_does_not_escape_the_public_chart_helpers(monkeypatch, primed_history):
    monkeypatch.setattr(cli.plt, "plot", lambda *a, **k: (_ for _ in ()).throw(ValueError("nope")))
    assert "Chart unavailable" in plain(cli.get_cpu_chart(40, 8))
    assert "Chart unavailable" in plain(cli.get_net_chart(40, 8))


# --- not enough data -----------------------------------------------------------------

@pytest.mark.parametrize("samples", [0, 1])
def test_charts_wait_for_two_samples(samples):
    for _ in range(samples):
        cli.cpu_history.append(1.0)
        cli.net_sent_history.append(1.0)
        cli.net_recv_history.append(1.0)
    assert "Collecting data" in plain(cli.get_cpu_chart(40, 8))
    assert "Collecting data" in plain(cli.get_net_chart(40, 8))


def test_two_samples_are_enough(captured_series):
    for _ in range(2):
        cli.cpu_history.append(1.0)
        cli.net_sent_history.append(1.0)
        cli.net_recv_history.append(1.0)
    cli.get_cpu_chart(40, 8)
    cli.get_net_chart(40, 8)
    assert len(captured_series) == 2


# --- what gets drawn -----------------------------------------------------------------

def test_cpu_chart_is_clamped_to_a_percentage_axis(captured_series, primed_history):
    cli.get_cpu_chart(40, 8)
    assert captured_series[0]["ylim"] == (0, 100)


def test_network_chart_has_no_fixed_axis(captured_series, primed_history):
    cli.get_net_chart(40, 8)
    assert captured_series[0]["ylim"] is None


def test_network_values_are_converted_to_kilobytes(captured_series):
    cli.net_sent_history.extend([1024.0, 2048.0])
    cli.net_recv_history.extend([4096.0, 8192.0])
    cli.get_net_chart(40, 8)
    series = captured_series[0]["series"]
    assert series[0][0] == [1.0, 2.0]
    assert series[1][0] == [4.0, 8.0]


def test_network_chart_labels_both_directions(captured_series, primed_history):
    cli.get_net_chart(40, 8)
    assert [label for _, label, _ in captured_series[0]["series"]] == ["TX KB/s", "RX KB/s"]


def test_chart_size_is_passed_through(captured_series, primed_history):
    cli.get_cpu_chart(77, 13)
    assert (captured_series[0]["width"], captured_series[0]["height"]) == (77, 13)


# --- braille and the transparent theme -----------------------------------------------

def test_the_chart_is_drawn_with_braille_dots(primed_history):
    """2x4 dots per cell - four times the vertical resolution of the block markers, and
    the reason the line reads as a curve instead of a staircase."""
    out = plain(cli.get_cpu_chart(60, 10))
    braille = [ch for ch in out if 0x2800 <= ord(ch) <= 0x28FF]
    assert braille, "no braille glyphs in the chart"


def test_the_chart_does_not_paint_its_own_background(primed_history):
    """plotext's "dark" theme fills the plot with black, which sits as a hard rectangle
    inside the panel whatever the terminal's own background is.

    Checked on the parsed styles, not on the raw escape text: rich re-encodes the colour
    for the target terminal, so matching a literal "\x1b[48;5;0m" would pass by accident.
    """
    chart = cli.get_cpu_chart(60, 10)
    backgrounds = {span.style.bgcolor for span in chart.spans
                   if getattr(span.style, "bgcolor", None) is not None}
    assert not backgrounds, f"the chart paints its own background: {backgrounds}"


def test_the_x_axis_is_labelled_in_time_not_sample_numbers(primed_history):
    """plotext defaults to sample indices (1.0, 15.8, 30.5) - a number nobody watching a
    live dashboard has any use for."""
    cli.sample_interval = 0.5
    out = plain(cli.get_cpu_chart(60, 10))
    assert "now" in out
    assert "-30s" in out
    assert "15.8" not in out


def test_the_time_axis_follows_the_refresh_interval(primed_history):
    cli.sample_interval = 2.0
    assert "-120s" in plain(cli.get_cpu_chart(60, 10))


@pytest.mark.parametrize("n", [2, 30, 60])
def test_time_ticks_stay_inside_the_series(n):
    positions, labels = cli._time_ticks(n)
    assert all(0 <= p < n for p in positions)
    assert labels[-1] == "now"


# --- steal series (Linux only) --------------------------------------------------------

def test_steal_series_is_omitted_off_linux(captured_series, monkeypatch, primed_history):
    monkeypatch.setattr(cli, "IS_LINUX", False)
    cli.steal_history.clear()
    cli.steal_history.extend([5.0] * 60)
    cli.get_cpu_chart(40, 8)
    assert len(captured_series[0]["series"]) == 1


def test_steal_series_is_omitted_on_linux_when_always_zero(captured_series, monkeypatch, primed_history):
    monkeypatch.setattr(cli, "IS_LINUX", True)
    cli.get_cpu_chart(40, 8)
    assert len(captured_series[0]["series"]) == 1


def test_steal_series_is_drawn_on_linux_when_nonzero(captured_series, monkeypatch, primed_history):
    monkeypatch.setattr(cli, "IS_LINUX", True)
    cli.steal_history.clear()
    cli.steal_history.extend([0.0] * 59 + [7.5])
    cli.get_cpu_chart(40, 8)
    series = captured_series[0]["series"]
    assert len(series) == 2 and series[1][1] == "Steal %"


# --- the ASCII fallback ---------------------------------------------------------------

def test_a_non_unicode_terminal_still_gets_a_chart(ascii_mode, primed_history):
    """plotext frames every plot in box-drawing glyphs, so there is no marker choice that
    yields pure ASCII. Dropping the chart would remove the feature the tool is named for."""
    out = plain(cli.get_cpu_chart(48, 8))
    assert out.isascii()
    assert "#" in out or "=" in out


def test_the_ascii_chart_fills_the_height_it_was_given(ascii_mode, primed_history):
    """A one-line sparkline inside an eight-line panel is a hole with a squiggle in it."""
    body = plain(cli._ascii_chart(list(range(60)), (0, 100), 48, 8)).rstrip("\n")
    assert len(body.split("\n")) == 8


@pytest.mark.parametrize("width", [20, 48, 90])
def test_the_ascii_chart_respects_its_width(ascii_mode, width):
    body = plain(cli._ascii_chart(list(range(60)), (0, 100), width, 6), width=width + 10)
    assert all(len(line) <= width for line in body.rstrip("\n").split("\n"))


def test_the_ascii_chart_labels_its_axes(ascii_mode):
    out = plain(cli._ascii_chart(list(range(60)), (0, 100), 48, 6))
    assert "100" in out and "now" in out


def test_the_ascii_chart_of_nothing_is_empty(ascii_mode):
    assert plain(cli._ascii_chart([], (0, 100), 40, 6)).strip() == ""


def test_a_rising_series_climbs_the_ascii_chart(ascii_mode):
    """Counter-check that the columns actually follow the data rather than being noise."""
    rows = plain(cli._ascii_chart([0.0] * 30 + [100.0] * 30, (0, 100), 40, 6)).split("\n")
    top = rows[0]
    assert top.rstrip().endswith("#"), "the high half is not drawn at the top"
    assert "#" not in top[: len(top) // 2], "the low half should be empty at the top"


# --- real rendering --------------------------------------------------------------------

def test_real_chart_renders_more_than_one_line(primed_history):
    assert len(plain(cli.get_cpu_chart(60, 12)).rstrip("\n").split("\n")) > 1
