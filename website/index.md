<!--# block name="none" --><!--# endblock -->
# termstats — System Monitor for the Terminal, Free Download

> termstats is a free, open-source (MIT) system dashboard for the terminal. It shows CPU load per core, memory and swap, network rates, disks, the busiest processes and live history charts, and adapts to the size and abilities of the terminal. On macOS and Linux a native binary written in Rust draws the live dashboard with about 0.3 % of one CPU core and 7 MB of memory; the Python program adds microphone screens (spectrum analyser, tempo, level), a scripted demo and snapshots, and runs on Windows too, where it ships as a single termstats.exe. On macOS and Linux termstats can record what the machine does in a local log and, after 14 days, recommend a computer that fits. It needs no account and no configuration file and never contacts the network.

This is the Markdown version of https://termstats.celox.io/ for agents and text tools. A short summary with every link lives at https://termstats.celox.io/llms.txt.

## Download

- **Newest release:** https://termstats.celox.io/download (picks the file for your platform; always the current release)
- **Current version:** <!--# include virtual="/ssi/version.txt" stub="none" --> · released <!--# include virtual="/ssi/date.txt" stub="none" -->
- **Release data as JSON:** https://termstats.celox.io/latest.json
- **Requirements:** macOS: macOS · Apple silicon; Linux: Linux · x86-64 · static binary; Windows: Windows 10/11 · x64 · no Python needed; macOS Intel: macOS · Intel; Linux ARM64: Linux · ARM64 (Raspberry Pi 4/5, ARM servers); Python wheel: Python 3.9+ · every platform

Files in the current release:

<!--# include virtual="/ssi/files.md" stub="none" -->

## Features

- **The whole machine on one screen** — CPU load per core, memory and swap, network rates, disks and the busiest processes, with history charts for CPU and network. One colour ramp from cool to hot runs through every bar and number.
- **Light on the machine it watches** — On macOS and Linux the live dashboard is a native binary written in Rust: measured at about 0.3 % of one CPU core and 7 MB of memory. A monitor should not show up in its own process list.
- **Fits the terminal you have** — The layout follows the window and drops whole sections instead of cutting them. Six colour themes; where a terminal cannot draw braille or 24-bit colour, termstats falls back to blocks, ASCII, 256 or 16 colours, and it honours `NO_COLOR`.
- **Sound, measured in the terminal** — The Python program and the Windows build listen to the microphone on request: `-eq` is a 28-band spectrum analyser, `-bpm` finds the tempo, `-db` shows the level, `-x` shows level and tempo together. Nothing is recorded.
- **Remembers what the machine does** — `termstats record install` starts a background service on macOS and Linux that writes one line per minute to a local log — measured at 0.024 % of one CPU core. The log stays on your disk and `record purge` deletes it.
- **Tells you which computer fits** — After 14 days of history, `termstats recommend` names machines in three tiers — just enough, fits, with reserve — plus a Linux alternative, each with the reason in your own numbers. The catalogue of Macs and Linux hardware ships with every release.

### What it looks like

- **The dashboard** — Every core, memory, network, disks, two history charts and the process list. ([image](https://termstats.celox.io/assets/gallery/dashboard.jpg))
- **Six themes** — The same dashboard in each colour theme. ([image](https://termstats.celox.io/assets/gallery/themes.jpg))
- **A small window** — In a laptop-sized terminal the layout drops a section whole instead of cutting it. ([image](https://termstats.celox.io/assets/gallery/narrow.jpg))
- **Spectrum analyser** — `-eq`: 28 bands from the microphone, with peak hold. ([image](https://termstats.celox.io/assets/gallery/eq.jpg))
- **Tempo** — `-bpm`: the tempo of what is playing, five rows tall. ([image](https://termstats.celox.io/assets/gallery/bpm.jpg))
- **Level and tempo** — `-x`: how loud and how fast, side by side. ([image](https://termstats.celox.io/assets/gallery/mix.jpg))

## Install

1. **Download the file for your system** — The button above picks it; the chips offer the other platforms. On macOS and Linux one line does everything: `curl -fsSL https://github.com/pepperonas/termstats/releases/latest/download/install.sh | sh`
2. **Unpack it** — macOS and Linux: `tar -xzf termstats-native-*.tar.gz`, then move `termstats-native` into a folder on your `PATH`, for example `~/.local/bin`. Windows: unpack the zip anywhere.
3. **Run it** — `termstats-native` on macOS and Linux, `termstats.exe` in Windows Terminal. `Esc` or `q` leaves. A file downloaded with a browser on macOS needs `xattr -d com.apple.quarantine termstats-native` once.

## Verify

<!--# include virtual="/ssi/checksums.md" stub="none" -->

- Compare with `shasum -a 256 <file>` (Windows: `certutil -hashfile <file> SHA256`). Every release also carries a `SHA256SUMS` file, and the install script checks against it.

## FAQ

**Is termstats free?** Yes. It is free and open source under the MIT licence — no account, no tracking, and no paid version with more features.

**Why is it not on PyPI or Homebrew?** termstats is distributed through GitHub Releases and this page only. Each release has a ready file per platform, so no package manager is needed; with Python you can still run `pipx install` on the wheel.

**What do I need?** A terminal. The native binary runs on macOS (Apple silicon or Intel) and Linux (x86-64 or ARM64, any distribution) and needs nothing else. The Windows file needs 64-bit Windows 10 or 11; Windows Terminal draws it best. The Python wheel needs Python 3.9 or later.

**How do I update?** Download the new file and replace the old one, or run the install script again. termstats never contacts the network, so it does not check for updates itself; this page always shows the newest release.

**What is the difference between the downloads?** The macOS and Linux files are the native binary: the live dashboard, the history recorder and the recommendation. The Windows file and the Python wheel are the Python program: the same dashboard plus the microphone screens, the demo and snapshots, but without recorder and recommendation on Windows. With both installed on macOS or Linux, `termstats` hands the live dashboard to the native binary by itself.

**Is the file genuine?** Every release is built by GitHub Actions from the public source code, and this page lists the SHA-256 of each file. The files are not code-signed: macOS quarantines a browser download and Windows SmartScreen may ask once — compare the checksum first.

## Limits

- The native binary, the history recorder and the recommendation run on macOS and Linux only; Windows gets the Python program as termstats.exe.
- The native binary has no microphone screens, no demo and no ASCII or block fallbacks - those are in the Python program.
- termstats is not on PyPI, Homebrew or winget; it is distributed through GitHub Releases and this page.
- The files are not code-signed or notarised: macOS quarantines a browser download and Windows SmartScreen may ask once.
- It watches one machine in one terminal; it is not a monitoring server and keeps no remote history.
- The recommendation uses list prices and benchmark scores from a catalogue shipped with each release; it does not check special requirements such as particular software or ports.

## Links

- Source code: https://github.com/pepperonas/termstats
- Changelog: https://termstats.celox.io/changelog.md
- Licence (MIT): https://github.com/pepperonas/termstats/blob/main/LICENSE
- Support the project: https://www.paypal.com/donate/?business=martin.pfeffer@celox.io&currency_code=EUR&item_name=termstats
- Author: Martin Pfeffer, https://celox.io — Imprint https://celox.io/impressum/ · Privacy https://celox.io/datenschutz/
