"""The three microphone screens: -eq, -bpm, -db. Rendered from a synthetic analyzer, no mic."""
import pytest

np = pytest.importorskip("numpy")

from rich.cells import cell_len  # noqa: E402

from termstats import audio, cli  # noqa: E402
from termstats import theme as T  # noqa: E402
from helpers import plain  # noqa: E402

SIZES = [(140, 42), (120, 36), (100, 30), (80, 24), (60, 20)]


def music(seconds=8.0, bpm=126.0):
    d = audio.DemoAudio(seed=7, bpm=bpm)
    an = audio.Analyzer(audio.SAMPLE_RATE, audio.BLOCK)
    while d.now() < seconds:
        an.feed(d.read(audio.BLOCK), d.now())
    return an, d.now()


def render(mode, an, now, w, h):
    return plain(cli.render_audio(mode, an, now, w, h), width=w, height=h)


@pytest.fixture(scope="module")
def played():
    return music()


# --- every mode, every size: fits ------------------------------------------------------------

@pytest.mark.parametrize("mode", cli.AUDIO_MODES)
@pytest.mark.parametrize("w,h", SIZES)
def test_audio_frames_fit_the_terminal(mode, w, h, played):
    an, now = played
    out = render(mode, an, now, w, h)
    lines = out.splitlines()
    assert len(lines) <= h, (mode, w, h, len(lines))
    assert all(cell_len(l) <= w for l in lines), (mode, w, h, max(cell_len(l) for l in lines))


@pytest.mark.parametrize("mode", cli.AUDIO_MODES)
def test_every_mode_carries_its_badge_in_the_header(mode, played):
    an, now = played
    first = render(mode, an, now, 120, 36).splitlines()[0]
    assert f" {mode.upper()} " in first
    assert "TERMSTATS" in first


@pytest.mark.parametrize("mode", cli.AUDIO_MODES)
def test_every_mode_shows_bpm_and_db_in_its_hud(mode, played):
    an, now = played
    out = render(mode, an, now, 120, 36)
    assert "bpm" in out.lower() and " db" in out.lower()   # 0.5.0: the shown scale is not dBFS


def test_live_footer_hint_in_audio_mode(played, monkeypatch):
    an, now = played
    monkeypatch.setattr(cli, "LIVE", True)
    assert "Ctrl+C" in render("eq", an, now, 120, 36)


# --- equalizer --------------------------------------------------------------------------

def test_eq_names_itself_and_its_range(played):
    an, now = played
    out = render("eq", an, now, 120, 36)
    assert "equalizer" in out
    assert "40" in out and "16k" in out          # the frequency axis


def test_eq_draws_bars_from_the_levels(played):
    an, now = played
    out = render("eq", an, now, 120, 36)
    assert out.count(cli.GLYPHS.bar_full) > 20, "a playing track must raise bars"


def test_eq_columns_adapt_to_the_width():
    assert cli.eq_columns(200) == audio.BANDS
    assert 4 <= cli.eq_columns(40) < cli.eq_columns(80) <= audio.BANDS
    widths = [cli.eq_columns(w) for w in range(30, 200, 5)]
    assert widths == sorted(widths), "more width never means fewer bars"


def test_eq_marks_a_peak_above_the_bar():
    an = audio.Analyzer()
    an.levels = [0.2] * audio.BANDS
    an.peaks = [0.8] * audio.BANDS
    out = render("eq", an, 0.0, 120, 36)
    assert cli.GLYPHS.vpeak in out


def test_eq_without_a_peak_above_the_bar_draws_no_marker():
    an = audio.Analyzer()
    an.levels = [0.5] * audio.BANDS
    an.peaks = [0.5] * audio.BANDS
    out = render("eq", an, 0.0, 120, 36)
    assert cli.GLYPHS.vpeak not in out


def test_eq_bars_are_as_tall_as_the_levels_say():
    an = audio.Analyzer()
    an.levels = [0.0] * audio.BANDS
    an.levels[0] = 1.0
    an.peaks = list(an.levels)
    out = render("eq", an, 0.0, 120, 36)
    body = [l for l in out.splitlines() if cli.GLYPHS.bar_full in l]
    assert len(body) >= 10, "a full band must fill most of the panel height"


def test_ascii_eq_draws_only_seven_bit(played):
    an, now = played
    cli.set_glyph_level("ascii")
    out = render("eq", an, now, 100, 30)
    bad = {ch for ch in out if ord(ch) > 127}
    assert not bad, sorted(bad)


# --- decibel meter -------------------------------------------------------------------------

def test_db_shows_the_level_with_one_decimal_and_the_unit(played):
    an, now = played
    out = render("db", an, now, 120, 36)
    assert f"{audio.spl(an.db):.1f}" in out          # 0.5.0: shown on the positive scale
    assert "dB" in out


def test_db_shows_session_min_and_max(played):
    an, now = played
    out = render("db", an, now, 120, 36)
    assert "min" in out and "max" in out
    assert f"{audio.spl(an.db_min):.1f}" in out and f"{audio.spl(an.db_max):.1f}" in out


def test_db_draws_a_meter_and_a_history_chart_when_tall(played):
    an, now = played
    out = render("db", an, now, 120, 36)
    assert cli.GLYPHS.bar_full in out
    assert "last " in out                      # the chart title names its window


def test_db_drops_the_chart_before_the_meter_when_short(played):
    an, now = played
    out = render("db", an, now, 80, 10)
    assert f"{audio.spl(an.db):.1f}" in out
    assert "last " not in out


# --- tempo -------------------------------------------------------------------------------

def test_bpm_shows_the_tempo_and_its_confidence(played):
    an, now = played
    out = render("bpm", an, now, 120, 36)
    assert an.bpm > 0
    assert str(an.bpm) in out
    assert "confidence" in out


def test_bpm_shows_a_placeholder_before_a_tempo_exists():
    an = audio.Analyzer()
    out = render("bpm", an, 0.0, 120, 36)
    assert "---" in out


def test_bpm_beat_indicator_lights_on_a_beat_and_rests_between():
    an = audio.Analyzer()
    an.last_beat = 10.0
    lit = render("bpm", an, 10.02, 120, 36)
    rest = render("bpm", an, 10.9, 120, 36)
    assert cli.GLYPHS.beat_on in lit
    assert cli.GLYPHS.beat_on not in rest and cli.GLYPHS.beat_off in rest


# --- glyph sets -----------------------------------------------------------------------------

@pytest.mark.parametrize("level", T.GLYPH_LEVELS)
def test_new_glyphs_are_part_of_the_probe_or_ascii(level):
    g = T.GLYPH_SETS[level]
    for ch in g.vpeak + g.beat_on + g.beat_off:
        assert ord(ch) < 128 or ch in T.GLYPH_PROBE, ch
    if level == "ascii":
        assert all(ord(ch) < 128 for ch in g.vpeak + g.beat_on + g.beat_off)


# --- the history charts tell the truth about their span and fill from the floor -----------------

def test_audio_chart_x_axis_names_the_real_span(played):
    an, now = played
    out = render("db", an, now, 120, 36)
    assert "last 8s" in out
    assert "-8s" in out, "the x axis must span the data, not the dashboard's 30 s window"
    assert "-30s" not in out


def test_level_chart_fills_from_the_floor_not_from_zero_db():
    values = [-20.0] * 60
    out = plain(cli._render_chart([(values, "dBFS", cli.ramp_rgb(0.5), True)], (-80.0, 0.0), 60, 12), width=60, height=12)
    lines = out.splitlines()                         # UNFILTERED: the empty rows are the evidence
    dots = [sum(1 for ch in l if "⠀" <= ch <= "⣿") for l in lines]
    top, bottom = dots[1], dots[-3]                  # just under the 0.0 label, just above the floor label
    assert bottom > 0 and top == 0, ("a -20 dB level must fill upward from the -80 floor, not downward from 0", dots)


# --- big, centred, smooth readouts (-db / -bpm) --------------------------------------------------

def body_text(mode, an, now, w, h):
    """The panel BODY, without the frame - margins inside a `|...|` row are always zero,
    so measuring centring on the framed output would pass no matter where the digits sit."""
    ch, cw = cli.chrome()
    return plain(cli._AUDIO_BODIES[mode](an, now, w - cw, h - ch - 2), width=w - cw, height=h)


def centred_rows(text, marker, width):
    """Rows of the BIG FONT, with their left and right margins.

    A row of big digits is the only line made of nothing but the bar glyph and spaces -
    matching `marker in line` alone also catches the HUD's confidence bar and the meters,
    which is how the first version of this check passed while measuring the wrong rows.
    """
    rows = [l for l in text.splitlines()
            if marker in l and set(l) <= {cli.GLYPHS.bar_full, " "}]
    return [(len(l) - len(l.lstrip()), width - len(l.rstrip())) for l in rows]


def test_big_digits_are_five_rows_of_equal_width_from_theme_glyphs():
    rows = cli.big_digits("-19.6")
    assert len(rows) == T.BIG_DIGIT_ROWS == 5
    widths = {len(r) for r in rows}
    assert len(widths) == 1, widths
    assert set("".join(rows)) <= {cli.GLYPHS.bar_full, " "}


def test_every_digit_has_a_distinct_shape():
    shapes = {d: tuple(cli.big_digits(d)) for d in "0123456789"}
    assert len(set(shapes.values())) == 10


def test_ascii_big_digits_are_seven_bit():
    cli.set_glyph_level("ascii")
    assert all(ord(ch) < 128 for ch in "".join(cli.big_digits("-19.6 123")))


@pytest.mark.parametrize("mode", ["db", "bpm"])
def test_the_headline_number_is_drawn_big_and_centred(mode, played):
    an, now = played
    width = 120
    text = body_text(mode, an, now, width, 36)
    inner = width - cli.chrome()[1]
    rows = centred_rows(text, cli.GLYPHS.bar_full, inner)      # not *3: "#.#" rows have no run of three
    assert len(rows) == T.BIG_DIGIT_ROWS, f"{mode} must draw its value in the big font"
    # The BLOCK is centred, not each row: "1" is ".#." and "4" ends "..#", so a single row's
    # own margins differ by design. The outermost ink on each side is what has to match.
    left, right = min(l for l, _ in rows), min(r for _, r in rows)
    assert abs(left - right) <= 1, (left, right, rows)
    assert left > 4, "centred, not flush left"


def test_db_still_names_its_unit(played):
    an, now = played
    assert "dB" in render("db", an, now, 120, 36)


@pytest.mark.parametrize("mode", ["db", "bpm"])
def test_small_screens_fall_back_to_the_one_line_readout(mode, played):
    an, now = played
    width, height = 80, 12
    text = body_text(mode, an, now, width, height)
    assert not centred_rows(text, cli.GLYPHS.bar_full, width), "no room for five rows of digits here"
    assert (f"{audio.spl(an.db):.1f}" if mode == "db" else str(an.bpm)) in render(mode, an, now, width, height)


def test_the_shown_level_glides_towards_the_measurement_in_live_mode(monkeypatch):
    monkeypatch.setattr(cli, "SMOOTHING", True)
    cli._smoother.reset()
    first = cli.shown_level(-20.0)
    second = cli.shown_level(-40.0)
    assert first == pytest.approx(-20.0)
    assert -40.0 < second < -20.0, "the readout eases, it does not jump"


def test_the_shown_level_is_raw_in_a_snapshot(monkeypatch):
    monkeypatch.setattr(cli, "SMOOTHING", False)
    cli.shown_level(-20.0)
    assert cli.shown_level(-40.0) == -40.0


def test_the_shown_tempo_glides_but_never_counts_up_from_nothing(monkeypatch):
    monkeypatch.setattr(cli, "SMOOTHING", True)
    cli._smoother.reset()
    assert cli.shown_tempo(0) == 0                       # no tempo yet: nothing to glide from
    assert cli.shown_tempo(124) == 124                   # a tempo appears: shown at once, not counted up from 0
    between = cli.shown_tempo(140)
    assert 124 < between < 140                           # a change of tempo eases
    assert cli.shown_tempo(0) == 0                       # tempo lost: the dashes come back at once
    assert cli.shown_tempo(90) == 90, ("a tempo found after a silence must appear as it is - easing "
                                       "from the forgotten 132 would count the number down on screen")


def test_big_tempo_digits_flash_on_the_beat():
    an = audio.Analyzer()
    an.last_beat = 10.0
    assert cli.tempo_tone(an, 10.02) != cli.tempo_tone(an, 10.9)
    assert cli.tempo_tone(an, 10.02) == cli.ramp(1.0)


def test_big_digits_are_scaled_horizontally_to_look_proportioned():
    """A terminal cell is about half as wide as it is tall, so a 3x5 glyph reads as a thin
    scratch rather than a headline number. Each filled cell is drawn `BIG_DIGIT_SCALE` wide."""
    rows = cli.big_digits("8")
    assert len(rows[0]) == 3 * T.BIG_DIGIT_SCALE
    assert T.BIG_DIGIT_SCALE >= 2
    assert rows[0] == cli.GLYPHS.bar_full * (3 * T.BIG_DIGIT_SCALE)      # "###" scaled
    assert rows[1] == (cli.GLYPHS.bar_full * T.BIG_DIGIT_SCALE + " " * T.BIG_DIGIT_SCALE
                       + cli.GLYPHS.bar_full * T.BIG_DIGIT_SCALE)        # "#.#" scaled


def test_the_headline_holds_its_value_between_readout_ticks(monkeypatch):
    """20 frames a second is the right cadence for a bar and far too fast for a digit: the
    last decimal would be a blur. The big number refreshes on its own, slower clock."""
    monkeypatch.setattr(cli, "SMOOTHING", True)
    cli._smoother.reset()
    cli._readouts.clear()
    first = cli.readout("x", 10.0, now=100.0)
    assert first == 10.0
    assert cli.readout("x", 99.0, now=100.0 + cli.AUDIO_READOUT_S / 2) == 10.0, "held"
    assert cli.readout("x", 99.0, now=100.0 + cli.AUDIO_READOUT_S * 1.1) == 99.0, "and refreshed"


def test_the_readout_hold_is_slower_than_the_frame_but_still_lively():
    assert cli.AUDIO_INTERVAL < cli.AUDIO_READOUT_S <= 0.35


def test_a_snapshot_readout_is_never_held(monkeypatch):
    monkeypatch.setattr(cli, "SMOOTHING", False)
    cli._readouts.clear()
    cli.readout("y", 10.0, now=100.0)
    assert cli.readout("y", 99.0, now=100.0) == 99.0


def test_the_hud_keeps_the_raw_sample_while_the_headline_eases(monkeypatch, played):
    """Both numbers are on screen: the HUD is the measurement, the big one is the movement."""
    an, now = played
    monkeypatch.setattr(cli, "SMOOTHING", True)
    cli._smoother.reset()
    cli._readouts.clear()
    an.db = -20.0
    render("db", an, now, 120, 36)                  # seed the easing at -20
    an.db = -55.0
    out = render("db", an, now + cli.AUDIO_READOUT_S * 2, 120, 36)
    assert f"{audio.spl(-55.0):.1f}" in out, "the HUD must show what was measured"
    digits = "".join(l for l in out.splitlines() if set(l.strip(" │|")) <= {cli.GLYPHS.bar_full, " "})
    assert digits.strip(), "and the big number is drawn"


# --- nothing on the level screen reads as a negative number ---------------------------------

def negative_numbers(text):
    """Negative numbers that claim to be a LEVEL.

    The chart's x axis is labelled in seconds-ago ("-8s", "now"), which is a time, not a
    reading - forbidding every minus sign would forbid the axis too.
    """
    import re
    return [m.group(0) for m in re.finditer(r"-\d+\.?\d*(?!\s*s\b)", text)
            if not text[m.end():m.end() + 1] == "s"]


@pytest.mark.parametrize("mode", cli.AUDIO_MODES)
def test_no_audio_screen_shows_a_negative_level(mode, played):
    an, now = played
    out = render(mode, an, now, 120, 36)
    assert not negative_numbers(out), f"{mode} still shows a negative number: {negative_numbers(out)}"


def test_the_level_screen_shows_the_positive_scale(played):
    an, now = played
    out = render("db", an, now, 120, 36)
    assert f"{audio.spl(an.db):.1f}" in out
    assert f"{audio.spl(an.db_min):.1f}" in out and f"{audio.spl(an.db_max):.1f}" in out
    assert "dB" in out


def test_the_hud_shows_the_positive_scale(played):
    an, now = played
    hud = plain(cli.audio_hud(an, now, 120), width=120)
    assert f"{audio.spl(an.db):.1f}" in hud
    assert "-" not in hud.replace("---", "")


def test_the_level_chart_axis_is_positive(played):
    an, now = played
    out = render("db", an, now, 120, 36)
    floor = f"{audio.spl(audio.DB_FLOOR):.1f}"
    assert floor in out or floor.rstrip("0").rstrip(".") in out


def test_a_quiet_room_still_reads_above_zero():
    an = audio.Analyzer()
    an.feed(np.zeros(audio.BLOCK, dtype=np.float32), 0.0)
    out = render("db", an, 0.0, 120, 36)
    assert not negative_numbers(out)


def test_the_chart_gets_the_history_on_the_shown_scale(played):
    """The big number is drawn in GLYPHS and the chart is plotted, so neither shows up in a
    text search - the two places the scale could silently revert need their own pins."""
    an, _ = played
    shifted = cli.level_history(an)
    assert shifted and all(v > 0 for _, v in shifted)
    assert [v for _, v in shifted] == [audio.spl(v) for _, v in an.db_history]


def test_the_headline_digits_are_the_shown_value(played):
    an, now = played
    text = body_text("db", an, now, 120, 36)
    expected = cli.big_digits(f"{audio.spl(cli.readout('audio.db', cli.shown_level(an.db), now)):.1f}")
    drawn = [l.strip() for l in text.splitlines() if set(l) <= {cli.GLYPHS.bar_full, " "} and l.strip()]
    assert [row.strip() for row in expected] == drawn[:len(expected)], (expected, drawn[:5])


@pytest.mark.parametrize("freq", [40.0, 100.0, 1000.0, 10000.0, 16000.0])
def test_every_axis_label_lands_in_a_real_band(freq, played):
    """The band lookup must CLAMP at both ends.

    40 Hz and 16 kHz are the outermost band edges, and whether `edges[0] <= 40.0` holds is
    a matter of float noise in whatever numpy built the edges: on one machine it is exactly
    40.0, on another 40.000000000000007. Without clamping the search finds nothing, falls
    back to the last band, and "40" lands on top of "16k" - which is exactly how the axis
    lost its labels on Linux while passing on macOS.
    """
    an, _ = played
    k = cli._band_of(an.spectrum.edges, freq, len(an.levels))
    assert 0 <= k < len(an.levels)
    if freq == 40.0:
        assert k == 0
    if freq == 16000.0:
        assert k == len(an.levels) - 1


def test_the_axis_survives_edges_that_miss_the_ends_by_a_hair(played):
    an, now = played
    nudged = [e + 1e-9 for e in an.spectrum.edges]          # what another numpy hands back
    original = an.spectrum.edges
    try:
        an.spectrum.edges = nudged
        out = render("eq", an, now, 120, 36)
        assert "40" in out and "16k" in out and "1k" in out
    finally:
        an.spectrum.edges = original
