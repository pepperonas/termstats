"""S4 - meter quality: the peak hairline, the cache segment's contrast, the track tone.

A meter shows one instant. The hairline at the recent maximum is the other half of the
story, and it is the one moving element here that carries data: it holds where the
value went and sinks back as old samples leave the window.
"""

from types import SimpleNamespace

import pytest

from termstats import cli
from termstats import theme as T
from helpers import plain


def cells(text):
    return plain(text, width=200).rstrip("\n")


# --- the tracker --------------------------------------------------------------------------

def test_peak_is_the_maximum_of_the_window():
    tr = cli.PeakTracker(window=5)
    for v in (10, 80, 30, 20, 25):
        last = tr.value("k", v)
    assert last == 80


def test_peak_decays_when_the_spike_leaves_the_window():
    """This is the animation: not a timer, the data itself ageing out."""
    tr = cli.PeakTracker(window=4)
    tr.value("k", 90)
    seen = [tr.value("k", 10) for _ in range(5)]
    assert seen[:3] == [90, 90, 90] and seen[3] == 10 and seen[4] == 10


def test_peak_tracks_keys_separately():
    tr = cli.PeakTracker(window=5)
    tr.value("a", 90); tr.value("b", 5)
    assert tr.value("a", 1) == 90 and tr.value("b", 1) == 5


def test_peak_forgets_keys_that_stopped_being_drawn():
    tr = cli.PeakTracker(window=5)
    tr.value("proc.1", 50); tr.value("proc.2", 50)
    tr.end_frame()
    tr.value("proc.1", 50)
    tr.end_frame()
    assert "proc.2" not in tr._hist and "proc.1" in tr._hist


def test_peak_window_matches_the_token():
    assert cli.PeakTracker().window == T.PEAK_WINDOW == 30


# --- the hairline in the bar ----------------------------------------------------------------

def test_hairline_is_drawn_at_the_peak_when_it_lies_beyond_the_fill():
    drawn = cells(cli.bar(30.0, 20, peak=80.0))
    assert drawn.count(cli.GLYPHS.peak) == 1
    assert drawn.index(cli.GLYPHS.peak) == 16          # int(20 * 80 / 100)
    assert len(drawn) == 20


def test_no_hairline_when_the_peak_is_inside_the_fill():
    assert cli.GLYPHS.peak not in cells(cli.bar(80.0, 20, peak=30.0))
    assert cli.GLYPHS.peak not in cells(cli.bar(80.0, 20, peak=80.0))


def test_no_hairline_without_a_peak():
    assert cli.GLYPHS.peak not in cells(cli.bar(30.0, 20))
    assert cli.GLYPHS.peak not in cells(cli.bar(30.0, 20, peak=float("nan")))


@pytest.mark.parametrize("peak", [99.9, 100.0, 250.0])
def test_a_peak_at_or_over_the_top_stays_inside_the_bar(peak):
    drawn = cells(cli.bar(30.0, 20, peak=peak))
    assert len(drawn) == 20
    assert drawn.index(cli.GLYPHS.peak) == 19


def test_hairline_sits_on_the_track_not_on_the_cache_segment():
    drawn = cells(cli.bar(30.0, 20, secondary=30.0, peak=50.0))
    assert cli.GLYPHS.peak not in drawn, "the peak lies under the cache segment"
    drawn = cells(cli.bar(30.0, 20, secondary=30.0, peak=90.0))
    assert drawn.index(cli.GLYPHS.peak) == 18


def test_hairline_takes_the_ramp_colour_of_its_position():
    styled = cli.bar(10.0, 20, peak=90.0)
    span = 19
    for sp in styled.spans:
        if styled.plain[sp.start] == cli.GLYPHS.peak:
            assert str(sp.style) == cli.ramp(18 / span)
            break
    else:
        pytest.fail("no hairline span")


def test_hairline_has_an_ascii_form(ascii_mode):
    drawn = cells(cli.bar(30.0, 20, peak=80.0))
    assert drawn.isascii() and "|" in drawn


def test_meter_passes_the_peak_through():
    assert cli.GLYPHS.peak in cells(cli.meter("cpu0", 20.0, 50, peak=90.0))


# --- integration: the marker follows a spike and sinks back ---------------------------------

@pytest.fixture
def cpu_script(monkeypatch):
    """Per-core CPU that spikes once, then idles."""
    script = {"values": [], "i": 0}

    def cpu_percent(percpu=False):
        v = script["values"][min(script["i"], len(script["values"]) - 1)]
        return [v] * 2 if percpu else v

    monkeypatch.setattr(cli.psutil, "cpu_percent", cpu_percent)
    monkeypatch.setattr(cli.psutil, "cpu_count", lambda: 2)

    def play(values):
        script["values"] = values
        frames = []
        for i in range(len(values)):
            script["i"] = i
            body, _, _ = cli.get_cpu_section(60)
            frames.append(plain(body, width=60))
            cli._peaks.end_frame()
        return frames

    return play


def test_a_spike_leaves_a_hairline_that_outlives_it(cpu_script):
    frames = cpu_script([5.0, 95.0] + [5.0] * 10)
    assert cli.GLYPHS.peak not in frames[0]
    assert cli.GLYPHS.peak not in frames[1], "at the spike itself the fill covers the peak"
    assert all(cli.GLYPHS.peak in f for f in frames[2:]), "the marker must persist after the spike"


def test_the_hairline_sinks_back_after_the_window(cpu_script):
    frames = cpu_script([95.0] + [5.0] * (T.PEAK_WINDOW + 3))
    assert cli.GLYPHS.peak in frames[T.PEAK_WINDOW - 1]
    assert cli.GLYPHS.peak not in frames[T.PEAK_WINDOW + 2]


def test_a_snapshot_has_no_hairline(monkeypatch):
    """One sample: the peak IS the value, so there is nothing beyond the fill to mark."""
    monkeypatch.setattr(cli.psutil, "cpu_percent", lambda percpu=False: [40.0] * 2 if percpu else 40.0)
    monkeypatch.setattr(cli.psutil, "cpu_count", lambda: 2)
    body, _, _ = cli.get_cpu_section(60)
    assert cli.GLYPHS.peak not in plain(body, width=60)


# --- the cache segment's contrast ------------------------------------------------------------

def test_dimming_keeps_the_hue():
    """Scaling sRGB channels drifts amber towards olive; OKLab dimming does not."""
    amber = T.rgb_of("#c0922c")
    _, a, b = T.rgb_to_oklab(amber)
    _, a2, b2 = T.rgb_to_oklab(T.rgb_of(T.dim_hex(amber)))
    import math
    assert abs(math.atan2(b, a) - math.atan2(b2, a2)) < 0.08, "hue drifted under dimming"


def test_dimming_lowers_lightness_by_the_factor():
    amber = T.rgb_of("#c0922c")
    assert T.lightness(T.rgb_of(T.dim_hex(amber))) == pytest.approx(T.lightness(amber) * T.DIM_FACTOR, abs=0.02)


@pytest.mark.parametrize("name", T.theme_names())
def test_cache_segment_is_clearly_darker_than_the_bar_in_every_theme(name):
    ramp = T.Ramp(T.resolve_theme(name).stops)
    for t in (0.2, 0.5, 0.8):
        primary = ramp.rgb(t)
        secondary = T.rgb_of(T.dim_hex(primary))
        assert T.lightness(primary) - T.lightness(secondary) >= 0.18, f"{name} at {t}"


# --- the empty track ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", T.theme_names())
def test_track_is_its_own_tone_between_background_and_labels(name):
    """Visible but quiet: lighter than the background the theme is designed for, darker
    than the dim label tone, so the empty part of a bar reads as a rail, not as nothing."""
    theme = T.resolve_theme(name)
    bg, track, dim = (T.lightness(T.rgb_of(c)) for c in (theme.bg, theme.track, theme.dim))
    assert bg < track < dim, f"{name}: bg {bg:.2f} track {track:.2f} dim {dim:.2f}"


def test_the_empty_track_is_drawn_in_the_track_tone():
    styled = cli.bar(20.0, 20)
    styles = {str(sp.style) for sp in styled.spans if styled.plain[sp.start] == cli.BAR_EMPTY}
    assert styles == {cli.TRACK}


def test_the_hairline_marks_the_raw_peak_not_the_eased_fill(monkeypatch):
    """With smoothing on, the fill after a 5 -> 95 spike is only halfway there; the
    hairline must stand at 95, which is what the bar is reaching toward. Fed from the
    eased value it would sit at 50 and say nothing the fill does not."""
    monkeypatch.setattr(cli.psutil, "cpu_count", lambda: 1)
    values = iter([[5.0], [95.0]])
    monkeypatch.setattr(cli.psutil, "cpu_percent", lambda percpu=False: next(values) if percpu else 0.0)
    cli.SMOOTHING = True
    cli._smoother.reset(); cli._peaks.reset()
    cli.get_cpu_section(60); cli._smoother.end_frame(); cli._peaks.end_frame()
    body, _, _ = cli.get_cpu_section(60)
    row = plain(body, width=60).split("\n")[0]
    bar_start = row.index(cli.BAR_FULL)
    bar_w = sum(row.count(ch) for ch in (cli.BAR_FULL, cli.BAR_EMPTY, cli.GLYPHS.peak) + tuple(cli.BAR_PARTIALS))
    peak_cell = row.index(cli.GLYPHS.peak) - bar_start
    assert peak_cell >= int(bar_w * 0.95) - 1, f"hairline at cell {peak_cell} of {bar_w}: fed from the eased fill"
