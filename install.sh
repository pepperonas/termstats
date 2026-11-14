#!/bin/sh
# termstats - installs the native dashboard (termstats-native) from the latest GitHub release.
#
#   curl -fsSL https://github.com/pepperonas/termstats/releases/latest/download/install.sh | sh
#
# No toolchain needed: it downloads the binary for this machine, checks it against the
# release's SHA256SUMS and puts it into ~/.local/bin. Nothing else is touched, and nothing is
# run as root.
#
#   TERMSTATS_VERSION=v0.9.2   install that release instead of the latest
#   TERMSTATS_BIN_DIR=/path    install there instead of ~/.local/bin
#
# macOS (Apple silicon, Intel) and Linux (x86_64, aarch64; static, any distribution). On
# Windows, and for the microphone modes, install the Python program - see the README.
set -eu

REPO="https://github.com/pepperonas/termstats"
BIN_DIR="${TERMSTATS_BIN_DIR:-$HOME/.local/bin}"
VERSION="${TERMSTATS_VERSION:-latest}"

fail() {
    printf 'install.sh: %s\n' "$1" >&2
    exit 1
}

# The release asset for an operating system and a machine type, as `uname` names them.
target_for() {
    case "$1/$2" in
        Darwin/arm64 | Darwin/aarch64) echo "aarch64-apple-darwin" ;;
        Darwin/x86_64) echo "x86_64-apple-darwin" ;;
        Linux/x86_64 | Linux/amd64) echo "x86_64-unknown-linux-musl" ;;
        Linux/aarch64 | Linux/arm64) echo "aarch64-unknown-linux-musl" ;;
        *) return 1 ;;
    esac
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d ' ' -f 1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d ' ' -f 1
    else
        return 1
    fi
}

main() {
    target=$(target_for "$(uname -s)" "$(uname -m)") ||
        fail "no native build for $(uname -s) $(uname -m) - install the Python program instead: pipx install git+$REPO.git"
    command -v curl >/dev/null 2>&1 || fail "curl is needed to download the release"
    command -v tar >/dev/null 2>&1 || fail "tar is needed to unpack the release"

    if [ -n "${TERMSTATS_RELEASE_URL:-}" ]; then
        base="$TERMSTATS_RELEASE_URL"                       # a mirror, or a test
    elif [ "$VERSION" = "latest" ]; then
        base="$REPO/releases/latest/download"
    else
        base="$REPO/releases/download/$VERSION"
    fi
    archive="termstats-native-$target.tar.gz"

    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT INT TERM

    printf 'Downloading %s (%s)\n' "$archive" "$VERSION"
    curl -fsSL "$base/$archive" -o "$work/$archive" || fail "could not download $base/$archive"
    curl -fsSL "$base/SHA256SUMS" -o "$work/SHA256SUMS" || fail "could not download $base/SHA256SUMS"

    wanted=$(awk -v name="$archive" '$2 == name || $2 == "*" name { print $1 }' "$work/SHA256SUMS")
    [ -n "$wanted" ] || fail "$archive is not listed in SHA256SUMS"
    got=$(sha256_of "$work/$archive") || fail "neither sha256sum nor shasum found - cannot verify the download"
    [ "$wanted" = "$got" ] || fail "checksum mismatch for $archive (expected $wanted, got $got) - nothing was installed"

    tar -xzf "$work/$archive" -C "$work"
    [ -f "$work/termstats-native" ] || fail "the archive does not contain termstats-native"
    mkdir -p "$BIN_DIR"
    # Written beside the target and renamed: a running dashboard keeps its old file, and a
    # failed copy never leaves half a binary under the real name.
    cp "$work/termstats-native" "$BIN_DIR/.termstats-native.new"
    chmod 755 "$BIN_DIR/.termstats-native.new"
    mv -f "$BIN_DIR/.termstats-native.new" "$BIN_DIR/termstats-native"

    printf 'Installed %s\n' "$BIN_DIR/termstats-native"
    "$BIN_DIR/termstats-native" --version || fail "the installed binary does not start on this machine"
    case ":$PATH:" in
        *":$BIN_DIR:"*) printf 'Run it:  termstats-native\n' ;;
        *) printf '%s is not on your PATH. Add it, or run:  %s/termstats-native\n' "$BIN_DIR" "$BIN_DIR" ;;
    esac
}

# Sourced by the tests for target_for(); run when executed or piped into sh.
if [ "${TERMSTATS_INSTALL_LIB:-}" != "1" ]; then
    main
fi
