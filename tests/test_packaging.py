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
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def pyproject_value(key):
    match = re.search(rf'^{key}\s*=\s*"([^"]+)"', PYPROJECT, re.M)
    assert match, f"{key} not found in pyproject.toml"
    return match.group(1)


# --- version ----------------------------------------------------------------------

def test_version_is_the_same_in_both_places():
    """The header and --version read __init__.py; packaging reads pyproject.toml."""
    assert termstats.__version__ == pyproject_value("version")


def test_version_is_valid_semver():
    """The official SemVer 2.0.0 pattern, so a "0.1" or "1.0.0-" cannot slip through."""
    semver = (
        r"(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
        r"(?:-(?P<pre>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
        r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
        r"(?:\+(?P<build>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
    )
    assert re.fullmatch(semver, termstats.__version__)


def test_a_zero_major_is_not_advertised_as_production_stable():
    """0.x means the interface can still move; the trove classifier has to agree."""
    major = int(termstats.__version__.split(".")[0])
    stable = "Development Status :: 5 - Production/Stable" in PYPROJECT
    assert not (major == 0 and stable), "0.x cannot claim Production/Stable"


# --- changelog --------------------------------------------------------------------

def test_changelog_names_the_current_version_first():
    """The top released heading must be what the package reports, or the changelog lies."""
    headings = re.findall(r"^## \[([^\]]+)\]", CHANGELOG, re.M)
    released = [h for h in headings if h.lower() != "unreleased"]
    assert released, "no released version in CHANGELOG.md"
    assert released[0] == termstats.__version__


def test_changelog_keeps_an_unreleased_section():
    assert re.search(r"^## \[Unreleased\]", CHANGELOG, re.M)


def test_changelog_states_the_versioning_policy():
    """0.x semantics are unusual enough that they must be written down, not assumed."""
    assert "semver.org" in CHANGELOG
    assert "1.0.0" in CHANGELOG


def test_readme_links_to_the_changelog():
    assert "CHANGELOG.md" in README


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


def test_readme_has_no_hand_typed_version_badge():
    """The version badge is generated (see tools/badges.py). A literal one goes stale."""
    assert not re.search(r"shields\.io/badge/version-[\d.]+-", README), (
        "hard-coded version badge found - use the generated endpoint badge"
    )


def test_readme_headline_badges_are_all_generated():
    assert "img.shields.io/endpoint?url=" in README
    for name in ("version", "loc", "tests"):
        assert f".github/badges/{name}.json" in README


# --- documented behaviour matches the code ----------------------------------------

def test_readme_states_the_real_default_interval():
    assert f"default: {cli.DEFAULT_INTERVAL:g}" in README


def test_help_states_the_real_default_interval(capsys):
    cli.print_help()
    assert f"default: {cli.DEFAULT_INTERVAL:g}" in capsys.readouterr().out


def test_readme_no_longer_advertises_snapshot_as_the_default():
    """The old opener was "termstats  # one snapshot"; that is now a lie."""
    assert not re.search(r"^termstats\s+#\s*one snapshot\s*$", README, re.M)


def test_readme_documents_the_automatic_snapshot_rule():
    """Anyone piping the command needs to find out why it did not stay live."""
    assert "isatty" in README
    assert "--once" in README


# --- support link -----------------------------------------------------------------

PAYPAL_ACCOUNT = "martin.pfeffer%40celox.io"


def paypal_links(text=README):
    return re.findall(r"https://www\.paypal\.com/donate/\?[^\s)]+", text)


def test_readme_has_a_paypal_link():
    assert paypal_links(), "PayPal donation link missing"


def test_every_paypal_link_points_at_the_right_account():
    """Checking only the first one was a green-blind pin: the README carries two, so a
    typo in either was invisible as long as the other survived."""
    links = paypal_links()
    assert links
    for link in links:
        assert PAYPAL_ACCOUNT in link, f"donation link points elsewhere: {link}"


def test_no_stray_paypal_url_bypasses_the_check():
    """Any paypal.com reference at all has to be one of the checked donate links."""
    mentions = re.findall(r"https://[a-z.]*paypal\.com/[^\s)]+", README)
    assert len(mentions) == len(paypal_links()), f"unchecked PayPal URL: {mentions}"


def test_the_support_section_itself_carries_the_button():
    """Header badges are easy to miss on mobile; the section must stand on its own."""
    section = README.split("## Support this project", 1)[1].split("\n## ", 1)[0]
    assert paypal_links(section), "no donate link inside the Support section"


def test_the_paypal_address_is_percent_encoded():
    """A raw '@' in a query value is legal but is mangled by enough Markdown renderers
    and chat clients to be worth avoiding."""
    assert "business=martin.pfeffer@celox.io" not in README


def test_the_donation_is_offered_but_not_demanded():
    """A section heading, not a paywall - the tool must not read as nagware."""
    assert "## Support this project" in README
    assert "free" in README.lower()


def test_readme_badges_all_point_at_the_current_repo():
    """A badge left pointing at the old `stats` repo would 404 forever."""
    for owner_repo in re.findall(r"github\.com/pepperonas/([a-z-]+)", README):
        assert owner_repo == "termstats", f"stale repo reference: {owner_repo}"


def test_readme_and_help_agree_on_the_flags(capsys):
    cli.print_help()
    help_text = capsys.readouterr().out
    for flag in ("--live", "--once", "--interval", "--version", "--help"):
        assert flag in help_text, f"{flag} missing from --help"
        assert flag in README, f"{flag} missing from README"
