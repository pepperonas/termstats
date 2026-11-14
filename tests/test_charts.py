"""Chart building: the dot rasteriser (braille / quadrants) and the ASCII fallback.

Until 0.8.0 the charts were drawn by plotext. It needed 20-50 ms for one chart - most of a
frame's CPU - and its 6.0.0 release removed the API the charts were built on (the 1.1.1
defect). The charts are rasterised in cli._dot_chart now; these tests pin what the picture
has to keep: the area, the line over it, the fixed axis, and that a failure costs a chart,
never the dashboard.
"""

import re

import pytest

from termstats import cli
from termstats import theme as T
from helpers import plain

ONE = [(list(range(60)), "x", "cyan")]


def colours(chart, row=None):
    """{hex colour: number of non-blank cells drawn in it}, for the whole chart or one row."""
    lines = chart.split("\n")
    seen = {}
    for line in (lines if row is None else [lines[row]]):
        for sp in line.spans:
            colour = getattr(sp.style, "color", None)
            if colour is None or colour.triplet is None:
                continue
            cells = sum(1 for ch in line.plain[sp.start:sp.end] if ch != " ")
            if cells:
                seen[colour.triplet.hex] = seen.get(colour.triplet.hex, 0) + cells
    return seen


# --- guard --------------------------------------------------------------------------

def test_the_charts_need_no_plotting_library():
    """plotext cost half a second of import and most of every frame; it must not come back
    through a convenience import."""
    import pathlib
    source = pathlib.Path(cli.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+plotext", source, re.M)


def test_a_raising_rasteriser_costs_a_chart_not_the_dashboard(monkeypatch):
    """House rule: a collector may return junk, it may not take the process down."""
    def boom(*_a, **_k):
        raise RuntimeError("the rasteriser exploded")

    monkeypatch.setattr(cli, "_dot_chart", boom)
    assert "Chart unavailable" in plain(cli._render_chart(ONE, None, 40, 8))


def test_a_raising_rasteriser_does_not_escape_the_public_chart_helpers(monkeypatch, primed_history):
    monkeypatch.setattr(cli, "_dot_chart", lambda *a, **k: (_ for _ in ()).throw(ValueError("nope")))
    assert "Chart unavailable" in plain(cli.get_cpu_chart(40, 8))
    assert "Chart unavailable" in plain(cli.get_net_chart(40, 8))


@pytest.mark.parametrize("values", [[float("nan")] * 5, [float("inf"), 1.0], []])
def test_junk_samples_cost_a_chart_at_most(values):
    out = plain(cli._render_chart([(values, "x", "cyan", True)], (0, 100), 40, 8))
    assert out.strip()


# --- not enough data -----------------------------------------------------------------

@pytest.mark.parametrize("samples", [0, 1])
def test_charts_wait_for_two_samples(samples):
    for _ in range(samples):
        cli.cpu_history.append(1.0)
        cli.net_sent_history.append(1.0)
        cli.net_recv_history.append(1.0)
    # Since S5 the empty state is a designed skeleton, not the sentence "Collecting data".
    assert "collecting" in plain(cli.get_cpu_chart(40, 8))
    assert "collecting" in plain(cli.get_net_chart(40, 8))


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
    by_label = {entry[1]: entry[0] for entry in captured_series[0]["series"]}
    assert by_label["TX"] == [1.0, 2.0]
    assert by_label["RX"] == [4.0, 8.0]


def test_network_chart_carries_both_directions(captured_series, primed_history):
    cli.get_net_chart(40, 8)
    assert {entry[1] for entry in captured_series[0]["series"]} == {"RX", "TX"}


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
    """A plot background fills the plot with black, which sits as a hard rectangle
    inside the panel whatever the terminal's own background is.

    Checked on the parsed styles, not on the raw escape text: rich re-encodes the colour
    for the target terminal, so matching a literal "\x1b[48;5;0m" would pass by accident.
    """
    chart = cli.get_cpu_chart(60, 10)
    backgrounds = {span.style.bgcolor for span in chart.spans
                   if getattr(span.style, "bgcolor", None) is not None}
    assert not backgrounds, f"the chart paints its own background: {backgrounds}"


def test_the_x_axis_is_labelled_in_time_not_sample_numbers(primed_history):
    """Sample indices (1.0, 15.8, 30.5) are a number nobody watching a
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
    """Neither braille nor block glyphs can be drawn there. Dropping the chart would remove
    the feature the tool is named for."""
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


# --- 0.3.0: area fills, ramp colours, scaled axes, subtitles ---------------------------

def flat(cpu=50.0, steal=0.0, rx=800.0, tx=200.0):
    """Histories with one constant value each: every row of the picture is predictable."""
    for dq, v in ((cli.cpu_history, cpu), (cli.steal_history, steal),
                  (cli.net_recv_history, rx * 1024), (cli.net_sent_history, tx * 1024)):
        dq.clear(); dq.extend([v] * cli.HISTORY_LEN)


def test_the_cpu_chart_is_drawn_as_a_filled_area():
    """Every cell from the floor up to the curve is drawn - not one dotted line at 50 %."""
    flat(cpu=50.0)
    chart = cli.get_cpu_chart(60, 10)
    rows = chart.plain.split("\n")[:-1]
    plot_w = 60 - T.AXIS_W_PCT
    drawn = [sum(1 for ch in row[T.AXIS_W_PCT:] if ch != " ") for row in rows]
    assert drawn[-1] == plot_w and drawn[-2] == plot_w, drawn
    assert drawn[0] == 0, "a 50 % curve must leave the top of the plot empty"


def test_the_cpu_area_takes_its_colour_from_the_ramp():
    flat(cpu=50.0)
    chart = cli.get_cpu_chart(60, 10)
    floor_row = len(chart.plain.split("\n")) - 2
    assert isinstance(cli.cpu_chart_colour(), tuple)
    assert T.hex_of(cli.cpu_chart_colour()) in colours(chart, floor_row)


def test_the_cpu_area_colour_follows_the_mean_load():
    """An amber chart says what an amber bar says: this window ran warm."""
    cli.cpu_history.clear(); cli.cpu_history.extend([5.0] * 60)
    cool = cli.cpu_chart_colour()
    cli.cpu_history.clear(); cli.cpu_history.extend([95.0] * 60)
    hot = cli.cpu_chart_colour()
    assert cool == cli.ramp_rgb(0.05) and hot == cli.ramp_rgb(0.95)
    assert cool != hot


def test_steal_is_a_line_over_the_area_not_a_second_fill(monkeypatch):
    monkeypatch.setattr(cli, "IS_LINUX", True)
    flat(cpu=20.0, steal=60.0)
    chart = cli.get_cpu_chart(60, 10)
    plot_w = 60 - T.AXIS_W_PCT
    steal = colours(chart).get(T.hex_of(cli.ramp_rgb(1.0)), 0)
    assert steal == plot_w, f"a flat line is one cell per column, a fill would be several: {steal}"


def test_only_one_network_series_is_filled():
    """Two overlapping fills turn to mud where they cross; rx is the area, tx the line."""
    flat(rx=800.0, tx=400.0)
    chart = cli.get_net_chart(60, 10)
    plot_w = 60 - T.AXIS_W_RATE
    tx = colours(chart).get(T.hex_of(cli.NET_TX_RGB), 0)
    rx = sum(n for hex_, n in colours(chart).items() if hex_ not in (T.hex_of(cli.NET_TX_RGB), cli.MUTED))
    assert tx == plot_w, f"tx is a line: one cell per column, got {tx}"
    assert rx > 3 * plot_w, f"rx is an area: several rows of cells, got {rx}"


def test_the_line_stays_visible_inside_the_area():
    """tx runs BELOW the top of the rx area here: the cells it crosses must carry its colour,
    or the line vanishes exactly where the two series are compared."""
    flat(rx=800.0, tx=400.0)
    chart = cli.get_net_chart(60, 10)
    lines = chart.plain.split("\n")
    tx_hex = T.hex_of(cli.NET_TX_RGB)
    tx_rows = [r for r in range(len(lines) - 1) if tx_hex in colours(chart, r)]
    assert len(tx_rows) == 1
    row = lines[tx_rows[0]][T.AXIS_W_RATE:]
    assert set(row) == {T.CHART_CELLS["braille"].glyphs[255]}, "the area under the line is still drawn"


def test_network_series_colours_come_from_the_ramp():
    assert cli.NET_RX_RGB == cli.ramp_rgb(0.0)
    assert cli.NET_TX_RGB == cli.ramp_rgb(0.55)
    assert cli.NET_RX_RGB != cli.NET_TX_RGB


def test_three_tuple_series_still_render(primed_history):
    """The fill flag is optional; older callers and the steal series pass three fields."""
    out = plain(cli._render_chart([(list(range(60)), "x", "cyan")], (0, 100), 60, 8))
    assert len(out.rstrip("\n").split("\n")) > 1


@pytest.mark.parametrize("peak_bytes,expected", [
    (500 * 1024, (1024, "KB/s")),
    (2 * 1024**2 - 1, (1024, "KB/s")),
    (2 * 1024**2, (1024**2, "MB/s")),
    (50 * 1024**2, (1024**2, "MB/s")),
])
def test_the_network_axis_switches_units_at_two_megabytes(peak_bytes, expected):
    cli.net_sent_history.extend([0.0, float(peak_bytes)])
    cli.net_recv_history.extend([0.0, 0.0])
    assert cli.net_scale() == expected


def test_an_empty_history_scales_in_kilobytes():
    assert cli.net_scale() == (1024, "KB/s")


def test_the_network_chart_uses_the_chosen_unit(captured_series):
    cli.net_sent_history.extend([0.0, 4 * 1024**2])
    cli.net_recv_history.extend([0.0, 2 * 1024**2])
    cli.get_net_chart(40, 8)
    by_label = {e[1]: e[0] for e in captured_series[0]["series"]}
    assert by_label["TX"] == [0.0, 4.0]          # megabytes, not 4096 kilobytes


def axis_labels(chart, axis_w):
    return [row[:axis_w] for row in chart.plain.split("\n")[:-1] if row[:axis_w].strip()]


def test_an_unbounded_axis_gets_a_round_top(primed_history):
    """Ticks like 466.6 / 373.3 are what an automatic axis lands on; 0 / 300 / 600 cost
    nothing to read."""
    chart = cli._render_chart([([0.0, 560.0], "x", "cyan")], None, 60, 8)
    labels = axis_labels(chart, T.AXIS_W_RATE)
    assert [lab.strip() for lab in labels] == ["600", "300", "0"]
    # Labels sit in a fixed-width field so the plot never shifts when the top changes.
    assert len({len(lab) for lab in labels}) == 1


def test_a_fixed_axis_keeps_its_own_ticks(primed_history):
    chart = cli._render_chart([([0.0, 42.0], "x", "cyan")], (0, 100), 60, 8)
    assert [lab.strip() for lab in axis_labels(chart, T.AXIS_W_PCT)] == ["100", "50", "0"]


def test_a_chart_is_exactly_as_large_as_it_was_asked_to_be(primed_history):
    for w, h in ((40, 8), (61, 9), (116, 12)):
        rows = cli.get_net_chart(w, h).plain.split("\n")
        assert len(rows) == h and {len(r) for r in rows} == {w}, (w, h)


def test_the_curve_reaches_the_floor_and_the_ceiling():
    """0 is the lowest dot of the bottom row, the axis top the highest dot of the top row."""
    cells = T.CHART_CELLS["braille"]
    chart = cli._render_chart([([0.0] * 30, "x", "cyan")], (0, 100), 40, 8)
    rows = chart.plain.split("\n")[:-1]
    floor_only = cells.glyphs[cells.bits[-1][0] | cells.bits[-1][1]]
    assert set(rows[-1][T.AXIS_W_PCT:]) == {floor_only}
    chart = cli._render_chart([([100.0] * 30, "x", "cyan")], (0, 100), 40, 8)
    top_only = cells.glyphs[cells.bits[0][0] | cells.bits[0][1]]
    assert set(chart.plain.split("\n")[0][T.AXIS_W_PCT:]) == {top_only}


def test_the_block_level_draws_quadrants_not_braille(primed_history):
    cli.set_glyph_level("block")
    out = plain(cli.get_cpu_chart(60, 10))
    assert not [ch for ch in out if 0x2800 <= ord(ch) <= 0x28FF]
    assert set(out) & set(T.CHART_CELLS["hd"].glyphs[1:])


def test_now_sits_at_the_right_edge_and_the_window_start_at_the_left(primed_history):
    cli.sample_interval = 0.5
    axis = cli.get_cpu_chart(60, 10).plain.split("\n")[-1]
    assert axis.rstrip().endswith("now") and axis.index("-30s") < axis.index("-15s") < axis.index("now")
    narrow = cli.get_cpu_chart(T.AXIS_W_PCT + 6, 6).plain.split("\n")[-1]
    assert "now" in narrow and "-15s" not in narrow, "a label with no room is dropped, never overlapped"


def test_the_cpu_subtitle_names_the_window_and_the_latest_value(primed_history):
    cli.sample_interval = 0.5
    cli.cpu_history.append(42.4)
    from rich.text import Text
    sub = Text.from_markup(cli.cpu_chart_subtitle()).plain   # the % has its own dim span
    assert "last 30s" in sub and "42%" in sub


def test_the_cpu_subtitle_colours_the_value_by_the_ramp():
    cli.cpu_history.append(95.0)
    assert cli.ramp(0.95) in cli.cpu_chart_subtitle()


def test_the_network_subtitle_is_a_legend_with_live_rates(primed_history):
    cli.net_recv_history.append(3.0 * 1024**2)
    cli.net_sent_history.append(1.5 * 1024**2)
    sub = cli.net_chart_subtitle()
    assert "▇ rx" in sub and "━ tx" in sub          # filled glyph for the area, bar for the line
    assert "3.0M/s" in sub and "1.5M/s" in sub          # fixed-width rate fields
    assert "MB/s" in sub


def test_the_network_legend_uses_the_series_colours(primed_history):
    sub = cli.net_chart_subtitle()
    assert "#%02x%02x%02x" % cli.NET_RX_RGB in sub
    assert "#%02x%02x%02x" % cli.NET_TX_RGB in sub


def test_subtitles_are_ascii_in_ascii_mode(ascii_mode, primed_history):
    """The separator dot was the one glyph that leaked into ASCII mode."""
    assert cli.cpu_chart_subtitle().isascii()
    assert cli.net_chart_subtitle().isascii()


def test_every_subtitle_glyph_is_in_the_probe():
    for glyph in ("▇", "━", "·"):
        assert glyph in cli._GLYPH_PROBE or glyph == "·", glyph


# --- S5: gradient fill, quiet scaffolding, the designed empty state -------------------------

def _fill_lightness_by_row(chart, fill_rgb):
    """OKLab lightness of the fill spans on each plot row, top to bottom."""
    from termstats import theme as T
    from rich.style import Style
    muted = T.rgb_of(cli.MUTED)
    rows = []
    for line in chart.split("\n"):
        styles = [Style.parse(sp.style) if isinstance(sp.style, str) else sp.style for sp in line.spans]
        cols = [st.color.triplet for st in styles
                if st and getattr(st, "color", None) is not None and st.color.triplet is not None]
        # the axis label shares the row with the fill; it is the muted tone, skip it
        cols = [c for c in cols if (c.red, c.green, c.blue) != muted]
        if cols:
            most = max(set(cols), key=cols.count)
            rows.append(T.lightness((most.red, most.green, most.blue)))
    return rows


def test_the_filled_area_fades_toward_the_background_at_the_top(primed_history):
    """Weight where the data is: solid at the base, thinner up top, so the line can read
    over it. On a dark theme that means the top rows are darker."""
    from termstats import theme as T
    cli.cpu_history.clear(); cli.cpu_history.extend([100.0] * 60)      # fill every row
    chart = cli.get_cpu_chart(60, 10)
    Ls = _fill_lightness_by_row(chart, cli.cpu_chart_colour())
    assert len(Ls) >= 4
    assert Ls[0] < Ls[-1], "top of the fill is not thinner than the base"
    assert all(b >= a - 1e-6 for a, b in zip(Ls, Ls[1:])), "the fade is not monotone"
    assert Ls[-1] == pytest.approx(T.lightness(cli.cpu_chart_colour()), abs=0.02)


def test_the_line_series_keeps_its_colour_over_the_fade(primed_history):
    """Only the FILL fades; tx is a line and must stay in its own colour everywhere."""
    from termstats import theme as T
    cli.net_recv_history.clear(); cli.net_recv_history.extend([1024.0 * 500] * 60)
    cli.net_sent_history.clear(); cli.net_sent_history.extend([1024.0 * 50] * 60)
    chart = cli.get_net_chart(60, 10)
    tx_hex = T.hex_of(cli.NET_TX_RGB)
    seen = {sp.style.color.triplet.hex for line in chart.split("\n") for sp in line.spans
            if sp.style and getattr(sp.style, "color", None) is not None and sp.style.color.triplet is not None}
    assert tx_hex in seen


def test_the_plot_has_no_frame_of_its_own(primed_history):
    """The panel border is the frame; a second box inside it was clutter and cost a row
    and two columns of data."""
    out = plain(cli.get_cpu_chart(60, 10))
    assert "┌" not in out and "┘" not in out and "┤" not in out


def test_axis_labels_are_in_the_muted_tone(primed_history):
    """Asks rich for the COLOUR, not for the bytes.

    Matching a literal "\x1b[38;2;108;108;108m" looked equivalent and was not: rich caches a
    Style's rendered escape on the instance, so whichever console rendered that style FIRST
    in the session decides the form - a 256-colour one earlier in the suite leaves
    "\x1b[38;5;242m" behind, and this test then failed depending on TERM and on test order.
    The colour is the same either way, and the colour is what the pin is about.
    """
    from rich.color import Color
    from rich.console import Console
    console = Console(width=80, force_terminal=True, no_color=False, legacy_windows=False,
                      color_system="truecolor")
    want = Color.parse(cli.MUTED).get_truecolor()
    tones = {seg.style.color.get_truecolor()
             for seg in console.render(cli.get_cpu_chart(60, 10))
             if seg.style is not None and seg.style.color is not None}
    assert want in tones, f"tick labels are not muted: {sorted(tones)}"


def test_the_empty_state_takes_the_charts_space():
    """Eight rows asked for, eight rows drawn - the blank ones included, so the panel
    does not change shape when the plot replaces the skeleton."""
    out = plain(cli._collecting(40, 8), width=60)
    assert out.count("\n") == 8
    assert all(len(line) <= 40 for line in out.split("\n"))


def test_the_empty_state_is_a_skeleton_with_a_hint():
    out = plain(cli._collecting(40, 8), width=60)
    assert cli.GLYPHS.collecting * 10 in out
    assert "collecting" in out and "2 samples" in out


def test_the_empty_state_is_ascii_in_ascii_mode(ascii_mode):
    assert plain(cli._collecting(40, 8), width=60).isascii()


def test_a_first_frame_shows_the_skeleton_not_a_bare_sentence():
    out = plain(cli.render_dashboard(140, 50), width=140, height=50)
    assert cli.GLYPHS.collecting * 10 in out
    assert "Collecting data" not in out


def test_the_ascii_chart_fades_as_well(ascii_mode):
    from termstats import theme as T
    chart = cli._ascii_chart([100.0] * 60, (0, 100), 40, 8)
    Ls = []
    for line in chart.renderables:
        cols = [sp.style for sp in line.spans if line.plain[sp.start] == cli.GLYPHS.chart_full]
        if cols:
            Ls.append(T.lightness(T.rgb_of(str(cols[0]))))
    assert len(Ls) >= 3 and Ls[0] < Ls[-1]


def test_a_chart_wider_than_eighty_columns_is_not_clamped_to_a_phantom_terminal(monkeypatch):
    """The plotting library the charts once used limited a plot to the terminal size IT detected -
    80 columns in a pipe, a test or a screenshot render. A 116-column chart came back 80 wide, ending two thirds across the
    panel while the meter above it ran to the edge. The caller decides the size, nothing else."""
    from rich.cells import cell_len
    monkeypatch.delenv("COLUMNS", raising=False)
    values = [50.0] * 110
    out = plain(cli._render_chart([(values, "x", cli.ramp_rgb(0.5), True)], (0.0, 100.0), 116, 10), width=116, height=10)
    widest = max(cell_len(l.rstrip()) for l in out.splitlines())
    assert widest >= 110, f"chart is only {widest} cells wide in a 116-column slot"


def test_a_steep_edge_of_a_line_is_joined_not_left_as_two_dots():
    """An unfilled series that jumps from the floor to the ceiling between two columns: the
    edge is drawn through every row, or a spike reads as two unrelated dots."""
    values = [0.0] * 10 + [100.0] * 10
    chart = cli._render_chart([(values, "x", "cyan")], (0, 100), 40, 9)
    rows = [row[T.AXIS_W_PCT:] for row in chart.plain.split("\n")[:-1]]
    assert all(row.strip() for row in rows), "a row of the plot is empty: the edge has a gap"
    middle = rows[len(rows) // 2]
    assert len(middle.replace(" ", "")) == 1, "the edge is one column wide, not an area"
