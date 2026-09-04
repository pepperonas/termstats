"""The badge generator behind the three headline README badges.

A badge is a claim, and a stale or zeroed one is worse than none at all: it is a
confident wrong number. These tests pin the counting rules and, more importantly, the
refusals - the generator must fail loudly rather than publish a guess.
"""

import importlib.util
import json
from pathlib import Path

import pytest

import termstats

ROOT = Path(__file__).resolve().parent.parent


def _load_badges_module():
    spec = importlib.util.spec_from_file_location("badges", ROOT / "tools" / "badges.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


badges = _load_badges_module()


@pytest.fixture
def project(tmp_path):
    """A miniature project tree: 6 lines of code among noise."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(
        "# a comment\n"
        "\n"
        "import os\n"
        "   \n"                       # whitespace only
        "    # an indented comment\n"
        "def f():\n"
        "    return os.sep\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mod.py").write_text(
        "def test_f():\n    assert True\n", encoding="utf-8"
    )
    (tmp_path / "termstats").mkdir()
    (tmp_path / "termstats" / "__init__.py").write_text(
        '__version__ = "9.8.7"\n', encoding="utf-8"
    )
    return tmp_path


# --- counting lines ----------------------------------------------------------------

def test_blank_lines_and_comments_do_not_count(project):
    """3 lines in mod.py + 2 in the test + 1 in __init__ = 6."""
    assert badges.count_loc(project) == 6


@pytest.mark.parametrize("junk", ["build", "dist", ".venv", "venv", "__pycache__",
                                  ".pytest_cache", "node_modules", "temp", ".tox"])
def test_generated_and_vendored_trees_are_skipped(project, junk):
    """Counting .venv would report the line count of every dependency."""
    before = badges.count_loc(project)
    (project / junk).mkdir()
    (project / junk / "huge.py").write_text("x = 1\n" * 5000, encoding="utf-8")
    assert badges.count_loc(project) == before


def test_egg_info_is_skipped_by_suffix_not_by_name(project):
    """The directory is named after the distribution, so it cannot be listed literally."""
    egg = project / "somename.egg-info"
    egg.mkdir()
    (egg / "generated.py").write_text("y = 2\n", encoding="utf-8")
    assert badges.count_loc(project) == 6


def test_nested_packages_are_counted(project):
    deep = project / "pkg" / "sub" / "deeper"
    deep.mkdir(parents=True)
    (deep / "x.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    assert badges.count_loc(project) == 8


def test_non_python_files_are_ignored(project):
    (project / "README.md").write_text("# lots\n" * 100, encoding="utf-8")
    (project / "data.json").write_text("{}\n", encoding="utf-8")
    assert badges.count_loc(project) == 6


def test_file_listing_is_deterministic(project):
    assert badges.python_files(project) == badges.python_files(project)


def test_the_real_project_is_counted_at_all():
    """Counter-check: a rule that skipped everything would happily return 0."""
    assert badges.count_loc(ROOT) > 500


# --- reading pytest's collection ---------------------------------------------------

def test_parses_the_per_file_summary():
    """pytest 9 with -q --collect-only prints one line per file."""
    text = "tests/test_a.py: 41\ntests/test_b.py: 20\n"
    assert badges.parse_collected(text) == 61


def test_parses_the_collected_summary_line():
    """Older pytest prints node ids and a total."""
    assert badges.parse_collected("...\n\n153 tests collected in 0.31s\n") == 153


def test_parses_a_single_collected_test():
    assert badges.parse_collected("1 test collected in 0.01s\n") == 1


def test_falls_back_to_counting_node_ids():
    text = "tests/test_a.py::test_one\ntests/test_a.py::test_two\ntests/test_b.py::test_x\n"
    assert badges.parse_collected(text) == 3


def test_per_file_summary_wins_over_node_ids():
    """Both shapes present must not be added together."""
    text = "tests/test_a.py::test_one\ntests/test_a.py: 7\n"
    assert badges.parse_collected(text) == 7


def test_empty_output_parses_as_zero():
    assert badges.parse_collected("") == 0


# --- refusing to publish a guess ---------------------------------------------------

def test_a_zero_test_count_is_refused_not_published():
    """The real failure this prevents: pytest missing from the interpreter, badge '0'."""
    with pytest.raises(badges.BadgeError, match="zero"):
        badges.count_tests(ROOT, runner=lambda root: "")


def test_a_failing_collection_names_the_interpreter():
    def angry(root):
        raise badges.BadgeError("pytest collection failed (exit 4) under /usr/bin/python3")

    with pytest.raises(badges.BadgeError, match="collection failed"):
        badges.count_tests(ROOT, runner=angry)


def test_a_good_runner_is_believed():
    assert badges.count_tests(ROOT, runner=lambda root: "tests/x.py: 12\n") == 12


# --- payload shape -----------------------------------------------------------------

def test_badge_matches_the_shields_endpoint_schema():
    payload = badges.badge("label", "message", "ABCDEF")
    assert payload == {"schemaVersion": 1, "label": "label",
                       "message": "message", "color": "ABCDEF"}


def test_version_reads_the_package_not_a_literal(project):
    assert badges.read_version(project) == "9.8.7"


def test_build_reports_the_installed_version():
    built = badges.build(ROOT, runner=lambda root: "tests/x.py: 3\n")
    assert built["version"]["message"] == f"v{termstats.__version__}"
    assert built["tests"]["message"] == "3"
    assert built["loc"]["message"].isdigit()


def test_loc_message_has_no_thousands_separator():
    """A separator is a rendering gamble for a number the badge must state exactly."""
    built = badges.build(ROOT, runner=lambda root: "tests/x.py: 3\n")
    assert built["loc"]["message"].isdigit()


def test_serialise_is_stable_and_newline_terminated():
    text = badges.serialise(badges.badge("a", "b", "c"))
    assert text.endswith("\n")
    assert json.loads(text)["label"] == "a"


# --- the committed files -----------------------------------------------------------

BADGE_NAMES = ["version", "loc", "tests"]


@pytest.mark.parametrize("name", BADGE_NAMES)
def test_every_badge_file_is_committed_and_valid(name):
    payload = json.loads((ROOT / ".github" / "badges" / f"{name}.json").read_text())
    assert payload["schemaVersion"] == 1
    assert payload["label"] and payload["message"]


def test_the_committed_version_badge_matches_the_package():
    """The one badge worth pinning exactly: you bump the version deliberately."""
    payload = json.loads((ROOT / ".github" / "badges" / "version.json").read_text())
    assert payload["message"] == f"v{termstats.__version__}"


@pytest.mark.parametrize("name", BADGE_NAMES)
def test_the_readme_actually_uses_every_generated_badge(name):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"badges/{name}.json" in readme, f"{name}.json is generated but never shown"


# --- the command line --------------------------------------------------------------

@pytest.fixture
def isolated_main(tmp_path, monkeypatch):
    """Point main() at a scratch badge directory with a fixed, cheap payload."""
    monkeypatch.setattr(badges, "BADGE_DIR", tmp_path / "badges")
    monkeypatch.setattr(badges, "build", lambda: {"version": badges.badge("version", "v1.2.3", "x")})
    return tmp_path / "badges"


def test_main_writes_the_files(isolated_main, capsys):
    assert badges.main([]) == 0
    assert json.loads((isolated_main / "version.json").read_text())["message"] == "v1.2.3"


def test_check_reports_stale_files_without_writing_them(isolated_main, capsys):
    assert badges.main(["--check"]) == 1
    assert not (isolated_main / "version.json").exists()
    assert "out of date" in capsys.readouterr().err


def test_check_passes_once_the_files_are_current(isolated_main, capsys):
    badges.main([])
    capsys.readouterr()
    assert badges.main(["--check"]) == 0


def test_a_broken_build_exits_nonzero_instead_of_writing_junk(isolated_main, monkeypatch, capsys):
    def boom():
        raise badges.BadgeError("pytest collected no tests")

    monkeypatch.setattr(badges, "build", boom)
    assert badges.main([]) == 1
    assert not (isolated_main / "version.json").exists()
    assert "no tests" in capsys.readouterr().err
