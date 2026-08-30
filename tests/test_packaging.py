"""Packaging and identity.

Two facts about this project are easy to break silently and expensive to get wrong:
the version lives in two files, and the rename from `stats` must stay done.
"""

import re
from pathlib import Path

import pytest

import termstats
from termstats import cli

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def pyproject_value(key):
    match = re.search(rf'^{key}\s*=\s*"([^"]+)"', PYPROJECT, re.M)
    assert match, f"{key} not found in pyproject.toml"
    return match.group(1)


# --- version ----------------------------------------------------------------------

def test_version_is_the_same_in_both_places():
    """The header and --version read __init__.py; packaging reads pyproject.toml."""
    assert termstats.__version__ == pyproject_value("version")


def test_version_looks_like_a_release():
    assert re.fullmatch(r"\d+\.\d+\.\d+", termstats.__version__)


def test_cli_reports_the_package_version():
    assert cli.__version__ == termstats.__version__


# --- dependency pins --------------------------------------------------------------

def test_plotext_is_capped_below_six():
    """6.0.0 removed the 5.x API the charts are built on - see test_charts.py."""
    assert re.search(r'"plotext>=[\d.]+,<6"', PYPROJECT)


def test_every_imported_third_party_package_is_declared():
    declared = set(re.findall(r'"([a-z0-9_-]+)[><=]', PYPROJECT))
    assert {"psutil", "plotext", "rich"} <= declared


# --- the rename stays done --------------------------------------------------------

def test_the_console_script_is_termstats():
    assert re.search(r'^termstats\s*=\s*"termstats\.cli:main"', PYPROJECT, re.M)


def test_there_is_no_stats_alias():
    """A `stats` command would restore exactly the ambiguity the rename removed."""
    scripts = PYPROJECT.split("[project.scripts]", 1)[1]
    assert not re.search(r'^\s*stats\s*=', scripts, re.M)


def test_distribution_name_is_termstats():
    assert pyproject_value("name") == "termstats"


def test_package_directory_matches_the_distribution():
    assert (ROOT / "termstats" / "cli.py").is_file()
    assert not (ROOT / "stats").exists()


# --- documentation does not promise things that do not exist ----------------------

@pytest.mark.parametrize("phantom", ["termstats", "stats-dashboard"])
def test_readme_does_not_claim_a_pypi_release(phantom):
    """Neither name has ever been uploaded; the old README led with an install that
    could not work for anybody. Install docs point at the git URL."""
    assert not re.search(rf"pip install\s+(?:--user\s+)?['\"]?{re.escape(phantom)}['\"]?\s*$",
                         README, re.M)


def test_readme_has_no_pypi_badge():
    assert "pypi" not in README.lower() or "not on PyPI" in README


def test_readme_documents_the_current_version():
    assert termstats.__version__ in README


def test_readme_version_badge_matches_the_package():
    badge = re.search(r"shields\.io/badge/version-([\d.]+)-", README)
    assert badge, "version badge missing from README"
    assert badge.group(1) == termstats.__version__


def test_readme_badges_all_point_at_the_current_repo():
    """A badge left pointing at the old `stats` repo would 404 forever."""
    for owner_repo in re.findall(r"github\.com/pepperonas/([a-z-]+)", README):
        assert owner_repo == "termstats", f"stale repo reference: {owner_repo}"


def test_readme_and_help_agree_on_the_flags(capsys):
    cli.print_help()
    help_text = capsys.readouterr().out
    for flag in ("--live", "--interval", "--version", "--help"):
        assert flag in help_text, f"{flag} missing from --help"
        assert flag in README, f"{flag} missing from README"
