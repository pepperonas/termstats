---
name: get-termstats
description: Download, verify and install the newest termstats - the free, open-source system dashboard for the terminal (macOS, Linux, Windows). Use when someone asks for the tool, its latest version, a download link for a platform, or how to check that a download is genuine.
license: MIT
---

# Get termstats

termstats is a free, open-source (MIT) system dashboard for the terminal. It shows CPU load per core, memory and swap, network rates, disks, the busiest processes and live history charts, and adapts to the size and abilities of the terminal. On macOS and Linux a native binary written in Rust draws the live dashboard with about 0.3 % of one CPU core and 7 MB of memory; the Python program adds microphone screens (spectrum analyser, tempo, level), a scripted demo and snapshots, and runs on Windows too, where it ships as a single termstats.exe. On macOS and Linux termstats can record what the machine does in a local log and, after 14 days, recommend a computer that fits. It needs no account and no configuration file and never contacts the network.

## 1. Find the newest release

`GET https://termstats.celox.io/latest.json` returns `version`, `published`, `notes` and `assets[]`, each with `target`,
`name`, `url`, `size` (bytes) and `sha256`. It is refreshed from GitHub Releases every 15 minutes. On the
page itself, browsers with WebMCP expose the same data as the tools `get_latest_release`,
`get_download_url` and `get_checksums`.

## 2. Download

- Stable link, always the newest file for the visitor's platform: <https://termstats.celox.io/download>
- macOS: <https://termstats.celox.io/download/macos> — macOS · Apple silicon
- Linux: <https://termstats.celox.io/download/linux> — Linux · x86-64 · static binary
- Windows: <https://termstats.celox.io/download/windows> — Windows 10/11 · x64 · no Python needed
- macOS Intel: <https://termstats.celox.io/download/macos-intel> — macOS · Intel
- Linux ARM64: <https://termstats.celox.io/download/linux-arm64> — Linux · ARM64 (Raspberry Pi 4/5, ARM servers)
- Python wheel: <https://termstats.celox.io/download/python> — Python 3.9+ · every platform

## 3. Verify

- The file's SHA-256 must equal the matching `assets[].sha256` in `latest.json`.

- Compare with `shasum -a 256 <file>` (Windows: `certutil -hashfile <file> SHA256`). Every release also carries a `SHA256SUMS` file, and the install script checks against it.

## 4. Install

1. The button above picks it; the chips offer the other platforms. On macOS and Linux one line does everything: `curl -fsSL https://github.com/pepperonas/termstats/releases/latest/download/install.sh | sh`
2. macOS and Linux: `tar -xzf termstats-native-*.tar.gz`, then move `termstats-native` into a folder on your `PATH`, for example `~/.local/bin`. Windows: unpack the zip anywhere.
3. `termstats-native` on macOS and Linux, `termstats.exe` in Windows Terminal. `Esc` or `q` leaves. A file downloaded with a browser on macOS needs `xattr -d com.apple.quarantine termstats-native` once.

## Limits

- The native binary, the history recorder and the recommendation run on macOS and Linux only; Windows gets the Python program as termstats.exe.
- The native binary has no microphone screens, no demo and no ASCII or block fallbacks - those are in the Python program.
- termstats is not on PyPI, Homebrew or winget; it is distributed through GitHub Releases and this page.
- The files are not code-signed or notarised: macOS quarantines a browser download and Windows SmartScreen may ask once.
- It watches one machine in one terminal; it is not a monitoring server and keeps no remote history.
- The recommendation uses list prices and benchmark scores from a catalogue shipped with each release; it does not check special requirements such as particular software or ports.

More: [product page](https://termstats.celox.io/) · [Markdown version](https://termstats.celox.io/index.md) · [changelog](https://termstats.celox.io/changelog.md) · [source](https://github.com/pepperonas/termstats)
