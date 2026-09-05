"""Esc (or q) ends a live session - without swallowing the Esc that starts an arrow key."""
import os
import sys

import pytest

from termstats import cli


# --- which bytes mean "quit" -----------------------------------------------------------

@pytest.mark.parametrize("data,expected", [
    (b"\x1b", True),            # Esc on its own
    (b"\x1b\x1b", True),        # Esc twice, still Esc
    (b"q", True),
    (b"Q", True),
    (b"\x1b[A", False),         # arrow up - Esc is only the PREFIX of a key sequence
    (b"\x1b[B", False),
    (b"\x1bOP", False),         # F1 in application mode
    (b"\x1b[1;5A", False),      # Ctrl+arrow
    (b"a", False),
    (b"", False),
    (b"\n", False),
])
def test_is_quit_key(data, expected):
    assert cli.is_quit_key(data) is expected


def test_a_quit_key_anywhere_in_a_burst_counts():
    assert cli.is_quit_key(b"aaq") is True


# --- the watcher ------------------------------------------------------------------------

class FakeTTY:
    def __init__(self, data=b"", tty=True):
        self.data, self._tty = data, tty

    def isatty(self):
        return self._tty

    def fileno(self):
        return 0


def test_the_watcher_stays_inactive_without_a_terminal(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", FakeTTY(tty=False))
    w = cli.KeyWatcher()
    w.start()
    assert not w.active
    assert w.quit_pressed() is False
    w.stop()                      # must not raise


def test_the_watcher_stays_inactive_when_stdin_has_no_descriptor(monkeypatch):
    class NoFd:
        def isatty(self):
            return True

        def fileno(self):
            raise OSError("not a real stream")

    monkeypatch.setattr(cli.sys, "stdin", NoFd())
    w = cli.KeyWatcher()
    w.start()
    assert not w.active


def test_the_watcher_puts_the_terminal_back(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.sys, "stdin", FakeTTY())
    monkeypatch.setattr(cli, "_set_cbreak", lambda fd: calls.append(("cbreak", fd)) or "SAVED")
    monkeypatch.setattr(cli, "_restore_tty", lambda fd, saved: calls.append(("restore", fd, saved)))
    w = cli.KeyWatcher()
    w.start()
    assert w.active and calls == [("cbreak", 0)]
    w.stop()
    assert calls[-1] == ("restore", 0, "SAVED")
    w.stop()                      # idempotent: no second restore
    assert len(calls) == 2


def test_the_watcher_reads_what_is_waiting(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", FakeTTY())
    monkeypatch.setattr(cli, "_set_cbreak", lambda fd: "SAVED")
    monkeypatch.setattr(cli, "_restore_tty", lambda fd, saved: None)
    monkeypatch.setattr(cli, "_read_ready", lambda fd: b"\x1b")
    w = cli.KeyWatcher()
    w.start()
    assert w.quit_pressed() is True
    monkeypatch.setattr(cli, "_read_ready", lambda fd: b"\x1b[A")
    assert w.quit_pressed() is False
    w.stop()


def test_a_closed_terminal_does_not_crash_the_watcher(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", FakeTTY())
    monkeypatch.setattr(cli, "_set_cbreak", lambda fd: "SAVED")
    monkeypatch.setattr(cli, "_restore_tty", lambda fd, saved: None)

    def boom(fd):
        raise OSError("terminal went away")

    monkeypatch.setattr(cli, "_read_ready", boom)
    w = cli.KeyWatcher()
    w.start()
    assert w.quit_pressed() is False
    w.stop()


# --- the wait loop notices ----------------------------------------------------------------

def test_the_wait_is_cut_short_by_a_quit_key(monkeypatch):
    monkeypatch.setattr(cli._keys, "quit_pressed", lambda: True)
    cli._quit.clear()
    assert cli._sleep_until(cli.time.monotonic() + 5) is True
    assert cli._quit.is_set()


def test_a_quiet_keyboard_does_not_cut_the_wait(monkeypatch):
    monkeypatch.setattr(cli._keys, "quit_pressed", lambda: False)
    cli._quit.clear()
    assert cli._sleep_until(cli.time.monotonic() + 0.15) is False
    assert not cli._quit.is_set()


# --- the loops leave ------------------------------------------------------------------------

def _fake_live(monkeypatch, frames, limit=3):
    """A Live that gives up after `limit` frames.

    Without the limit, a loop whose exit condition is broken spins forever and the test
    HANGS instead of failing - which is exactly what happened when this pin was first
    mutation-probed: pytest never returned and the mutant stayed in the working tree.
    """
    class Live:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def update(self, *a, **kw):
            frames.append(1)
            if len(frames) > limit:
                raise KeyboardInterrupt("the loop never left")

    monkeypatch.setattr(cli, "Live", Live)


def test_esc_ends_the_dashboard_without_an_error(monkeypatch):
    frames = []
    _fake_live(monkeypatch, frames)
    monkeypatch.setattr(cli, "_prime_measurements", lambda: None)
    monkeypatch.setattr(cli, "render_dashboard", lambda *a, **kw: "frame")
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)

    def sleep_until(deadline):
        cli._quit.set()
        return True

    monkeypatch.setattr(cli, "_sleep_until", sleep_until)
    cli._quit.clear()
    cli.run_live(0.5)                                  # returns instead of looping
    assert len(frames) <= 1, "no frame is drawn after the quit key"


def test_esc_ends_an_audio_screen(monkeypatch):
    pytest.importorskip("numpy")
    from termstats import audio
    frames = []
    _fake_live(monkeypatch, frames)
    monkeypatch.setattr(cli, "render_audio", lambda *a, **kw: "frame")

    def sleep_until(deadline):
        cli._quit.set()
        return True

    monkeypatch.setattr(cli, "_sleep_until", sleep_until)
    cli._quit.clear()
    cli.run_audio("db", 0.05, audio.DemoAudio(seed=1), once=False)
    assert len(frames) <= 1


def test_the_quit_flag_is_cleared_when_a_session_starts(monkeypatch):
    monkeypatch.setattr(cli, "_prime_measurements", lambda: None)
    monkeypatch.setattr(cli, "render_dashboard", lambda *a, **kw: "frame")
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli, "_sleep_until", lambda d: (_ for _ in ()).throw(KeyboardInterrupt()))
    _fake_live(monkeypatch, [])
    cli._quit.set()                                   # a stale flag from an earlier session
    cli.run_live(0.5)
    assert not cli._quit.is_set()


# --- it is announced ---------------------------------------------------------------------------

def test_the_footer_names_esc_in_live_mode(monkeypatch):
    monkeypatch.setattr(cli, "LIVE", True)
    from helpers import plain
    assert "Esc" in plain(cli.footer_line(120), width=120)


def test_the_footer_stays_quiet_in_a_snapshot(monkeypatch):
    monkeypatch.setattr(cli, "LIVE", False)
    from helpers import plain
    assert "Esc" not in plain(cli.footer_line(120), width=120)


def test_help_and_readme_mention_esc(capsys):
    from pathlib import Path
    cli.print_help()
    assert "Esc" in capsys.readouterr().out
    assert "Esc" in (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
