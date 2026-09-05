"""Meters: the colour ramp, the gradient bar and the one-line meter row.

The old bar was 40 fixed cells, three hard colour steps and a second line underneath for
the "6.1G / 16.0G" annotation. All three are gone: the width follows the panel, the colour
is a continuous ramp, and the annotation shares the line.
"""

import re

import pytest

from termstats import cli
from helpers import plain

HEX = re.compile(r"^#[0-9a-f]{6}$")


# --- the shared ramp ---------------------------------------------------------------

def test_ramp_returns_a_truecolor_hex():
    """Deliberately not a palette index - rich quantises to 256 or 16 by itself."""
    assert HEX.match(cli.ramp(0.0))
    assert HEX.match(cli.ramp(0.5))
    assert HEX.match(cli.ramp(1.0))


def test_ramp_hits_its_own_stops_exactly():
    """A stop is returned as designed, never re-derived through the interpolation.

    Positions come from the ramp itself: the 0.2.0 version hard-coded 0.0/0.55/1.0 and
    would have broken the day the ramp gained its desaturated idle stop.
    """
    for pos, rgb in cli.RAMP:
        assert cli.ramp(pos) == "#%02x%02x%02x" % rgb, f"stop at {pos} not returned exactly"


@pytest.mark.parametrize("t,expected", [(-5.0, 0.0), (0.0, 0.0), (1.0, 1.0), (99.0, 1.0)])
def test_ramp_clamps_out_of_range_input(t, expected):
    assert cli.ramp(t) == cli.ramp(expected)


def test_ramp_is_continuous():
    """A visible seam anywhere on the ramp would read as a threshold that isn't there."""
    previous = cli.ramp(0.0)
    for step in range(1, 101):
        current = cli.ramp(step / 100)
        jump = max(abs(int(current[i:i+2], 16) - int(previous[i:i+2], 16)) for i in (1, 3, 5))
        assert jump < 20, f"colour jumps by {jump} at t={step/100}"
        previous = current


def test_ramp_survives_nan():
    """percent can be NaN when a counter goes backwards; a crash there kills the frame."""
    assert HEX.match(cli.ramp(float("nan")))


def test_ramp_is_cool_at_the_bottom_and_hot_at_the_top():
    cold, hot = cli.ramp(0.0), cli.ramp(1.0)
    blue_cold, blue_hot = int(cold[5:7], 16), int(hot[5:7], 16)
    red_cold, red_hot = int(cold[1:3], 16), int(hot[1:3], 16)
    assert red_hot > red_cold and blue_cold > blue_hot


# --- the bar -----------------------------------------------------------------------

def cells(text):
    return plain(text, width=200).rstrip("\n")


@pytest.mark.parametrize("width", [6, 10, 20, 40, 64])
def test_bar_occupies_exactly_the_requested_width(width):
    assert len(cells(cli.bar(50.0, width))) == width


@pytest.mark.parametrize("pct,filled", [(0, 0), (25, 10), (50, 20), (100, 40)])
def test_fill_is_proportional(pct, filled):
    drawn = cells(cli.bar(pct, 40))
    assert drawn.count(cli.BAR_FULL) == filled
    assert drawn.count(cli.BAR_EMPTY) == 40 - filled


def test_a_partial_cell_is_drawn_for_the_remainder():
    """Eighth-blocks are what makes a 10-cell bar readable at all."""
    drawn = cells(cli.bar(55.0, 10))          # 5.5 cells
    assert drawn.count(cli.BAR_FULL) == 5
    assert any(ch in drawn for ch in cli.BAR_PARTIALS)


def test_no_partial_cell_when_the_bar_lands_on_a_boundary():
    assert not any(ch in cells(cli.bar(50.0, 10)) for ch in cli.BAR_PARTIALS)


def test_empty_bar_at_zero():
    assert cli.BAR_FULL not in cells(cli.bar(0.0, 20))


def test_full_bar_at_hundred():
    assert cli.BAR_EMPTY not in cells(cli.bar(100.0, 20))


@pytest.mark.parametrize("pct", [-40.0, 140.0, float("nan")])
def test_out_of_range_percentages_still_produce_a_valid_bar(pct):
    assert len(cells(cli.bar(pct, 16))) == 16


def test_a_zero_width_bar_is_empty_not_an_error():
    assert cells(cli.bar(50.0, 0)) == ""


def test_the_bar_is_a_gradient_not_one_flat_colour():
    """The point of the ramp: a long bar has to read as a scale."""
    styled = cli.bar(100.0, 40)
    colours = {span.style for span in styled.spans}
    assert len(colours) > 10, "the bar is painted in a single colour"


def test_ascii_mode_uses_only_ascii(ascii_mode):
    assert cells(cli.bar(62.5, 20)).isascii()


def test_ascii_mode_has_no_partial_cells(ascii_mode):
    """There is no ASCII glyph for three eighths of a block; rounding down is honest."""
    assert len(cells(cli.bar(55.0, 10))) == 10


# --- the meter row -----------------------------------------------------------------

@pytest.mark.parametrize("total", [30, 46, 60, 92])
def test_meter_occupies_exactly_the_width_it_was_given(total):
    assert len(cells(cli.meter("ram", 61.0, total, note="6.1G/16.0G"))) == total


def test_meter_fits_on_one_line():
    """The old form used two lines per value and doubled every panel's height."""
    assert plain(cli.meter("ram", 61.0, 46, note="6.1G/16.0G"), width=46).count("\n") == 1


def test_meter_shows_label_value_and_note():
    out = cells(cli.meter("ram", 61.0, 60, note="6.1G/16.0G"))
    assert "ram" in out and "61.0%" in out and "6.1G/16.0G" in out


def test_a_narrow_meter_drops_the_note_rather_than_slicing_it():
    """A cut-off "421.4G/460." still looks like a number, which is worse than no note."""
    out = cells(cli.meter("/", 99.2, 26, note="421.4G/460.4G"))
    assert "99.2%" in out
    assert "460." not in out, "the annotation was sliced instead of dropped"


def test_the_bar_never_vanishes_completely():
    out = cells(cli.meter("/", 50.0, 24, note="x" * 40))
    assert cli.BAR_FULL in out or cli.BAR_EMPTY in out


def test_an_explicit_value_replaces_the_percentage():
    assert "1.2MB/s" in cells(cli.meter("tx", 40.0, 50, value="1.2MB/s"))


def test_a_long_label_is_truncated_not_wrapped():
    assert len(cells(cli.meter("averyverylonglabel", 10.0, 40))) == 40


def test_percentage_carries_one_decimal():
    assert "7.2%" in cells(cli.meter("x", 7.25, 40))


# --- heat strip (many-core machines) ------------------------------------------------

def test_heat_strip_is_one_cell_per_value_when_it_fits():
    assert len(cells(cli.heat_strip([10.0] * 12, 40))) == 12


def test_heat_strip_downsamples_when_there_are_more_cores_than_cells():
    assert len(cells(cli.heat_strip([10.0] * 200, 20))) <= 20


def test_heat_strip_of_nothing_is_empty():
    assert cells(cli.heat_strip([], 20)) == ""


# --- rate formatting ----------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (0, "0 B/s"), (512, "512 B/s"), (1024, "1024 B/s"),
    (1025, "1.0 KB/s"), (1024**2, "1024.0 KB/s"), (1024**2 + 1, "1.0 MB/s"),
    (5 * 1024**2, "5.0 MB/s"),
])
def test_rate_scaling_across_the_boundaries(value, expected):
    """Both comparisons are strict >, so 1024 and 1 MiB sit in the lower unit."""
    assert cli._fmt_bytes_rate(value) == expected


# --- 0.3.0: rgb helpers, the secondary segment, the sparkline, memory formatting -----

def test_ramp_rgb_and_ramp_hex_agree():
    for t in (0.0, 0.3, 0.55, 0.8, 1.0):
        r, g, b = cli.ramp_rgb(t)
        assert cli.ramp(t) == f"#{r:02x}{g:02x}{b:02x}"


def test_ramp_rgb_returns_integers_in_range():
    for t in (0.0, 0.5, 1.0, float("nan")):
        assert all(isinstance(c, int) and 0 <= c <= 255 for c in cli.ramp_rgb(t))


def test_dim_rgb_keeps_the_hue_and_lowers_the_brightness():
    dimmed = cli.dim_rgb((200, 100, 50), 0.5)
    assert dimmed == "#643219"
    assert HEX.match(dimmed)


def test_secondary_segment_is_drawn_after_the_primary():
    drawn = cells(cli.bar(30.0, 20, secondary=50.0))
    assert drawn.count(cli.BAR_FULL) == 6
    assert drawn.count(cli.BAR_SECONDARY) == 10
    assert drawn.count(cli.BAR_EMPTY) == 4
    assert drawn.index(cli.BAR_SECONDARY) > drawn.rindex(cli.BAR_FULL)


def test_secondary_segment_never_pushes_the_bar_past_its_width():
    """Used 80% plus a claimed 50% cache: the cache is clamped, the width is not."""
    drawn = cells(cli.bar(80.0, 20, secondary=50.0))
    assert len(drawn) == 20
    assert drawn.count(cli.BAR_EMPTY) == 0


def _luma(hex_colour):
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_secondary_segment_is_dimmer_than_the_ramp_at_the_same_position():
    """It has to read as related to the bar but clearly not the same thing.

    ⚠️ "Different colour from the primary cells" is NOT the property - a secondary painted
    with the plain ramp at its own positions is also different, and that mutation passed
    the first version of this test. The property is: darker than the ramp would be there.
    """
    styled = cli.bar(30.0, 20, secondary=50.0)
    span = 19
    for sp in styled.spans:
        if styled.plain[sp.start] == cli.BAR_SECONDARY:
            position = sp.start
            assert _luma(str(sp.style)) < 0.6 * _luma(cli.ramp(position / span)), \
                f"cache cell at {position} is not dimmed"


def test_secondary_segment_has_an_ascii_form(ascii_mode):
    drawn = cells(cli.bar(30.0, 20, secondary=50.0))
    assert drawn.isascii()
    assert cli.ASCII_SECONDARY in drawn


@pytest.mark.parametrize("bad", [-10.0, float("nan")])
def test_a_nonsense_secondary_is_treated_as_zero(bad):
    assert cells(cli.bar(30.0, 20, secondary=bad)) == cells(cli.bar(30.0, 20))


def test_meter_value_and_colour_describe_the_whole_occupied_part():
    """32% used plus 52% cache prints 84% - the figure psutil calls percent."""
    out = cells(cli.meter("ram", 32.0, 60, secondary=52.0))
    assert "84.0%" in out


def test_meter_value_colour_follows_the_occupied_total_not_the_primary():
    cool = cli.meter("ram", 32.0, 60)
    hot = cli.meter("ram", 32.0, 60, secondary=60.0)
    cool_style = [sp.style for sp in cool.spans if "84.0%" in cool.plain[sp.start:sp.end] or "32.0%" in cool.plain[sp.start:sp.end]]
    hot_style = [sp.style for sp in hot.spans if "92.0%" in hot.plain[sp.start:sp.end]]
    assert cool_style and hot_style and cool_style[0] != hot_style[0]


def test_sparkline_is_always_exactly_its_width():
    """Sixty samples in sixteen cells is fifteen slices; the sixteenth cell is padding in
    the track tone. A sparkline that grew from one cell to fifteen while the history
    filled walked the header's clock left for thirty seconds."""
    assert len(cells(cli.sparkline([10.0] * 60, 16))) == 16
    assert len(cells(cli.sparkline([10.0] * 3, 16))) == 16
    assert len(cells(cli.sparkline([10.0], 16))) == 16


def test_sparkline_shows_the_peak_of_each_slice_not_the_mean():
    """A spike hidden inside a quiet slice is exactly what a sparkline exists to show."""
    quiet = [0.0] * 3 + [100.0]
    assert cells(cli.sparkline(quiet, 1)) == cli.SPARK[-1]


def test_sparkline_climbs_with_the_value():
    glyphs = [cells(cli.sparkline([v], 1)) for v in (0.0, 25.0, 50.0, 75.0, 100.0)]
    assert glyphs == sorted(glyphs, key=cli.SPARK.index)
    assert glyphs[0] == cli.SPARK[0] and glyphs[-1] == cli.SPARK[-1]


def test_sparkline_is_empty_without_history():
    assert cells(cli.sparkline([], 16)) == ""


def test_sparkline_is_absent_in_ascii_mode(ascii_mode):
    """There is no ASCII glyph set with eight heights; nothing beats a wrong picture."""
    assert cells(cli.sparkline([50.0] * 60, 16)) == ""


def test_every_sparkline_glyph_is_in_the_probe():
    for glyph in cli.SPARK:
        assert glyph in cli._GLYPH_PROBE


@pytest.mark.parametrize("value,expected", [
    (0, "   0M"), (482 * 1024**2, " 482M"), (1023 * 1024**2, "1023M"),
    (1024**3, " 1.0G"), (int(1.25 * 1024**3), " 1.2G"), (12 * 1024**3, "12.0G"),
])
def test_rss_switches_to_gigabytes_at_one_gigabyte_in_a_fixed_field(value, expected):
    assert cli._fmt_mem(value) == expected
    assert len(expected) == 5


@pytest.mark.parametrize("x,expected", [
    (0, 1.0), (0.4, 0.4), (1, 1), (7, 8), (12, 15), (99, 100), (101, 150), (466.6, 500),
    (560, 600), (1300, 1500), (2100, 2500), (2600, 3000), (9999, 10000),
])
def test_nice_ceiling_lands_on_a_round_number(x, expected):
    assert cli.nice_ceiling(x) == pytest.approx(expected)


def test_nice_ceiling_never_goes_below_its_input():
    for x in (0.3, 1, 2.2, 47, 560, 1234, 98765):
        assert cli.nice_ceiling(x) >= x
