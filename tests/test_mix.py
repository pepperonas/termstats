"""-x / the x key: the level and the tempo on one screen, side by side or stacked."""
import re
import sys

import pytest

np = pytest.importorskip("numpy")

from rich.cells import cell_len  # noqa: E402

from termstats import audio, cli  # noqa: E402
from termstats import theme as T  # noqa: E402
from helpers import plain  # noqa: E402

WIDE = (120, 36)
STACK = (80, 24)


def music(seconds=8.0, bpm=126.0):
    d = audio.DemoAudio(seed=7, bpm=bpm)
    an = audio.Analyzer(audio.SAMPLE_RATE, audio.BLOCK)
    while d.now() < seconds:
        an.feed(d.read(audio.BLOCK), d.now())
    return an, d


@pytest.fixture(scope="module")
def played():
    an, d = music()
    return an, d.now()


def render(an, now, w, h, mode="mix"):
    return plain(cli.render_audio(mode, an, now, w, h), width=w, height=h)


def lines_of(an, now, w, h):
    return render(an, now, w, h).splitlines()


def row_of(lines, needle):
    hits = [i for i, line in enumerate(lines) if needle in line]
    assert len(hits) == 1, (needle, hits)
    return hits[0]


LEVEL_METER = re.compile(r"\d\.\ddB")          # the level meter's value field, "80.4dB"


def row_matching(lines, pattern):
    hits = [i for i, line in enumerate(lines) if pattern.search(line)]
    assert len(hits) == 1, (pattern.pattern, hits)
    return hits[0]


# --- the key and the flags ---------------------------------------------------------------

@pytest.mark.parametrize("data", [b"x", b"X", b"ax"])
def test_x_selects_the_combined_view(data):
    assert cli.view_key(data) == "mix"


def test_the_combined_view_is_an_audio_mode_the_loop_can_switch_to():
    assert "mix" in cli.AUDIO_MODES
    assert "mix" in cli._AUDIO_BODIES and "mix" in cli._AUDIO_TITLES


def test_the_watcher_hands_the_combined_view_to_the_loop(monkeypatch):
    class FakeTTY:
        def isatty(self):
            return True

        def fileno(self):
            return 0

    monkeypatch.setattr(cli.sys, "stdin", FakeTTY())
    monkeypatch.setattr(cli, "_set_cbreak", lambda fd: "SAVED")
    monkeypatch.setattr(cli, "_restore_tty", lambda fd, saved: None)
    monkeypatch.setattr(cli, "_read_ready", lambda fd: b"x")
    w = cli.KeyWatcher()
    w.start()
    assert w.quit_pressed() is False
    assert w.view_pressed() == "mix"
    w.stop()


@pytest.fixture
def invoke(monkeypatch):
    calls = {}
    monkeypatch.setattr(cli, "run_audio",
                        lambda mode, interval, source, once=False: calls.update(mode=mode, once=once))
    monkeypatch.setattr(cli, "run_live", lambda interval: calls.update(live=interval))
    monkeypatch.setattr(cli, "run_once", lambda: calls.update(snapshot=True))
    monkeypatch.setattr(cli, "_stdout_is_interactive", lambda: True)
    monkeypatch.setattr(cli, "_mic_source", lambda device: object())

    def go(*argv):
        monkeypatch.setattr(sys, "argv", ["termstats", *argv])
        cli.main()
        return calls
    return go


@pytest.mark.parametrize("flag", cli._MIX_FLAGS)
def test_every_spelling_of_the_flag_starts_the_combined_screen(invoke, flag):
    assert invoke(flag, "--demo")["mode"] == "mix"


def test_the_combined_screen_counts_as_one_audio_mode(invoke, capsys):
    with pytest.raises(SystemExit) as exc:
        invoke("-db", "-x")
    assert exc.value.code == 2
    assert "-x" in capsys.readouterr().err


def test_a_snapshot_of_the_combined_screen_is_a_short_measurement(invoke):
    assert invoke("-x", "--once", "--demo")["once"] is True


def test_help_names_the_flag_and_the_key(capsys):
    cli.print_help()
    text = capsys.readouterr().out
    assert "-x, --x, --mix" in text
    assert "x both" in text


# --- the header ---------------------------------------------------------------------------

def test_the_badge_names_both_quantities(played):
    an, now = played
    assert " DB+BPM " in lines_of(an, now, *WIDE)[0]


def test_the_panel_names_both_quantities(played):
    an, now = played
    assert "level & tempo" in lines_of(an, now, *WIDE)[1]


# --- wide: two columns -------------------------------------------------------------------

def test_a_wide_terminal_puts_the_level_left_and_the_tempo_right(played):
    an, now = played
    w, h = WIDE
    lines = lines_of(an, now, w, h)
    heading = lines[2]
    assert heading.index("level") < w // 2 < heading.index("tempo")


def test_the_column_rule_is_the_minimum_column_width():
    assert cli.mix_columns(2 * cli.MIX_MIN_COLUMN_W + cli.MIX_GAP) == cli.MIX_MIN_COLUMN_W
    assert cli.mix_columns(2 * cli.MIX_MIN_COLUMN_W + cli.MIX_GAP - 1) is None


def big_rows(text):
    return cli.big_digits(text)


def test_both_headline_numbers_share_the_same_rows(played):
    an, now = played
    w, h = WIDE
    lines = lines_of(an, now, w, h)
    level = big_rows(f"{cli._shown_db(an.db):.1f}")
    tempo = big_rows(f"{an.bpm:.0f}")
    half = w // 2
    starts = [i for i in range(len(lines) - 5)
              if all(level[r] in lines[i + r][:half] for r in range(5))]
    assert len(starts) == 1, starts
    k = starts[0]
    assert all(tempo[r] in lines[k + r][half:] for r in range(5)), "the tempo digits are not on the level's rows"


def test_the_meters_and_the_extremes_pair_up_across_the_columns(played):
    an, now = played
    lines = lines_of(an, now, *WIDE)
    assert row_matching(lines, LEVEL_METER) == row_of(lines, "confidence")
    assert row_of(lines, "min ") == row_of(lines, "kick band")


def test_both_charts_start_on_the_same_row(played):
    an, now = played
    lines = lines_of(an, now, *WIDE)
    sep = T.GLYPH_SETS["braille"].sep
    assert row_of(lines, f"level {sep} last") == row_of(lines, f"tempo {sep} last")


def test_the_columns_draw_no_hud_because_everything_is_already_there(played):
    an, now = played
    lines = lines_of(an, now, *WIDE)
    assert not any("conf " in line for line in lines)              # the HUD's confidence bar
    assert any("confidence" in line for line in lines)             # ...is a full meter instead


def test_the_beat_dot_lives_in_the_tempo_heading(played):
    an, now = played
    lines = lines_of(an, now, *WIDE)
    glyphs = T.GLYPH_SETS["braille"]
    heading = lines[2]
    assert glyphs.beat_off in heading or glyphs.beat_on in heading
    assert heading.index("tempo") - 2 == max(heading.rfind(glyphs.beat_off), heading.rfind(glyphs.beat_on))


# --- narrow: a stack --------------------------------------------------------------------------

def test_a_narrow_terminal_stacks_the_level_above_the_tempo(played):
    an, now = played
    lines = lines_of(an, now, *STACK)
    hud = lines[2]
    assert "BPM" in hud and " dB" in hud                            # the shared HUD is back
    assert row_of(lines, "min ") < row_of(lines, "confidence")
    assert not any("level" in line and "tempo" in line for line in lines[2:4])


def test_a_short_stack_shows_both_one_line_readouts(played):
    an, now = played
    lines = lines_of(an, now, 80, 12)
    assert any(re.search(r"\d+\.\d dB   \S   smoothed", line) for line in lines)
    assert any(re.search(r"\d+ BPM   \S   \d", line) for line in lines)


def test_a_tall_stack_draws_both_numbers_big(played):
    an, now = played
    lines = lines_of(an, now, 80, 40)
    assert any(re.match(r"^\W*dB   \S   smoothed", line.strip("│ ")) for line in lines)
    assert any(re.match(r"^\W*BPM   \S   \d", line.strip("│ ")) for line in lines)


def test_a_tall_stack_gives_both_charts_room(played):
    an, now = played
    lines = lines_of(an, now, 80, 56)
    sep = T.GLYPH_SETS["braille"].sep
    assert row_of(lines, f"level {sep} last") < row_of(lines, f"tempo {sep} last")


def test_a_low_stack_keeps_the_level_chart_only(played):
    an, now = played
    lines = lines_of(an, now, *STACK)
    sep = T.GLYPH_SETS["braille"].sep
    assert any(f"level {sep} last" in line for line in lines)
    assert not any(f"tempo {sep} last" in line for line in lines)


def test_the_tightest_stack_spends_the_gap_before_the_kick_band(played):
    an, now = played
    lines = lines_of(an, now, 80, 11)                                # 7 body rows
    assert any("kick band" in line for line in lines)


# --- every size, every glyph level ---------------------------------------------------------------

@pytest.mark.parametrize("w,h", [(200, 50), (140, 42), (120, 36), (100, 30), (90, 14), (80, 24),
                                 (80, 12), (60, 20), (44, 15), (40, 8)])
def test_the_combined_screen_fits_every_terminal(w, h, played):
    an, now = played
    lines = lines_of(an, now, w, h)
    assert len(lines) <= h
    assert max(cell_len(line) for line in lines) <= w


def test_the_ascii_combined_screen_draws_only_seven_bit(played):
    an, now = played
    cli.CAPS = T.Capabilities(color="truecolor", glyphs="ascii", nerd=False)
    cli.set_glyph_level("ascii")
    text = render(an, now, *WIDE)
    assert all(ord(ch) < 128 for ch in text), {ch for ch in text if ord(ch) > 127}
    assert "level" in text and "tempo" in text


# --- still and live ------------------------------------------------------------------------------

def test_a_snapshot_of_the_combined_screen_stands_still(played):
    an, now = played
    body = lambda: render(an, now, *WIDE).splitlines()[1:]     # the header carries the live process count
    assert body() == body()


def test_the_single_screens_render_as_before_the_refactor(played):
    """db_body and bpm_body are now composed from the blocks the combined screen uses;
    each still shows the HUD first, then its number, its meters and its chart."""
    an, now = played
    sep = T.GLYPH_SETS["braille"].sep
    db = render(an, now, *WIDE, mode="db").splitlines()
    assert "conf " in db[2] and row_of(db, "min ") < row_of(db, f"level {sep} last")
    bpm = render(an, now, *WIDE, mode="bpm").splitlines()
    assert "conf " in bpm[2] and row_of(bpm, "kick band") < row_of(bpm, f"tempo {sep} last")


def test_live_the_metronome_does_not_push_the_meters_apart(monkeypatch):
    monkeypatch.setattr(cli, "SMOOTHING", True)
    monkeypatch.setattr(cli, "LIVE", True)
    cli._get_motion().reset()
    cli._chart_cache.clear()
    an, d = music()
    w, h = WIDE
    for _ in range(12):
        for _ in range(2):
            an.feed(d.read(audio.BLOCK), d.now())
        cli.render_audio("mix", an, d.now(), w, h)
    cli.wait_for_chart_workers()
    lines = lines_of(an, d.now(), w, h)
    glyphs = T.GLYPH_SETS["braille"]
    assert any(glyphs.metro_head in line for line in lines), "no metronome in a live frame with a tempo"
    assert row_matching(lines, LEVEL_METER) == row_of(lines, "confidence")
    assert row_of(lines, "min ") == row_of(lines, "kick band")
    cli.wait_for_chart_workers()


def test_the_live_footer_offers_the_x_key(monkeypatch):
    monkeypatch.setattr(cli, "LIVE", True)
    assert "x both" in plain(cli.footer_line(120), width=120)
