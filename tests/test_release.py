"""Releases that can be downloaded (0.9.1).

A pushed tag becomes a GitHub release with the native binary for four targets, the wheel,
the sdist, install.sh and SHA256SUMS. Three files have to agree on the asset names - the
workflow that builds them, the script that downloads them and the README that links to
them - and none of them is checked by anything else: a mismatch is a dead link that only a
user finds.
"""

import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile

import pytest

import termstats

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
INSTALL = ROOT / "install.sh"
README = (ROOT / "README.md").read_text(encoding="utf-8")
TARGETS = {"aarch64-apple-darwin", "x86_64-apple-darwin", "x86_64-unknown-linux-musl", "aarch64-unknown-linux-musl"}

posix = pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None, reason="needs a POSIX shell")


def sh(script, env=None, **kwargs):
    full = dict(os.environ, **(env or {}))
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=full, timeout=60, **kwargs)


# --- three files, one set of names ----------------------------------------------------------

def test_the_workflow_builds_the_four_targets():
    assert set(re.findall(r"target: ([a-z0-9_-]+)", WORKFLOW)) == TARGETS


def test_the_readme_links_every_download_and_nothing_else():
    linked = set(re.findall(r"releases/latest/download/termstats-native-([a-z0-9_-]+)\.tar\.gz", README))
    assert linked == TARGETS


def test_the_assets_are_named_the_way_they_are_linked():
    """No version in the name: only then does releases/latest/download/<name> stay valid."""
    assert "dist/termstats-native-${{ matrix.target }}.tar.gz" in WORKFLOW
    assert 'archive="termstats-native-$target.tar.gz"' in INSTALL.read_text(encoding="utf-8")


@posix
@pytest.mark.parametrize("system, machine, target", [
    ("Darwin", "arm64", "aarch64-apple-darwin"),
    ("Darwin", "x86_64", "x86_64-apple-darwin"),
    ("Linux", "x86_64", "x86_64-unknown-linux-musl"),
    ("Linux", "aarch64", "aarch64-unknown-linux-musl"),
    ("Linux", "arm64", "aarch64-unknown-linux-musl"),
])
def test_the_script_picks_the_asset_for_the_machine(system, machine, target):
    result = sh(f'. "{INSTALL}"; target_for {system} {machine}', env={"TERMSTATS_INSTALL_LIB": "1"})
    assert result.stdout.strip() == target and target in TARGETS


@posix
@pytest.mark.parametrize("system, machine", [("Linux", "riscv64"), ("FreeBSD", "x86_64"), ("MINGW64_NT", "x86_64")])
def test_a_machine_without_a_build_is_refused_not_guessed(system, machine):
    result = sh(f'. "{INSTALL}"; target_for {system} {machine}', env={"TERMSTATS_INSTALL_LIB": "1"})
    assert result.returncode != 0 and result.stdout.strip() == ""


@posix
def test_the_script_is_valid_posix_shell():
    assert subprocess.run(["sh", "-n", str(INSTALL)], capture_output=True).returncode == 0
    assert INSTALL.read_text(encoding="utf-8").startswith("#!/bin/sh\n"), "piped into sh: no bashisms"


# --- the script, against a release that lives in a directory --------------------------------

@pytest.fixture
def release(tmp_path):
    """A fake release for THIS machine: the 'binary' is a script that prints a version."""
    system, machine = os.uname().sysname, os.uname().machine
    target = sh(f'. "{INSTALL}"; target_for {system} {machine}', env={"TERMSTATS_INSTALL_LIB": "1"}).stdout.strip()
    if not target:
        pytest.skip(f"no native build for {system} {machine}")
    folder = tmp_path / "release"
    folder.mkdir()
    binary = tmp_path / "termstats-native"
    binary.write_text("#!/bin/sh\necho termstats-native 9.9.9\n")
    binary.chmod(0o755)
    archive = folder / f"termstats-native-{target}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(binary, arcname="termstats-native")
    (folder / "SHA256SUMS").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    return folder, archive, tmp_path / "bin"


def install(folder, bin_dir):
    return sh(f'sh "{INSTALL}"', env={"TERMSTATS_RELEASE_URL": f"file://{folder}", "TERMSTATS_BIN_DIR": str(bin_dir)})


@posix
def test_the_script_installs_the_verified_binary(release):
    folder, _archive, bin_dir = release
    result = install(folder, bin_dir)
    assert result.returncode == 0, result.stderr
    installed = bin_dir / "termstats-native"
    assert installed.exists() and os.access(installed, os.X_OK)
    assert "termstats-native 9.9.9" in result.stdout, "it runs what it installed"
    assert not list(bin_dir.glob(".termstats-native*")), "no half-written file left behind"


@posix
def test_a_download_that_does_not_match_its_checksum_installs_nothing(release):
    folder, archive, bin_dir = release
    with open(archive, "ab") as f:
        f.write(b"tampered")
    result = install(folder, bin_dir)
    assert result.returncode != 0 and "checksum mismatch" in result.stderr
    assert not (bin_dir / "termstats-native").exists()


@posix
def test_an_archive_missing_from_the_checksum_list_installs_nothing(release):
    folder, _archive, bin_dir = release
    (folder / "SHA256SUMS").write_text("0" * 64 + "  something-else.tar.gz\n")
    result = install(folder, bin_dir)
    assert result.returncode != 0 and "not listed" in result.stderr
    assert not (bin_dir / "termstats-native").exists()


@posix
def test_a_release_that_is_not_there_is_an_error_not_an_empty_install(release, tmp_path):
    _folder, _archive, bin_dir = release
    result = install(tmp_path / "no-such-release", bin_dir)
    assert result.returncode != 0 and "could not download" in result.stderr


@posix
def test_an_update_replaces_the_installed_binary(release):
    folder, _archive, bin_dir = release
    bin_dir.mkdir()
    (bin_dir / "termstats-native").write_text("old")
    assert install(folder, bin_dir).returncode == 0
    assert "9.9.9" in (bin_dir / "termstats-native").read_text()


# --- the release itself ---------------------------------------------------------------------

def test_the_readme_installs_the_current_version():
    """The two wheel URLs carry the version. A stale one is a 404 the day after a release."""
    version = termstats.__version__
    wheels = re.findall(r"releases/download/v([\d.]+)/termstats-([\d.]+)-py3-none-any\.whl", README)
    assert len(wheels) == 2 and all(tag == version and name == version for tag, name in wheels), wheels
    assert f"TERMSTATS_VERSION=v{version}" in README


def test_the_lockfile_follows_the_version():
    """The release builds with --locked: a Cargo.lock one version behind stops it."""
    lock = (ROOT / "native" / "Cargo.lock").read_text(encoding="utf-8")
    entry = re.search(r'name = "termstats-native"\nversion = "([^"]+)"', lock)
    assert entry and entry.group(1) == termstats.__version__, "run cargo build in native/"
    assert "--locked" in WORKFLOW


def test_the_workflow_refuses_a_tag_that_is_not_the_version():
    assert 'test "v$version" = "$TAG"' in WORKFLOW


def test_the_release_carries_checksums_the_script_and_the_python_package():
    assert "sha256sum * > SHA256SUMS" in WORKFLOW and "cp install.sh assets/" in WORKFLOW
    assert "python -m build" in WORKFLOW and "--latest" in WORKFLOW


def test_the_release_text_is_the_changelog_entry():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import release_notes
    finally:
        sys.path.pop(0)
    notes = release_notes.notes_for(termstats.__version__)
    assert notes and notes.strip(), "the current version has no changelog entry"
    assert "## [" not in notes, "the entry ends where the next version begins"
    sample = "## [2.0.0] - 2030-01-01\n\nnew\n\n## [1.0.0] - 2029-01-01\n\nold\n"
    assert release_notes.notes_for("2.0.0", sample) == "new\n"
    assert release_notes.notes_for("1.0.0", sample) == "old\n"
    assert release_notes.notes_for("3.0.0", sample) is None
    assert release_notes.main(["x", "v" + termstats.__version__]) == 0, "the tag form works too"
    assert release_notes.main(["x", "0.0.0-nope"]) == 1
