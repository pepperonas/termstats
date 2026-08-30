"""Pure formatting helpers: bar_horizontal and _fmt_bytes_rate."""

import re

import pytest

from termstats import cli

ANSI_MARKUP = re.compile(r"\[/?[a-z ]+\]")


def bar_glyphs(text):
    return ANSI_MARKUP.sub("", text)


# --- bar_horizontal ---------------------------------------------------------------

def test_bar_is_exactly_the_requested_width():
    plain = bar_glyphs(cli.bar_horizontal("x", 37.0, width=40))
    assert plain.count("█") + plain.count("░") == 40


@pytest.mark.parametrize("width", [10, 20, 40, 64])
def test_width_is_honoured(width):
    plain = bar_glyphs(cli.bar_horizontal("x", 50.0, width=width))
    assert plain.count("█") + plain.count("░") == width


@pytest.mark.parametrize("percent,filled", [(0, 0), (25, 10), (50, 20), (100, 40)])
def test_fill_is_proportional(percent, filled):
    plain = bar_glyphs(cli.bar_horizontal("x", percent, width=40))
    assert plain.count("█") == filled
    assert plain.count("░") == 40 - filled


def test_empty_bar_at_zero():
    assert "█" not in bar_glyphs(cli.bar_horizontal("x", 0.0))


def test_full_bar_at_hundred():
    assert "░" not in bar_glyphs(cli.bar_horizontal("x", 100.0))


def test_label_is_right_aligned_in_twelve_columns():
    assert cli.bar_horizontal("RAM", 10.0).startswith(" " * 9 + "RAM ")


def test_percentage_is_printed_with_one_decimal():
    assert cli.bar_horizontal("x", 7.25).endswith("  7.2%")


@pytest.mark.parametrize("percent,expected", [
    (0.0, "green"), (69.9, "green"), (70.0, "green"),
    (70.1, "yellow"), (85.0, "yellow"), (90.0, "yellow"),
    (90.1, "red"), (100.0, "red"),
])
def test_threshold_colours(percent, expected):
    """Both thresholds are strict >, so 70 and 90 sit in the lower band."""
    assert f"[{expected}]" in cli.bar_horizontal("x", percent)


def test_caller_colour_is_used_below_the_thresholds():
    assert "[cyan]" in cli.bar_horizontal("Total", 12.0, color="cyan")


def test_caller_colour_is_overridden_when_it_matters():
    """A quiet colour must not hide a critical reading."""
    assert "[red]" in cli.bar_horizontal("Total", 95.0, color="cyan")
    assert "[cyan]" not in cli.bar_horizontal("Total", 95.0, color="cyan")


def test_over_hundred_percent_does_not_raise():
    """cpu_percent can overshoot slightly; a bar is not worth a crash."""
    assert cli.bar_horizontal("x", 105.0)


# --- _fmt_bytes_rate --------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (0, "0 B/s"),
    (1, "1 B/s"),
    (999, "999 B/s"),
    (1024, "1024 B/s"),          # boundary is strict >, so 1 KiB still reads as bytes
    (1025, "1.0 KB/s"),
    (2048, "2.0 KB/s"),
    (1024 ** 2, "1024.0 KB/s"),  # same strict boundary one scale up
    (1024 ** 2 + 1, "1.0 MB/s"),
    (5 * 1024 ** 2, "5.0 MB/s"),
])
def test_rate_scaling(value, expected):
    assert cli._fmt_bytes_rate(value) == expected


def test_rate_never_returns_an_empty_string():
    for value in (0, 0.4, 1023.9, 1024.1, 1e9):
        assert cli._fmt_bytes_rate(value).endswith("/s")
