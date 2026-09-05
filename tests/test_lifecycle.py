"""S8 - lifecycle: a resize relayouts at once, the cursor always comes back, Ctrl+C is
quiet, and none of it depends on a signal that Windows does not have.
"""

import signal
import sys

import pytest

from termstats import cli

HAS_SIGWINCH = hasattr(signal, "SIGWINCH")


class FakeLive:
    """Stands in for rich.live.Live: records update() calls, never touches the terminal."""
    updates = []
    entered = 0

    def __init__(self, renderable=None, **kwargs):
        FakeLive.updates = []
        self.kwargs = kwargs

    def __enter__(self):
        FakeLive.entered += 1
        return self

    def __exit__(self, *exc):
        return False

    def update(self, renderable, refresh=False):
        FakeLive.updates.append(refresh)


@pytest.fixture
def quiet_live(monkeypatch):
    """run_live without psutil, sleeping or a terminal; the loop ends on the first tick."""
    monkeypatch.setattr(cli, "Live", FakeLive)
    monkeypatch.setattr(cli, "_prime_measurements", lambda: None)
    monkeypatch.setattr(cli, "render_dashboard", lambda *a, **k: "frame")
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    cursor = []
    monkeypatch.setattr(cli.console, "show_cursor", lambda show=True: cursor.append(show))
    return cursor


# --- SIGWINCH ------------------------------------------------------------------------------

def test_the_resize_handler_is_guarded_where_the_signal_does_not_exist(monkeypatch):
    monkeypatch.delattr(cli.signal, "SIGWINCH", raising=False)
    assert cli._install_resize_handler() is None
    cli._restore_resize_handler(None)          # must not raise either


@pytest.mark.skipif(not HAS_SIGWINCH, reason="no SIGWINCH on this platform")
def test_the_resize_handler_is_installed_and_put_back():
    before = signal.getsignal(signal.SIGWINCH)
    previous = cli._install_resize_handler()
    try:
        assert signal.getsignal(signal.SIGWINCH) is cli._on_resize
    finally:
        cli._restore_resize_handler(previous)
    assert signal.getsignal(signal.SIGWINCH) == before


def test_a_resize_raises_the_flag_and_the_sleep_returns_at_once(monkeypatch):
    monkeypatch.setattr(cli.time, "sleep", lambda s: pytest.fail("slept although resized"))
    cli._on_resize(28, None)
    assert cli._resized.is_set()
    assert cli._sleep_until(cli.time.monotonic() + 5) is True
    assert not cli._resized.is_set(), "the flag must be consumed, or every tick would relayout"


def test_the_sleep_runs_in_slices_so_a_resize_is_seen_quickly(monkeypatch):
    clock = {"t": 100.0}
    slept = []

    def fake_sleep(s):
        slept.append(s)
        clock["t"] += s

    monkeypatch.setattr(cli.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    assert cli._sleep_until(100.0 + 0.35) is False
    assert slept and max(slept) <= cli.RESIZE_SLICE_S
    assert abs(sum(slept) - 0.35) < 1e-9, "the slices must add up to the whole wait"


def test_a_resize_relayouts_without_waiting_for_the_tick(quiet_live, monkeypatch):
    answers = iter([True, KeyboardInterrupt()])

    def fake_sleep_until(deadline):
        a = next(answers)
        if isinstance(a, BaseException):
            raise a
        return a

    monkeypatch.setattr(cli, "_sleep_until", fake_sleep_until)
    cli.run_live(0.5)
    assert FakeLive.updates == [True], "the resize must produce exactly one immediate frame"


@pytest.mark.skipif(not HAS_SIGWINCH, reason="no SIGWINCH on this platform")
def test_run_live_leaves_the_signal_handler_as_it_found_it(quiet_live, monkeypatch):
    monkeypatch.setattr(cli, "_sleep_until", lambda d: (_ for _ in ()).throw(KeyboardInterrupt()))
    before = signal.getsignal(signal.SIGWINCH)
    cli.run_live(0.5)
    assert signal.getsignal(signal.SIGWINCH) == before


# --- cursor and Ctrl+C ---------------------------------------------------------------------

def test_ctrl_c_during_priming_is_quiet_and_restores_the_cursor(quiet_live, monkeypatch):
    def interrupted():
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_prime_measurements", interrupted)
    entered = FakeLive.entered
    try:
        assert cli.run_live(0.5) is None
    except KeyboardInterrupt:          # pytest would abort the whole session on this
        pytest.fail("Ctrl+C during priming escaped run_live - that is a traceback for the user")
    assert FakeLive.entered == entered, "the alternate screen must not have been entered"
    assert quiet_live == [False, True]


def test_the_cursor_is_hidden_before_the_priming_pause(quiet_live, monkeypatch):
    order = []
    monkeypatch.setattr(cli, "_prime_measurements", lambda: order.append("prime"))
    monkeypatch.setattr(cli.console, "show_cursor", lambda show=True: order.append(("cursor", show)))
    monkeypatch.setattr(cli, "_sleep_until", lambda d: (_ for _ in ()).throw(KeyboardInterrupt()))
    cli.run_live(0.5)
    assert order[:2] == [("cursor", False), "prime"]
    assert order[-1] == ("cursor", True)


def test_the_cursor_comes_back_even_when_a_render_raises(quiet_live, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("collector exploded")

    monkeypatch.setattr(cli, "render_dashboard", boom)
    with pytest.raises(RuntimeError):
        cli.run_live(0.5)
    assert quiet_live[-1] is True


def test_ctrl_c_in_live_mode_exits_zero(quiet_live, monkeypatch):
    """No traceback, no 130: leaving the dashboard is the normal way out."""
    monkeypatch.setattr(cli, "_sleep_until", lambda d: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(cli, "_stdout_is_interactive", lambda: True)
    monkeypatch.setattr(sys, "argv", ["termstats", "--live"])
    assert cli.main() is None


def test_ctrl_c_in_snapshot_mode_exits_130_without_a_traceback(monkeypatch):
    """An interrupted snapshot is no snapshot: quiet, but not a success for the shell."""
    monkeypatch.setattr(cli, "_prime_measurements", lambda: None)
    monkeypatch.setattr(cli.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(sys, "argv", ["termstats", "--once"])
    try:
        with pytest.raises(SystemExit) as exc:
            cli.main()
    except KeyboardInterrupt:
        pytest.fail("Ctrl+C in snapshot mode escaped main() - a traceback for the user")
    assert exc.value.code == 130


def test_live_uses_the_alternate_screen(quiet_live, monkeypatch):
    monkeypatch.setattr(cli, "_sleep_until", lambda d: (_ for _ in ()).throw(KeyboardInterrupt()))
    seen = {}
    real_init = FakeLive.__init__

    def spy(self, renderable=None, **kwargs):
        seen.update(kwargs)
        seen["first_frame"] = renderable
        real_init(self, renderable, **kwargs)

    monkeypatch.setattr(FakeLive, "__init__", spy)
    cli.run_live(0.5)
    assert seen["screen"] is True
    assert seen["first_frame"] == "frame", "the first frame must enter WITH the screen, not after it"
