#!/usr/bin/env python3
"""Generate the shields.io endpoint JSON behind the three headline README badges.

Version, lines of code and test count all go stale the moment you touch the code, and a
stale badge is worse than no badge - it is a confident wrong number. So they are not typed
by hand: this script computes them, writes .github/badges/*.json, and CI re-runs it on every
push to main and commits the result when it changed (see .github/workflows/tests.yml).

Usage:
    python tools/badges.py            # rewrite the badge files
    python tools/badges.py --check    # exit 1 if they are out of date, write nothing
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BADGE_DIR = ROOT / ".github" / "badges"

# Directories that contain Python but not *this project's* Python.
SKIP_DIRS = {
    ".git", ".github", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "build", "dist", ".venv", "venv", "env", ".tox", "node_modules", "temp",
}

COLOR_VERSION = "0A7BBB"
COLOR_LOC = "5C4EE5"
COLOR_TESTS = "2EA043"


class BadgeError(RuntimeError):
    """Something made the numbers untrustworthy. Never publish a guess."""


def _is_wanted(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    return not any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel.parts)


def python_files(root: Path = ROOT):
    """Every .py file that belongs to the project, in a stable order."""
    return sorted(p for p in root.rglob("*.py") if _is_wanted(p, root))


def count_loc(root: Path = ROOT) -> int:
    """Lines of code: non-blank, non-comment lines across the project's .py files.

    Deliberately the simple SLOC approximation - blank lines and whole-line `#` comments
    are dropped, everything else counts. It needs no dependency, is trivial to explain,
    and cannot silently disagree with itself between machines.
    """
    total = 0
    for path in python_files(root):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                total += 1
    return total


def parse_collected(text: str) -> int:
    """Read a test count out of `pytest --collect-only -q` output.

    Three formats in the wild, hence three passes: pytest 9 prints "<file>: <n>" per file,
    older versions print one nodeid per line plus an "N tests collected" summary.
    """
    per_file = re.findall(r"^\S+\.py: (\d+)$", text, re.M)
    if per_file:
        return sum(int(n) for n in per_file)
    summary = re.search(r"(\d+) tests? collected", text)
    if summary:
        return int(summary.group(1))
    return sum(1 for line in text.splitlines() if "::" in line)


def _run_pytest_collect(root: Path) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=root, capture_output=True, text=True,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise BadgeError(
            f"pytest collection failed (exit {result.returncode}) under {sys.executable}.\n"
            "Run this script with a Python that has the dev extra installed:\n"
            "  pip install -e \".[dev]\" && python tools/badges.py\n\n" + output.strip()
        )
    return output


def count_tests(root: Path = ROOT, runner=_run_pytest_collect) -> int:
    """Authoritative test count, straight from pytest.

    Refuses to report zero: a badge proudly announcing "0 unit tests" because pytest was
    missing from the interpreter is precisely the confident wrong number this script exists
    to prevent.
    """
    count = parse_collected(runner(root))
    if count <= 0:
        raise BadgeError("pytest collected no tests - refusing to publish a zero badge")
    return count


def read_version(root: Path = ROOT) -> str:
    text = (root / "termstats" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("could not read __version__ from termstats/__init__.py")
    return match.group(1)


def badge(label: str, message: str, color: str) -> dict:
    """A shields.io endpoint payload: https://shields.io/badges/endpoint-badge"""
    return {"schemaVersion": 1, "label": label, "message": message, "color": color}


def build(root: Path = ROOT, runner=_run_pytest_collect) -> dict[str, dict]:
    loc = count_loc(root)
    return {
        "version": badge("version", f"v{read_version(root)}", COLOR_VERSION),
        "loc": badge("lines of code", str(loc), COLOR_LOC),
        "tests": badge("unit tests", str(count_tests(root, runner)), COLOR_TESTS),
    }


def serialise(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    check_only = "--check" in argv

    try:
        badges = build()
    except BadgeError as exc:
        print(f"badges: {exc}", file=sys.stderr)
        return 1
    BADGE_DIR.mkdir(parents=True, exist_ok=True)

    stale = []
    for name, payload in badges.items():
        path = BADGE_DIR / f"{name}.json"
        text = serialise(payload)
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        stale.append(name)
        if not check_only:
            path.write_text(text, encoding="utf-8")

    try:
        rel = BADGE_DIR.relative_to(ROOT)
    except ValueError:          # BADGE_DIR pointed somewhere else (tests do this)
        rel = BADGE_DIR
    if check_only:
        if stale:
            print(f"badges out of date: {', '.join(stale)} - run 'python tools/badges.py'",
                  file=sys.stderr)
            return 1
        print(f"{rel}: up to date")
        return 0

    print(f"{rel}: " + ("updated " + ", ".join(stale) if stale else "already up to date"))
    for name, payload in badges.items():
        print(f"  {payload['label']:>13s}: {payload['message']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
