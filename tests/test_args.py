"""Argument parsing.

Regression cover for the 1.1.2 defect: the parser matched only the exact spellings and
silently ignored everything else, so `termstats -live` ran a *snapshot* — which shows
"Collecting data..." in both history panels and reads as a broken live dashboard.
"""

import sys

import pytest

from termstats import cli, __version__


@pytest.fixture
def run(monkeypatch):
    """Call main() with argv and report which mode it entered."""
    seen = {}

    monkeypatch.setattr(cli, "run_live", lambda interval=1.0: seen.update(mode="live", interval=interval))
    monkeypatch.setattr(cli, "run_once", lambda: seen.update(mode="once"))

    def _run(*args):
        monkeypatch.setattr(sys, "argv", ["termstats", *args])
        cli.main()
        return seen

    _run.seen = seen
    return _run


# --- live mode -------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["-l", "--live", "-live"])
def test_every_live_spelling_enters_live_mode(run, flag):
    assert run(flag)["mode"] == "live"


def test_no_arguments_is_a_snapshot(run):
    assert run()["mode"] == "once"


def test_live_defaults_to_one_second(run):
    assert run("-l")["interval"] == 1.0


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
    documented = set(cli._HELP_FLAGS + cli._VERSION_FLAGS + cli._LIVE_FLAGS + cli._INTERVAL_FLAGS)
    for spelling in ("-h", "--help", "-help", "-V", "--version", "-version",
                     "-l", "--live", "-live", "-i", "--interval", "-interval"):
        assert spelling in documented
