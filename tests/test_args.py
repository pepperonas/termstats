"""Argument parsing and mode selection.

Two behaviours are pinned here.

Historical: the parser once matched only the exact spellings and silently ignored
everything else, so `termstats -live` ran a *snapshot* — which shows "Collecting data..."
in both history panels and reads as a broken live dashboard.

Current: a bare `termstats` runs the live dashboard, but only when stdout is a terminal.
Piped or redirected it must produce one snapshot and exit — otherwise `termstats > out.txt`
and every CI step that runs the command would hang forever.
"""

import sys

import pytest

from termstats import cli, __version__


@pytest.fixture
def run(monkeypatch):
    """Call main() with argv and report which mode it entered.

    `tty=` fakes the answer to "is a human watching?" - under pytest stdout is captured,
    so the real isatty() would always say no and the default-mode tests would all pass
    for the wrong reason.
    """
    seen = {}

    monkeypatch.setattr(
        cli, "run_live",
        lambda interval=cli.DEFAULT_INTERVAL: seen.update(mode="live", interval=interval),
    )
    monkeypatch.setattr(cli, "run_once", lambda: seen.update(mode="once"))

    def _run(*args, tty=True):
        monkeypatch.setattr(cli, "_stdout_is_interactive", lambda: tty)
        monkeypatch.setattr(sys, "argv", ["termstats", *args])
        cli.main()
        return seen

    _run.seen = seen
    return _run


# --- live mode -------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["-l", "--live", "-live"])
def test_every_live_spelling_enters_live_mode(run, flag):
    assert run(flag)["mode"] == "live"


def test_no_arguments_in_a_terminal_is_live(run):
    """The headline behaviour: `termstats` updates by itself."""
    assert run(tty=True)["mode"] == "live"


def test_no_arguments_when_piped_is_a_snapshot(run):
    """`termstats > out.txt` must terminate. A live loop there never would."""
    assert run(tty=False)["mode"] == "once"


def test_live_uses_the_default_interval(run):
    assert run("-l")["interval"] == cli.DEFAULT_INTERVAL


def test_the_default_interval_is_faster_than_a_second():
    assert 0 < cli.DEFAULT_INTERVAL < 1.0


# --- snapshot mode ---------------------------------------------------------------

@pytest.mark.parametrize("flag", ["-1", "--once", "-once"])
def test_every_once_spelling_forces_a_snapshot(run, flag):
    assert run(flag, tty=True)["mode"] == "once"


@pytest.mark.parametrize("flag", ["-l", "--live", "-live"])
def test_live_is_forced_even_when_piped(run, flag):
    """An explicit --live outranks the terminal check - for `termstats -l | tee log`."""
    assert run(flag, tty=False)["mode"] == "live"


@pytest.mark.parametrize("args", [("--live", "--once"), ("--once", "--live"),
                                  ("-1", "-l"), ("-l", "-i", "2", "-1")])
def test_live_and_once_together_are_rejected(run, capsys, args):
    """Silently picking one would make the other flag a lie."""
    with pytest.raises(SystemExit) as excinfo:
        run(*args)
    assert excinfo.value.code == 2
    assert "mutually exclusive" in capsys.readouterr().err
    assert run.seen == {}


@pytest.mark.parametrize("flag", ["-l", "--live", "-1", "--once"])
def test_repeating_the_same_mode_flag_is_harmless(run, flag):
    """Only *conflicting* modes are an error; `-l -l` is merely redundant."""
    assert run(flag, flag)["mode"] in {"live", "once"}


def test_once_ignores_the_interval_but_still_validates_it(run, capsys):
    """--interval is the live refresh rate; a snapshot always samples for one second.

    Accepting a value we then ignore is fine - accepting a *bad* value silently is not.
    """
    assert run("--once", "-i", "5")["mode"] == "once"
    with pytest.raises(SystemExit):
        run("--once", "-i", "-4")
    assert "positive" in capsys.readouterr().err


# --- interval --------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["-i", "--interval", "-interval"])
def test_every_interval_spelling_is_accepted(run, flag):
    assert run("-l", flag, "3")["interval"] == 3.0


def test_interval_accepts_fractions(run):
    assert run("-l", "-i", "2.5")["interval"] == 2.5


def test_interval_value_is_consumed_not_reparsed(run):
    """The value must not be examined as an option of its own.

    With the value left in the stream, "3" would be an unknown option and the whole
    invocation would die — the reason parsing is an index loop and not enumerate().
    """
    assert run("-l", "-i", "3")["mode"] == "live"


def test_flag_order_does_not_matter(run):
    assert run("-i", "4", "-l") == {"mode": "live", "interval": 4.0}


# --- rejected input --------------------------------------------------------------

def _expect_exit_2(run, *args):
    with pytest.raises(SystemExit) as excinfo:
        run(*args)
    assert excinfo.value.code == 2
    return excinfo


@pytest.mark.parametrize("bad", ["--lvie", "-x", "--", "foo", "-L", "--LIVE"])
def test_unknown_options_are_an_error_not_a_shrug(run, capsys, bad):
    _expect_exit_2(run, bad)
    err = capsys.readouterr().err
    assert "unknown option" in err
    assert bad in err


def test_unknown_option_never_falls_back_to_a_snapshot(run):
    """The whole point of 1.1.2: a typo must not quietly produce a snapshot."""
    with pytest.raises(SystemExit):
        run("--lvie")
    assert run.seen == {}, "neither run_once nor run_live may fire on a bad option"


def test_a_bad_interval_aborts_before_running_anything(run):
    with pytest.raises(SystemExit):
        run("-l", "-i", "abc")
    assert run.seen == {}


def test_interval_without_a_value_is_rejected(run, capsys):
    _expect_exit_2(run, "-l", "-i")
    assert "needs an interval" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["abc", "", "1e", "3,5"])
def test_non_numeric_interval_is_rejected(run, capsys, bad):
    _expect_exit_2(run, "-l", "-i", bad)
    assert "needs a number" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["0", "-3", "-0.0"])
def test_non_positive_interval_is_rejected(run, capsys, bad):
    _expect_exit_2(run, "-l", "-i", bad)
    assert "positive" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
def test_non_finite_interval_is_rejected(run, capsys, bad):
    """float() happily parses these; sleep(nan) raises and sleep(inf) never returns."""
    _expect_exit_2(run, "-l", "-i", bad)
    assert "finite" in capsys.readouterr().err


def test_errors_go_to_stderr_and_leave_stdout_clean(run, capsys):
    _expect_exit_2(run, "-x")
    out = capsys.readouterr()
    assert out.out == ""
    assert "Try 'termstats --help'" in out.err


# --- help and version ------------------------------------------------------------

@pytest.mark.parametrize("flag", ["-h", "--help", "-help"])
def test_help_exits_zero_and_prints_usage(run, capsys, flag):
    with pytest.raises(SystemExit) as excinfo:
        run(flag)
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "Usage: termstats" in out
    assert "--live" in out and "--interval" in out


def test_help_documents_the_single_dash_long_forms(run, capsys):
    with pytest.raises(SystemExit):
        run("--help")
    assert "-live" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["-V", "--version", "-version"])
def test_version_exits_zero_with_exactly_the_version(run, capsys, flag):
    with pytest.raises(SystemExit) as excinfo:
        run(flag)
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"termstats {__version__}"


def test_help_wins_over_a_bad_option(run, capsys):
    """Asking for help must never be answered with a parse error."""
    with pytest.raises(SystemExit) as excinfo:
        run("--bogus", "--help")
    assert excinfo.value.code == 0
    assert "Usage: termstats" in capsys.readouterr().out


def test_help_wins_over_version(run, capsys):
    with pytest.raises(SystemExit):
        run("--version", "--help")
    assert "Usage: termstats" in capsys.readouterr().out


def test_every_documented_flag_tuple_is_reachable():
    """A flag that is in no _*_FLAGS tuple is now a hard error - keep them in sync."""
    documented = set(cli._HELP_FLAGS + cli._VERSION_FLAGS + cli._LIVE_FLAGS
                     + cli._ONCE_FLAGS + cli._INTERVAL_FLAGS)
    for spelling in ("-h", "--help", "-help", "-V", "--version", "-version",
                     "-l", "--live", "-live", "-1", "--once", "-once",
                     "-i", "--interval", "-interval"):
        assert spelling in documented


def test_no_flag_spelling_is_claimed_by_two_options():
    """A spelling in two tuples would make the first `elif` win at random."""
    tuples = (cli._HELP_FLAGS, cli._VERSION_FLAGS, cli._LIVE_FLAGS,
              cli._ONCE_FLAGS, cli._INTERVAL_FLAGS)
    flat = [flag for group in tuples for flag in group]
    assert len(flat) == len(set(flat)), "duplicate flag spelling across option groups"


def test_help_documents_the_new_defaults(run, capsys):
    with pytest.raises(SystemExit):
        run("--help")
    out = capsys.readouterr().out
    assert "--once" in out
    assert "not a terminal" in out, "the piped-means-snapshot rule must be discoverable"


def test_stdout_is_interactive_survives_a_stream_without_isatty(monkeypatch):
    """Some embedded/captured stdouts have no isatty at all; that means 'not a human'."""
    monkeypatch.setattr(sys, "stdout", object())
    assert cli._stdout_is_interactive() is False
