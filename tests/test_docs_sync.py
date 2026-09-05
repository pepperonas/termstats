"""The README, --help and CHANGELOG describe the code; these pins keep them from drifting.

Every check reads the real artefact and the real code. A flag added to cli.py without a
README row, a theme without a table entry, an image link without a file, a changelog entry
out of order: all red here rather than found by a reader.
"""
import re
from pathlib import Path

import pytest

from termstats import cli
from termstats import theme as T

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
THEME_SRC = (ROOT / "termstats" / "theme.py").read_text(encoding="utf-8")
CLI_SRC = (ROOT / "termstats" / "cli.py").read_text(encoding="utf-8")
RAW = "https://raw.githubusercontent.com/pepperonas/termstats/main/"


def help_text(capsys):
    cli.print_help()
    return capsys.readouterr().out


def section(title):
    """The body of a `##`/`###` README section, up to the next heading of the same level."""
    m = re.search(rf"^(#{{2,3}}) {re.escape(title)}\s*$", README, re.M)
    assert m, f"README has no section {title!r}"
    level = m.group(1)
    rest = README[m.end():]
    nxt = re.search(rf"^#{{1,{len(level)}}} ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def long_flags():
    """Every --long option the parser accepts, read from the _*_FLAGS tuples."""
    tuples = re.findall(r"^_[A-Z_]+_FLAGS = \((.*)\)$", CLI_SRC, re.M)
    flags = set()
    for body in tuples:
        flags.update(re.findall(r'"(--[a-z-]+)"', body))
    assert flags, "no flag tuples found"
    return flags


# --- options ---------------------------------------------------------------------------

def test_every_parser_flag_has_a_readme_options_row():
    table = section("Options")
    for flag in long_flags():
        assert re.search(rf"^\|[^|]*`[^`]*{re.escape(flag)}", table, re.M), f"{flag} has no row in the Options table"


def test_every_readme_options_row_names_a_real_flag():
    table = section("Options")
    documented = set(re.findall(r"^\|[^|]*`[^`]*(--[a-z-]+)", table, re.M))
    assert documented, "the Options table has no rows"
    assert documented <= long_flags(), f"README documents flags the parser does not know: {documented - long_flags()}"


def test_every_parser_flag_is_in_help(capsys):
    text = help_text(capsys)
    for flag in long_flags():
        assert flag in text, f"{flag} missing from --help"


def description_column(line):
    """Where the description of a `  name   description` help line starts."""
    indent = len(line) - len(line.lstrip())
    gap = line.strip().index("  ")                       # first double space ends the name
    rest = line[indent + gap:]
    return indent + gap + (len(rest) - len(rest.lstrip()))


def aligned_block(text, title):
    block = text.split(title)[1].split("\n\n")[0]
    return {description_column(line) for line in block.splitlines() if line.strip()}


def test_help_option_descriptions_are_aligned(capsys):
    columns = aligned_block(help_text(capsys), "Options:")
    assert len(columns) == 1, f"option descriptions start in different columns: {sorted(columns)}"


def test_help_environment_descriptions_are_aligned(capsys):
    columns = aligned_block(help_text(capsys), "Environment:")
    assert len(columns) == 1, f"environment descriptions start in different columns: {sorted(columns)}"


# --- environment variables --------------------------------------------------------------

def code_env_names():
    names = set(re.findall(r"TERMSTATS_[A-Z_]+", CLI_SRC + THEME_SRC))
    names.discard("TERMSTATS_")
    return names


def test_every_termstats_env_var_is_in_the_readme_table():
    table = section("Environment")
    for name in code_env_names():
        assert name in table, f"{name} missing from the README Environment table"


def test_every_termstats_env_var_is_in_help(capsys):
    text = help_text(capsys)
    for name in code_env_names():
        assert name in text, f"{name} missing from --help"


def test_readme_detection_order_names_every_key_the_colour_chain_reads():
    keys = set(re.findall(r'env\.get\("([A-Z_]+)"', THEME_SRC))
    keys.discard("TERMSTATS_GLYPHS"); keys.discard("TERMSTATS_NERD_FONT")
    assert {"NO_COLOR", "TERM", "COLORTERM", "WT_SESSION"} <= keys
    order = re.search(r"Detection order for colour is (.+?)\.", section("Environment"), re.S)
    assert order, "README has no detection-order sentence"
    for key in keys:
        assert key in order.group(1), f"{key} is read by the colour chain but not in the README detection order"


# --- themes -----------------------------------------------------------------------------

def test_every_theme_has_a_readme_table_row():
    table = section("Themes")
    for name in T.theme_names():
        assert re.search(rf"^\| `{re.escape(name)}` \|", table, re.M), f"theme {name} has no row"


def test_readme_theme_table_names_only_real_themes():
    rows = re.findall(r"^\| `([a-z-]+)` \|", section("Themes"), re.M)
    assert set(rows) == set(T.theme_names())


def test_help_lists_every_theme_on_the_theme_line(capsys):
    line = next(l for l in help_text(capsys).splitlines() if "--theme" in l)
    for name in T.theme_names():
        assert name in line


def test_features_list_names_every_theme():
    for name in T.theme_names():
        assert f"`{name}`" in section("Features")


# --- images -----------------------------------------------------------------------------

def readme_images():
    return re.findall(r'src="' + re.escape(RAW) + r'([^"]+)"', README)


def test_every_readme_image_exists_on_disk():
    images = readme_images()
    assert images, "README references no images"
    for rel in images:
        assert (ROOT / rel).is_file(), f"README links {rel} but the file does not exist"


def test_every_screenshot_on_disk_is_shown_in_the_readme():
    on_disk = {p.relative_to(ROOT).as_posix() for p in (ROOT / "docs" / "screenshots").glob("*.png")}
    on_disk |= {p.name for p in ROOT.glob("*.png")}
    orphans = on_disk - set(readme_images())
    assert not orphans, f"screenshots nobody links: {sorted(orphans)}"


def test_every_readme_image_has_alt_text():
    for m in re.finditer(r"<img [^>]*>", README):
        assert re.search(r'alt="[^"]{3,}"', m.group(0)), f"image without alt text: {m.group(0)[:80]}"


# --- table of contents --------------------------------------------------------------------

def slug(heading):
    heading = heading.strip().lower()
    heading = re.sub(r"[^\w\- ]", "", heading)
    return heading.replace(" ", "-")


def test_readme_has_a_table_of_contents_with_every_h2():
    toc = section("Contents")
    for heading in re.findall(r"^## (.+)$", README, re.M):
        if heading.strip() in ("Contents", "termstats"):
            continue
        assert f"(#{slug(heading)})" in toc, f"h2 {heading!r} is not in the table of contents"


def test_every_table_of_contents_link_resolves():
    anchors = {slug(h) for h in re.findall(r"^#{2,3} (.+)$", README, re.M)}
    links = re.findall(r"\]\(#([^)]+)\)", section("Contents"))
    assert links, "the table of contents has no links"
    for link in links:
        assert link in anchors, f"TOC link #{link} points at no heading"


# --- changelog -----------------------------------------------------------------------------

RELEASED = re.findall(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})$", CHANGELOG, re.M)


def test_changelog_released_headings_carry_an_iso_date():
    headings = re.findall(r"^## \[(\d+\.\d+\.\d+)\][^\n]*$", CHANGELOG, re.M)
    assert len(headings) == len(RELEASED), "a released heading is missing its `- YYYY-MM-DD`"


def test_changelog_versions_are_unique_and_descending():
    versions = [tuple(int(x) for x in v.split(".")) for v, _ in RELEASED]
    assert len(set(versions)) == len(versions), "a version appears twice"
    assert versions == sorted(versions, reverse=True), "released versions are not in descending order"


def test_changelog_unreleased_comes_first():
    first = re.search(r"^## \[", CHANGELOG, re.M)
    assert CHANGELOG[first.start():].startswith("## [Unreleased]")


def test_changelog_sections_use_keep_a_changelog_headings():
    allowed = {"Added", "Changed", "Deprecated", "Removed", "Fixed", "Security", "Performance", "Documentation", "Tests", "Tools"}
    for h in re.findall(r"^### (.+)$", CHANGELOG, re.M):
        if re.match(r"\d+\.\d+\.\d+", h.strip()):
            continue          # the pre-reset 1.1.x history is folded into one entry with version sub-headings
        assert h.strip() in allowed, f"unexpected changelog section {h!r}"


# --- the screenshot tool and the README agree on what is shown --------------------------

def test_readme_names_the_screenshot_tool():
    assert "tools/screenshots.py" in README
