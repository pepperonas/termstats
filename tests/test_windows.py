"""Windows: the terminal says nothing in the environment, so detection must ask elsewhere.

Windows Terminal exports neither TERM nor COLORTERM (microsoft/terminal#11057 is still
open), only WT_SESSION. A plain conhost exports nothing at all, but has drawn 24-bit colour
since Windows 10 1703 (build 15063). Before 0.4.1 both landed on the 16-colour rung.
"""
import io
import sys
from pathlib import Path

import pytest

from termstats import cli
from termstats import theme as T

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")


def utf8_stream():
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8")


# --- WT_SESSION ------------------------------------------------------------------------

@pytest.mark.parametrize("env", [
    {"WT_SESSION": "3b1c9e2a-0000-4000-8000-000000000000"},
    {"WT_SESSION": "x", "TERM": ""},
    {"WT_SESSION": "x", "TERM": "xterm"},            # Git Bash inside Windows Terminal
    {"WT_SESSION": "x", "TERM": "xterm-256color"},   # tmux/WSL inside Windows Terminal
])
def test_windows_terminal_is_truecolor_through_wt_session(env):
    assert T.detect(env, stream=utf8_stream()).color == "truecolor"


@pytest.mark.parametrize("env", [
    {"WT_SESSION": "x", "NO_COLOR": "1"},
    {"WT_SESSION": "x", "TERM": "dumb"},
])
def test_no_color_and_dumb_still_win_over_wt_session(env):
    assert T.detect(env, stream=utf8_stream()).color == "mono"


def test_an_empty_wt_session_is_not_a_windows_terminal(monkeypatch):
    monkeypatch.setattr(T, "_windows_build", lambda: None)   # a Unix host: nothing else speaks
    assert T.detect({"WT_SESSION": "", "TERM": ""}, stream=utf8_stream()).color == "16"
    assert T._color_from_env({"WT_SESSION": "", "TERM": ""}, windows_build=22631) == "truecolor"


# --- plain conhost: nothing in the environment, so the build number decides ------------

@pytest.mark.parametrize("build,expected", [
    (15063, "truecolor"),   # Windows 10 1703: conhost learned 24-bit colour
    (22631, "truecolor"),   # Windows 11
    (15062, "16"),
    (9600, "16"),           # Windows 8.1
    (None, "16"),           # not Windows at all
])
def test_a_silent_environment_falls_back_to_the_windows_build(build, expected):
    assert T._color_from_env({"TERM": "", "COLORTERM": ""}, windows_build=build) == expected


def test_a_term_that_speaks_is_not_second_guessed_by_the_build():
    # TERM=xterm without COLORTERM is 16 colours on every platform; Windows is no exception.
    assert T._color_from_env({"TERM": "xterm"}, windows_build=22631) == "16"
    assert T._color_from_env({"TERM": "xterm-256color"}, windows_build=22631) == "256"


def test_detect_consults_the_build_helper(monkeypatch):
    monkeypatch.setattr(T, "_windows_build", lambda: 22631)
    assert T.detect({"TERM": "", "COLORTERM": ""}, stream=utf8_stream()).color == "truecolor"
    monkeypatch.setattr(T, "_windows_build", lambda: 9600)
    assert T.detect({"TERM": "", "COLORTERM": ""}, stream=utf8_stream()).color == "16"


def test_build_helper_reads_getwindowsversion_when_present(monkeypatch):
    class Version:
        build = 19045

    monkeypatch.setattr(sys, "getwindowsversion", lambda: Version(), raising=False)
    assert T._windows_build() == 19045


def test_build_helper_is_none_where_there_is_no_windows_version(monkeypatch):
    monkeypatch.delattr(sys, "getwindowsversion", raising=False)
    assert T._windows_build() is None


def test_build_helper_survives_a_broken_getwindowsversion(monkeypatch):
    def boom():
        raise OSError("no console")

    monkeypatch.setattr(sys, "getwindowsversion", boom, raising=False)
    assert T._windows_build() is None


# --- the emulated load average needs a head start --------------------------------------

def test_priming_starts_the_load_average_sampler(monkeypatch):
    # psutil emulates getloadavg() on Windows with a sampler thread that reports 0.00 for
    # its first five seconds; asking during priming starts that clock as early as possible.
    calls = []
    monkeypatch.setattr(cli.psutil, "getloadavg", lambda: calls.append(1) or (0.0, 0.0, 0.0))
    cli._prime_measurements()
    assert calls, "priming never touched getloadavg()"


def test_priming_survives_a_platform_without_getloadavg(monkeypatch):
    def missing():
        raise OSError("Load averages are unobtainable")

    monkeypatch.setattr(cli.psutil, "getloadavg", missing)
    cli._prime_measurements()   # must not raise


# --- the docs and the CI keep the PowerShell path honest --------------------------------

def test_readme_documents_wt_session():
    assert "WT_SESSION" in README


def test_readme_no_longer_blames_python_312_for_the_load_average():
    assert "requires Python 3.12" not in README


def test_readme_names_the_powershell_alias():
    assert "Set-Alias ts termstats" in README


def test_readme_explains_powershell_redirection():
    assert "OutputEncoding" in README


def test_ci_runs_a_powershell_smoke_test():
    assert "shell: pwsh" in WORKFLOW
