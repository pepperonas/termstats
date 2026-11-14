"""Print the CHANGELOG section of one version - the body of its GitHub release.

    python tools/release_notes.py 0.9.1

Exits 1 when the changelog has no entry for that version: a release without notes is a
release nobody can read, and the release workflow should stop rather than publish one.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def notes_for(version, changelog=None):
    """The text between `## [version]` and the next `## [` heading, or None."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8") if changelog is None else changelog
    match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    if not match:
        return None
    return match.group(1).strip() + "\n"


def main(argv):
    if len(argv) != 2:
        print("usage: release_notes.py VERSION", file=sys.stderr)
        return 2
    notes = notes_for(argv[1].lstrip("v"))
    if not notes:
        print(f"CHANGELOG.md has no entry for {argv[1]}", file=sys.stderr)
        return 1
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
