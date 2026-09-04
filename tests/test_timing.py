"""Refresh cadence, the history window label, and what the two run modes actually do.

Three things went from "constant" to "computed" when live became the default:
the chart title (a 60-sample buffer is not 60 seconds at 0.5 s), the sleep between
frames (a flat sleep drifts by the render time), and rich's refresh rate.
"""

import pytest

from termstats import cli
from helpers import plain


# --- how much time the history actually covers ------------------------------------

@pytest.mark.parametrize("interval, expected", [
    (0.5, "last 30s"),      # the default: 60 samples x 0.5 s
    (1.0, "last 60s"),      # the old hard-coded value, now merely one case
    (0.25, "last 15s"),
    (2.0, "last 2m"),
    (3.0, "last 3m"),
    (1.5, "last 1.5m"),     # 90 s - the switch-over point
    (10.0, "last 10m"),
])
def test_window_label_follows_the_interval(interval, expected):
    assert cli._window_label(interval) == expected


def test_window_label_reads_the_live_interval_when_not_told_one():
    cli.sample_interval = 2.0
    assert cli._window_label() == "last 2m"


def test_window_label_never_prints_a_trailing_zero():
    """"last 2.0m" would look like a rounding artefact."""
    for interval in (2.0, 4.0, 6.0, 10.0):
        assert ".0m" not in cli._window_label(interval)


def test_the_window_label_reaches_the_panel_title(primed_history):
    """The bug this guards: both charts claimed "last 60s" whatever the interval was.

    The label now lives in the panel header rather than inside the plot, where plotext
    used to draw it on top of the data.
    """
    cli.sample_interval = 0.5
    out = plain(cli.render_dashboard(140, 50), width=140, height=50)
    assert "last 30s" in out
    assert "last 60s" not in out


def test_a_slower_interval_relabels_the_panel(primed_history):
    cli.sample_interval = 3.0
    assert "last 3m" in plain(cli.render_dashboard(140, 50), width=140, height=50)


def test_history_length_is_a_sample_count_not_a_duration():
    assert cli.HISTORY_LEN == 60
    assert cli._window_label(1.0) == f"last {cli.HISTORY_LEN}s"


# --- fixed-cadence scheduling -----------------------------------------------------

def test_schedule_tick_targets_a_fixed_grid():
    """Back-to-back ticks land exactly one interval apart, not interval + render time."""
    next_tick, delay = cli._schedule_tick(100.0, now=100.0, interval=0.5)
    assert (next_tick, delay) == (100.5, 0.5)


def test_schedule_tick_subtracts_the_time_the_render_took():
    """0.2 s spent rendering must shorten the sleep, or the period drifts to 0.7 s."""
    next_tick, delay = cli._schedule_tick(100.0, now=100.2, interval=0.5)
    assert next_tick == 100.5
    assert delay == pytest.approx(0.3)


def test_schedule_tick_resyncs_after_an_overrun_instead_of_catching_up():
    """A render that took longer than the interval must not bank a backlog.

    Without the resync the schedule stays in the past and every following frame sleeps
    zero seconds until it has "caught up" - a burst of instant redraws.
    """
    next_tick, delay = cli._schedule_tick(100.0, now=103.0, interval=0.5)
    assert next_tick == 103.5
    assert delay == 0.5


def test_schedule_tick_never_returns_a_negative_sleep():
    for now in (100.0, 100.5, 101.0, 250.0):
        _, delay = cli._schedule_tick(100.0, now=now, interval=0.5)
        assert delay > 0


def test_schedule_tick_is_stable_over_many_frames():
    """Simulate 20 frames that each take 120 ms and check the grid does not slip."""
    interval, render_cost = 0.5, 0.12
    now = 1000.0
    next_tick = now
    for _ in range(20):
        next_tick, delay = cli._schedule_tick(next_tick, now, interval)
        now += delay + render_cost
    assert now == pytest.approx(1000.0 + 20 * interval + render_cost, abs=1e-9)


# --- the two run modes ------------------------------------------------------------

class FakeLive:
    """Stands in for rich.live.Live and records how it was configured."""

    last = None

    def __init__(self, renderable, console=None, refresh_per_second=None, screen=None):
        FakeLive.last = self
        self.refresh_per_second = refresh_per_second
        self.screen = screen
        self.updates = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, renderable, refresh=False):
        self.updates.append(refresh)


RENDER_COST = 0.03


class Sleeps(list):
    """The recorded sleeps, with the fake clock and the render timestamps alongside."""
    clock = None
    renders = None


@pytest.fixture
def live_harness(monkeypatch):
    """Run run_live() for a bounded number of frames, then interrupt it.

    The clock is faked and *advanced by the sleeps*. A frozen clock would make this
    harness lie: the scheduler would keep pushing next_tick into a future that never
    arrives and the delays would grow 0.5, 1.0, 1.5 - which is correct behaviour for a
    stopped clock and tells us nothing about the real loop.
    """
    clock = {"t": 1000.0}
    renders = []
    sleeps = Sleeps()
    sleeps.clock = clock
    sleeps.renders = renders

    def fake_render():
        renders.append(clock["t"])
        return "<dashboard>"

    def fake_monotonic():
        return clock["t"]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds + RENDER_COST      # the frame costs time too
        if len(sleeps) > 4:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "Live", FakeLive)
    monkeypatch.setattr(cli, "_prime_measurements", lambda: None)
    monkeypatch.setattr(cli, "render_dashboard", fake_render)
    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    monkeypatch.setattr(cli.time, "monotonic", fake_monotonic)
    FakeLive.last = None
    return sleeps


def test_run_live_publishes_the_interval_the_charts_label_themselves_with(live_harness):
    cli.run_live(2.0)
    assert cli.sample_interval == 2.0


def test_run_live_swallows_ctrl_c(live_harness):
    """The alternate screen has to be torn down cleanly, not by a traceback."""
    cli.run_live(0.5)   # the harness raises KeyboardInterrupt; no exception may escape


def test_run_live_forces_a_repaint_on_every_frame(live_harness):
    cli.run_live(0.5)
    assert FakeLive.last.updates, "no frames were drawn"
    assert all(FakeLive.last.updates), "update() must pass refresh=True"


def test_run_live_uses_the_alternate_screen(live_harness):
    cli.run_live(0.5)
    assert FakeLive.last.screen is True


@pytest.mark.parametrize("interval, refresh", [(0.5, 2), (1.0, 1), (0.1, 10), (5.0, 1)])
def test_refresh_rate_tracks_the_interval(live_harness, interval, refresh):
    """A 1 fps Live would smear a 0.5 s interval; an unbounded one would burn CPU."""
    cli.run_live(interval)
    assert FakeLive.last.refresh_per_second == refresh


def test_run_live_subtracts_the_render_cost_from_the_sleep(live_harness):
    """The whole point of the scheduler: sleep interval - render time, not interval.

    A flat sleep would show up here as a full 0.5 s on every frame. The first frame is
    the exception and legitimately sleeps the full interval: the schedule is anchored
    *after* the opening render, so at that point nothing has been spent yet.
    """
    cli.run_live(0.5)
    priming, first_frame, *steady = live_harness
    assert priming == 0.5
    assert first_frame == pytest.approx(0.5)
    assert steady, "not enough frames to observe the steady state"
    for delay in steady:
        assert delay == pytest.approx(0.5 - RENDER_COST)


def test_run_live_draws_frames_exactly_one_interval_apart(live_harness):
    """The property that matters, measured on the clock rather than on the sleeps.

    A flat sleep would put these 0.53 s apart - a 6% drift, plainly visible at this
    cadence and worse the shorter the interval.
    """
    cli.run_live(0.5)
    frames = live_harness.renders[1:]        # [0] is the render that opens Live()
    assert len(frames) >= 3
    gaps = [b - a for a, b in zip(frames, frames[1:])]
    for gap in gaps:
        assert gap == pytest.approx(0.5)


def test_run_once_samples_for_a_full_second_regardless_of_interval(monkeypatch):
    """Rates need a gap; --interval is the live refresh rate, not a sampling window."""
    slept = []
    printed = []
    monkeypatch.setattr(cli, "_prime_measurements", lambda: None)
    monkeypatch.setattr(cli, "render_dashboard", lambda: "<dashboard>")
    monkeypatch.setattr(cli.time, "sleep", slept.append)
    monkeypatch.setattr(cli.console, "print", printed.append)

    cli.run_once()

    assert slept == [cli.SNAPSHOT_SAMPLE_S]
    assert printed == ["<dashboard>"]
    assert cli.sample_interval == cli.SNAPSHOT_SAMPLE_S
