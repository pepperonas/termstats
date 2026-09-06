"""The microphone screens MOVE in live mode - and stand still in a snapshot."""
import pytest

np = pytest.importorskip("numpy")

from rich.color import Color  # noqa: E402
from rich.console import Console  # noqa: E402

from termstats import audio, cli  # noqa: E402
from helpers import plain  # noqa: E402

W, H = 120, 36


def body(mode, an, now):
    ch, cw = cli.chrome()
    return plain(cli._AUDIO_BODIES[mode](an, now, W - cw, H - ch - 2), width=W - cw, height=H)


def bar_cells(text):
    return sum(l.count(cli.GLYPHS.bar_full) for l in text.splitlines()
               if set(l.strip()) <= {cli.GLYPHS.bar_full, cli.GLYPHS.bar_secondary, cli.GLYPHS.vpeak, " "} and l.strip())


def advance(mode, an, t0, t1, fps=30):
    """Render every frame from t0 to t1, the way a live session does. Jumping the clock in one
    step would hit the motion layer's MAX_DT clamp - the guard against a suspended laptop -
    and prove nothing about a fade."""
    n = int(round((t1 - t0) * fps))
    for i in range(1, n + 1):
        cli.render_audio(mode, an, t0 + i / fps, W, H)
    return t0 + n / fps


def quiet_analyzer(level=0.0):
    an = audio.Analyzer()
    an.levels = [level] * audio.BANDS
    an.peaks = list(an.levels)
    return an


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(cli, "SMOOTHING", True)
    monkeypatch.setattr(cli, "LIVE", True)
    cli._get_motion().reset()
    cli._chart_cache.clear()


# --- the frame rate ---------------------------------------------------------------------

def test_audio_screens_run_at_thirty_frames_a_second():
    assert cli.AUDIO_INTERVAL == pytest.approx(1 / 30, abs=1e-6)
    assert cli.AUDIO_INTERVAL < cli.AUDIO_READOUT_S


# --- equalizer ------------------------------------------------------------------------------

def test_live_bars_ease_towards_a_jump_instead_of_taking_it(live):
    an = quiet_analyzer(0.0)
    cli.render_audio("eq", an, 0.0, W, H)             # seed: everything at zero
    an.levels = [1.0] * audio.BANDS; an.peaks = list(an.levels)
    first = bar_cells(body("eq", an, 1 / 30))
    settled = bar_cells(body("eq", an, 0.6))
    assert 0 < first < settled, (first, settled)


def test_a_falling_bar_leaves_a_trail_in_live_mode(live):
    an = quiet_analyzer(1.0)
    cli.render_audio("eq", an, 0.0, W, H)
    cli.render_audio("eq", an, 0.3, W, H)
    an.levels = [0.0] * audio.BANDS; an.peaks = list(an.levels)
    text = body("eq", an, 0.45)
    assert cli.GLYPHS.bar_secondary in text, "the afterglow above a fallen bar"


def test_a_snapshot_never_draws_a_trail(monkeypatch):
    monkeypatch.setattr(cli, "SMOOTHING", False)
    an = quiet_analyzer(1.0)
    cli.render_audio("eq", an, 0.0, W, H)
    an.levels = [0.0] * audio.BANDS; an.peaks = list(an.levels)
    assert cli.GLYPHS.bar_secondary not in body("eq", an, 0.45)


def test_a_snapshot_stands_perfectly_still(monkeypatch):
    monkeypatch.setattr(cli, "SMOOTHING", False)
    an = quiet_analyzer(0.6)
    an.last_beat, an.beats = 0.0, 3
    first, later = body("eq", an, 100.0), body("eq", an, 105.0)
    assert first == later


def test_the_beat_nudges_every_bar_up_in_live_mode(live):
    an = quiet_analyzer(0.5)
    for i in range(30):
        cli.render_audio("eq", an, i / 30, W, H)
    calm = bar_cells(body("eq", an, 1.0))
    an.beats, an.last_beat = 1, 1.0
    punched = bar_cells(body("eq", an, 1.0 + 1 / 30))
    assert punched > calm


# --- the beat dot fades -----------------------------------------------------------------

def dot_colour(an, now):
    console = Console(width=W, color_system="truecolor", force_terminal=True, no_color=False, legacy_windows=False)
    for seg in console.render(cli.audio_hud(an, now, W)):
        if seg.text.strip() in (cli.GLYPHS.beat_on, cli.GLYPHS.beat_off) and seg.style and seg.style.color:
            return seg.text.strip(), seg.style.color.get_truecolor()
    return None, None


def test_the_beat_dot_fades_instead_of_switching_off(live):
    an = quiet_analyzer(0.5)
    cli.render_audio("bpm", an, 0.0, W, H)
    an.beats, an.last_beat = 1, 1.0
    cli.render_audio("bpm", an, 1.0, W, H)
    g0, c0 = dot_colour(an, 1.0)
    t = advance("bpm", an, 1.0, 1.3)
    g1, c1 = dot_colour(an, t)
    t = advance("bpm", an, t, 2.5)
    g2, _ = dot_colour(an, t)
    assert g0 == cli.GLYPHS.beat_on and g1 == cli.GLYPHS.beat_on and g2 == cli.GLYPHS.beat_off
    assert c0 != c1, "the dot is dimmer a third of a second later, not the same then gone"


# --- tempo: a metronome -----------------------------------------------------------------

def test_the_tempo_screen_sweeps_a_metronome_between_beats(live):
    an = quiet_analyzer(0.3)
    an.bpm_history.append((0.0, 120.0)); an.tempo.display_bpm = 120.0
    an.beats, an.last_beat = 8, 10.0
    early = body("bpm", an, 10.05)
    mid = body("bpm", an, 10.25)
    rows_e = [l for l in early.splitlines() if cli.GLYPHS.metro_head in l]
    rows_m = [l for l in mid.splitlines() if cli.GLYPHS.metro_head in l]
    assert rows_e and rows_m, "a metronome row with a head glyph"
    assert rows_m[0].index(cli.GLYPHS.metro_head) > rows_e[0].index(cli.GLYPHS.metro_head), "the head moves right"


def test_no_metronome_without_a_tempo_or_in_a_snapshot(live, monkeypatch):
    an = quiet_analyzer(0.3)                            # bpm 0
    assert cli.GLYPHS.metro_head not in body("bpm", an, 1.0)
    monkeypatch.setattr(cli, "SMOOTHING", False)
    an.tempo.display_bpm = 120.0; an.beats, an.last_beat = 8, 0.0
    assert cli.GLYPHS.metro_head not in body("bpm", an, 0.25)


def test_tempo_digits_fade_after_the_beat(live):
    an = quiet_analyzer(0.3); an.tempo.display_bpm = 120.0
    an.beats, an.last_beat = 1, 1.0
    cli.render_audio("bpm", an, 1.0, W, H)
    hot = cli.tempo_tone(an, 1.0)
    t = advance("bpm", an, 1.0, 1.3)
    warm = cli.tempo_tone(an, t)
    t = advance("bpm", an, t, 3.0)
    cool = cli.tempo_tone(an, t)
    assert hot == cli.ramp(1.0) and cool == cli.ramp(0.75)
    assert warm not in (hot, cool), "somewhere between, not a switch"


# --- level: ballistics and a cached chart -----------------------------------------------------

def meter_fill(text):
    row = next(l for l in text.splitlines() if "level" in l and cli.GLYPHS.bar_empty in l)
    return row.count(cli.GLYPHS.bar_full)


def test_the_level_meter_falls_like_a_needle_not_a_switch(live):
    an = quiet_analyzer(0.2); an.db = audio.DB_FLOOR
    cli.render_audio("db", an, 0.0, W, H)
    an.db = -20.0
    for i in range(1, 16):
        cli.render_audio("db", an, i / 30, W, H)
    risen = meter_fill(body("db", an, 0.5))
    an.db = audio.DB_FLOOR
    one_frame_later = meter_fill(body("db", an, 0.5 + 1 / 30))
    assert one_frame_later > risen * 0.6, (risen, one_frame_later)


def played_analyzer(seconds=6.0):
    an = audio.Analyzer(); src = audio.DemoAudio(seed=3)
    while src.now() < seconds:
        an.feed(src.read(audio.BLOCK), src.now())
    return an, src.now()


def test_the_live_chart_is_built_off_the_render_path(live, monkeypatch):
    """plotext needs ~37 ms for one chart - more than a whole 33 ms frame. Live, the chart
    is built in a worker; the frame draws the last finished one and never waits."""
    import time
    real = cli._audio_chart
    def slow(*a, **k):
        time.sleep(0.05)
        return real(*a, **k)
    monkeypatch.setattr(cli, "_audio_chart", slow)
    an, t0 = played_analyzer()
    cli.render_audio("db", an, t0, W, H)                        # the first frame builds once, synchronously
    cli.wait_for_chart_workers()
    took = []
    for i in range(1, 6):
        s = time.perf_counter(); cli.render_audio("db", an, t0 + i / 30, W, H); took.append(time.perf_counter() - s)
    assert max(took) < 0.03, f"a frame waited for the chart: {[round(t*1000) for t in took]} ms"
    s = time.perf_counter(); cli.render_audio("db", an, t0 + cli.CHART_REFRESH_S + 0.05, W, H)
    assert time.perf_counter() - s < 0.03, "the refresh frame must hand the build to a worker, not do it"
    cli.wait_for_chart_workers()


def test_the_worker_refreshes_the_chart_on_its_own_clock(live, monkeypatch):
    calls = []
    real = cli._render_chart
    monkeypatch.setattr(cli, "_render_chart", lambda *a, **k: calls.append(1) or real(*a, **k))
    an, t0 = played_analyzer()
    for i in range(10):
        cli.render_audio("db", an, t0 + i / 30, W, H)          # a third of a second: inside one window
    cli.wait_for_chart_workers()
    assert len(calls) == 1, f"{len(calls)} builds inside one refresh window"
    cli.render_audio("db", an, t0 + cli.CHART_REFRESH_S + 0.05, W, H)
    cli.wait_for_chart_workers()
    assert len(calls) == 2


def test_a_failing_chart_build_keeps_the_last_chart(live, monkeypatch):
    an, t0 = played_analyzer()
    before = plain(cli.render_audio("db", an, t0, W, H), width=W, height=H)
    cli.wait_for_chart_workers()
    assert "last " in before
    def boom(*a, **k):
        raise RuntimeError("plotext had a bad day")
    monkeypatch.setattr(cli, "_audio_chart", boom)
    after = plain(cli.render_audio("db", an, t0 + cli.CHART_REFRESH_S + 0.05, W, H), width=W, height=H)
    cli.wait_for_chart_workers()
    again = plain(cli.render_audio("db", an, t0 + cli.CHART_REFRESH_S + 0.1, W, H), width=W, height=H)
    assert "last " in after and "last " in again, "the old chart stays up when a rebuild fails"


def test_the_history_snapshot_is_taken_on_the_render_thread(live, monkeypatch):
    """The audio thread appends to `db_history` while a worker would iterate it - a deque
    mutated during iteration raises. So the render thread takes the list and the worker only
    ever sees that finished list."""
    import threading
    threads, kinds = [], []
    real_hist, real_chart = cli.level_history, cli._audio_chart
    def spy_hist(an):
        threads.append(threading.current_thread() is threading.main_thread())
        return real_hist(an)
    def spy_chart(title, history, *a, **k):
        kinds.append(type(history))
        return real_chart(title, history, *a, **k)
    monkeypatch.setattr(cli, "level_history", spy_hist)
    monkeypatch.setattr(cli, "_audio_chart", spy_chart)
    an, t0 = played_analyzer()
    cli.render_audio("db", an, t0, W, H); cli.wait_for_chart_workers()
    cli.render_audio("db", an, t0 + cli.CHART_REFRESH_S + 0.05, W, H); cli.wait_for_chart_workers()
    assert threads and all(threads), "level_history ran on a worker"
    assert kinds and all(k is list for k in kinds)


def test_a_snapshot_builds_its_chart_every_time(monkeypatch):
    monkeypatch.setattr(cli, "SMOOTHING", False)
    calls = []
    real = cli._render_chart
    monkeypatch.setattr(cli, "_render_chart", lambda *a, **k: calls.append(1) or real(*a, **k))
    an, t0 = played_analyzer()
    cli.render_audio("db", an, t0, W, H); cli.render_audio("db", an, t0 + 0.01, W, H)
    assert len(calls) == 2


def test_the_envelope_reaches_zero_so_the_calm_colour_is_exact():
    from termstats import motion
    m = motion.Motion()
    class Fake:
        levels = [0.0] * audio.BANDS; peaks = levels; beats = 1; last_beat = 0.0; bpm = 0; db = -30.0
    m.update(Fake(), 0.0)
    for i in range(1, 91):
        m.update(Fake(), i / 30)
    assert m.beat_env == 0.0
