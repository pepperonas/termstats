"""The hand-over to the native core (0.9.0).

`native/` is the same dashboard as a Rust program. Where it is installed, `termstats` hands
the LIVE dashboard to it and nothing else - and never when the native picture would not be
this picture. These tests pin the conditions and the command line; the native program has
its own suite (`cargo test` in native/).
"""

import pathlib
import re
import sys

import pytest

import termstats
from termstats import cli
from termstats import theme as T

ROOT = pathlib.Path(__file__).resolve().parent.parent
NATIVE = ROOT / "native"


@pytest.fixture
def installed(monkeypatch):
    """A native core on the PATH, a terminal on stdout, and exec recorded instead of done."""
    calls = []

    def execv(path, argv):
        calls.append((path, list(argv)))
        raise SystemExit(0)                    # exec does not return; neither may the fake

    monkeypatch.delenv(cli.NATIVE_ENV, raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/opt/bin/{name}")
    monkeypatch.setattr(cli, "_stdout_is_interactive", lambda: True)
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli.os, "execv", execv)
    return calls


def run_main(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["termstats", *args])
    monkeypatch.setattr(cli, "detect_capabilities", lambda *a, **k: None)
    monkeypatch.setattr(cli, "configure_console", lambda: None)
    with pytest.raises(SystemExit):
        cli.main()


# --- when the dashboard is handed over ------------------------------------------------------

def test_an_installed_core_is_found(installed):
    assert cli.native_core() == "/opt/bin/termstats-native"


@pytest.mark.parametrize("value", ["0", "off", "no", "false", "OFF"])
def test_the_environment_switches_it_off(installed, monkeypatch, value):
    monkeypatch.setenv(cli.NATIVE_ENV, value)
    assert cli.native_core() is None


def test_off_means_off_even_if_a_file_is_called_that(installed, monkeypatch, tmp_path):
    """TERMSTATS_NATIVE=0 is a switch, never a path: an executable named "0" in the current
    directory must not be started because of it."""
    trap = tmp_path / "0"
    trap.write_text("#!/bin/sh\n")
    trap.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(cli.NATIVE_ENV, "0")
    assert cli.native_core() is None


def test_nothing_installed_means_nothing_to_hand_to(installed, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli.native_core() is None


def test_an_explicit_path_wins_over_the_search(installed, monkeypatch, tmp_path):
    binary = tmp_path / "my-core"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv(cli.NATIVE_ENV, str(binary))
    assert cli.native_core() == str(binary)
    monkeypatch.setenv(cli.NATIVE_ENV, str(tmp_path / "missing"))
    assert cli.native_core() is None, "a path that is not there is not silently replaced by the search"


def test_never_on_windows(installed, monkeypatch):
    monkeypatch.setattr(cli, "IS_WINDOWS", True)
    assert cli.native_core() is None


def test_never_without_a_terminal(installed, monkeypatch):
    monkeypatch.setattr(cli, "_stdout_is_interactive", lambda: False)
    assert cli.native_core() is None


def test_a_demo_is_drawn_here(installed):
    """Its numbers are scripted in this process; the native core would show the real machine."""
    from termstats.demo import DemoSource
    cli.set_demo(DemoSource())
    assert cli.native_core() is None


@pytest.mark.parametrize("caps", [
    T.Capabilities(color="truecolor", glyphs="ascii", nerd=False),
    T.Capabilities(color="truecolor", glyphs="block", nerd=False),
    T.Capabilities(color="16", glyphs="braille", nerd=False),
    T.Capabilities(color="truecolor", glyphs="braille", nerd=True),
])
def test_a_picture_the_core_does_not_draw_is_drawn_here(installed, caps):
    cli.CAPS = caps
    assert cli.native_core() is None


@pytest.mark.parametrize("color", ["truecolor", "256", "mono"])
def test_the_colour_depths_the_core_knows(installed, color):
    cli.CAPS = T.Capabilities(color=color, glyphs="braille", nerd=False)
    assert cli.native_core() is not None


# --- the command line it is started with ----------------------------------------------------

def test_the_command_line_carries_what_the_session_was_asked_for(installed, monkeypatch):
    monkeypatch.setattr(cli, "_own_command", lambda: "/usr/local/bin/termstats")
    cli.set_theme("nord", color="truecolor")
    cli.set_frame(compact=True, no_border=False)
    argv = cli.native_argv("/opt/bin/termstats-native", 2.0)
    assert argv == ["/opt/bin/termstats-native", "--live", "--interval", "2", "--theme", "nord", "--compact",
                    "--python", "/usr/local/bin/termstats"]
    cli.set_frame(compact=False, no_border=True)
    assert "--no-border" in cli.native_argv("x", 0.5) and "--compact" not in cli.native_argv("x", 0.5)
    assert cli.native_argv("x", 0.5)[2:4] == ["--interval", "0.5"]


def test_the_microphone_device_travels_behind_the_separator(installed, monkeypatch):
    """The native core passes everything after "--" back when b, d or x is pressed, so a
    session started with --device returns to THAT microphone."""
    monkeypatch.setattr(cli, "_own_command", lambda: "/usr/local/bin/termstats")
    argv = cli.native_argv("x", 0.5, device="MacBook")
    assert argv[-3:] == ["--", "--device", "MacBook"]


def test_without_a_way_back_no_switch_keys_are_offered(installed, monkeypatch):
    monkeypatch.setattr(cli, "_own_command", lambda: None)
    assert "--python" not in cli.native_argv("x", 0.5, device="MacBook")


# --- what main() does with it ---------------------------------------------------------------

def test_a_bare_termstats_becomes_the_native_dashboard(installed, monkeypatch):
    monkeypatch.setattr(cli, "run_live", lambda interval: pytest.fail("the dashboard was drawn here"))
    run_main(monkeypatch)
    assert len(installed) == 1
    path, argv = installed[0]
    assert path == "/opt/bin/termstats-native" and argv[:2] == [path, "--live"]


def test_a_snapshot_is_never_handed_over(installed, monkeypatch):
    done = []
    monkeypatch.setattr(cli, "run_once", lambda: done.append(1))
    monkeypatch.setattr(sys, "argv", ["termstats", "--once"])
    monkeypatch.setattr(cli, "detect_capabilities", lambda *a, **k: None)
    monkeypatch.setattr(cli, "configure_console", lambda: None)
    cli.main()
    assert done == [1] and installed == []


def test_a_microphone_screen_starts_here_and_s_leads_to_the_native_dashboard(installed, monkeypatch):
    """`termstats -bpm` is drawn here; pressing s (the performance view) hands over."""
    monkeypatch.setattr(cli, "_load_audio", lambda: object())
    monkeypatch.setattr(cli, "_mic_source", lambda device: object())
    views = []
    monkeypatch.setattr(cli, "run_audio", lambda mode, interval, source: views.append(mode) or "performance")
    run_main(monkeypatch, "-bpm", "--device", "MacBook")
    assert views == ["bpm"]
    assert len(installed) == 1 and installed[0][1][-3:] == ["--", "--device", "MacBook"]


def test_a_core_that_cannot_be_started_costs_nothing(installed, monkeypatch):
    def broken(path, argv):
        raise OSError("exec format error")
    monkeypatch.setattr(cli.os, "execv", broken)
    drawn = []
    monkeypatch.setattr(cli, "run_live", lambda interval: drawn.append(interval))
    monkeypatch.setattr(sys, "argv", ["termstats"])
    monkeypatch.setattr(cli, "detect_capabilities", lambda *a, **k: None)
    monkeypatch.setattr(cli, "configure_console", lambda: None)
    cli.main()
    assert drawn == [cli.DEFAULT_INTERVAL], "the dashboard is drawn here instead"


def test_the_suite_itself_can_never_exec_the_core():
    """The autouse fixture: with a real core installed, a test that reaches the live loop
    would replace pytest with it."""
    import os
    assert os.environ.get(cli.NATIVE_ENV) == "0" and cli.native_core() is None


# --- the two programs stay one project ------------------------------------------------------

def test_the_native_core_carries_the_package_version():
    cargo = (NATIVE / "Cargo.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', cargo, re.M).group(1) == termstats.__version__


def test_the_native_colour_tables_are_generated_from_the_themes_as_they_are():
    """native/src/themes.rs is written by tools/gen_native_themes.py. A theme edited here
    without re-running it would leave the two dashboards in different colours."""
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import gen_native_themes
    finally:
        sys.path.pop(0)
    assert gen_native_themes.TARGET.read_text(encoding="utf-8") == gen_native_themes.render(), \
        "run: python tools/gen_native_themes.py"


def test_the_native_core_has_one_dependency():
    """libc, and nothing between the program and the kernel."""
    cargo = (NATIVE / "Cargo.toml").read_text(encoding="utf-8")
    deps = cargo.split("[dependencies]")[1].split("[")[0]
    assert [line.split("=")[0].strip() for line in deps.strip().splitlines()] == ["libc"]


def test_the_layout_constants_agree_in_both_programs():
    """The native layout is a port of render_dashboard(); a budget constant changed on one
    side only would make the two pictures drift apart at some terminal size."""
    rust = (NATIVE / "src" / "ui.rs").read_text(encoding="utf-8")

    def const(name):
        return float(re.search(rf"const {name}: \w+ = ([\d.]+);", rust).group(1))

    pairs = {"LABEL_W": T.LABEL_W, "VALUE_W": T.VALUE_W, "RATE_W": T.RATE_W, "DISK_LABEL_W": T.DISK_LABEL_W,
             "MIN_BAR_W": T.MIN_BAR_W, "NOTE_GB_PAIR_W": T.NOTE_GB_PAIR_W, "NOTE_MEM_W": T.NOTE_MEM_W,
             "NOTE_TOTAL_W": T.NOTE_TOTAL_W, "SPARK_W": T.SPARK_W, "PEAK_WINDOW": T.PEAK_WINDOW,
             "SMOOTH_ALPHA": T.SMOOTH_ALPHA, "RIGHT_COL_MIN": T.RIGHT_COL_MIN, "RIGHT_COL_MAX": T.RIGHT_COL_MAX,
             "RIGHT_COL_SHARE": T.RIGHT_COL_SHARE, "NARROW_BELOW": T.NARROW_BELOW, "CHART_MIN_H": T.CHART_MIN_H,
             "CHART_MAX_H": T.CHART_MAX_H, "AXIS_W_PCT": T.AXIS_W_PCT, "AXIS_W_RATE": T.AXIS_W_RATE,
             "CHART_FADE_TOP": T.CHART_FADE_TOP, "HISTORY_LEN": cli.HISTORY_LEN, "PROC_SCAN_S": cli.PROC_SCAN_S,
             "DISK_SCAN_S": cli.DISK_SCAN_S}
    for name, value in pairs.items():
        assert const(name) == float(value), name


def test_the_audio_constants_agree_in_both_programs():
    """The microphone screens in the core are a port of audio.py, motion.py and the audio
    bodies of cli.py; a constant changed on one side only would make the two drift apart."""
    audio = pytest.importorskip("termstats.audio")
    from termstats import motion
    rust = "\n".join((NATIVE / "src" / name).read_text(encoding="utf-8") for name in
                     ("audio/mod.rs", "audio/spectrum.rs", "audio/bpm.rs", "motion.rs", "audio_ui.rs"))

    def const(name):
        found = re.findall(rf"const {name}: \w+ = (-?[\d.]+);", rust)
        assert len(found) == 1, f"{name}: {found}"
        return float(found[0])

    pairs = {
        # audio.py
        "DB_FLOOR": audio.DB_FLOOR, "SPL_OFFSET": audio.SPL_OFFSET, "LOUD_DB": audio.LOUD_DB, "BANDS": audio.BANDS,
        "F_LO": audio.F_LO, "F_HI": audio.F_HI, "BLOCK": audio.BLOCK, "SAMPLE_RATE": audio.SAMPLE_RATE,
        "FFT": audio.FFT, "BASS_LO": audio.BASS_LO, "BASS_HI": audio.BASS_HI, "DB_HISTORY_S": audio.DB_HISTORY_S,
        "ATTACK": audio.ATTACK, "RELEASE": audio.RELEASE, "RANGE_DB": audio.RANGE_DB, "CEIL_ATTACK": audio.CEIL_ATTACK,
        "CEIL_DECAY": audio.CEIL_DECAY, "CEIL_HEADROOM_DB": audio.CEIL_HEADROOM_DB,
        "PEAK_HOLD_BLOCKS": audio.PEAK_HOLD_BLOCKS, "PEAK_FALL": audio.PEAK_FALL,
        "THRESHOLD": audio.THRESHOLD, "AVG_WINDOW_S": audio.AVG_WINDOW_S, "ONSET_REFRACTORY_S": audio.ONSET_REFRACTORY_S,
        "IOI_WINDOW_S": audio.IOI_WINDOW_S, "BPM_MIN": audio.BPM_MIN, "BPM_MAX": audio.BPM_MAX,
        "MIN_ONSETS": audio.MIN_ONSETS, "STALE_RESET_S": audio.STALE_RESET_S, "OCTAVE_SNAP": audio.OCTAVE_SNAP,
        "DISPLAY_AVG_S": audio.DISPLAY_AVG_S, "SF_LAG": audio.SF_LAG, "SF_WIN": audio.SF_WIN,
        "SF_MARGIN": audio.SF_MARGIN,
        # motion.py
        "MAX_DT": motion.MAX_DT, "BEAT_TAU": motion.BEAT_TAU, "ATTACK_TAU": motion.ATTACK_TAU,
        "RELEASE_TAU": motion.RELEASE_TAU, "PUNCH": motion.PUNCH, "TRAIL_G": motion.TRAIL_G,
        "PEAK_HOLD_S": motion.PEAK_HOLD_S, "PEAK_G": motion.PEAK_G, "METER_ATTACK_TAU": motion.METER_ATTACK_TAU,
        "METER_RELEASE_TAU": motion.METER_RELEASE_TAU, "ENV_FLOOR": motion.ENV_FLOOR,
        # cli.py
        "MIX_MIN_COLUMN_W": cli.MIX_MIN_COLUMN_W, "MIX_GAP": cli.MIX_GAP, "BEAT_DOT_ON": cli.BEAT_DOT_ON,
        "AUDIO_READOUT_S": cli.AUDIO_READOUT_S, "EQ_BAR_W": cli.EQ_BAR_W, "EQ_GAP": cli.EQ_GAP,
        "EQ_MIN_COLUMNS": cli.EQ_MIN_COLUMNS, "AUDIO_CHART_MIN_H": cli.AUDIO_CHART_MIN_H,
    }
    for name, value in pairs.items():
        assert const(name) == float(value), name
    labels = re.search(r"const EQ_LABELS: [^=]+= \[(.*?)\];", rust).group(1)
    assert re.findall(r'\(([\d.]+), "(\w+)"\)', labels) == [(f"{f:.1f}", t) for f, t in cli.EQ_LABELS]


# --- record / recommend: commands only the native core has ----------------------------------

@pytest.mark.parametrize("command", ["record", "recommend"])
def test_the_history_commands_become_the_native_program(installed, monkeypatch, command):
    run_main(monkeypatch, command, "--now", "status")
    assert installed == [("/opt/bin/termstats-native", ["/opt/bin/termstats-native", command, "--now", "status"])]


def test_their_help_is_the_native_help(installed, monkeypatch):
    """`termstats record --help` must not print THIS program's help and exit."""
    printed = []
    monkeypatch.setattr(cli, "print_help", lambda: printed.append(1))
    run_main(monkeypatch, "record", "--help")
    assert printed == [] and installed[0][1][1:] == ["record", "--help"]


def test_the_dashboard_switch_does_not_hide_the_history_commands(installed, monkeypatch):
    """TERMSTATS_NATIVE=0 keeps the DASHBOARD in Python. The recorder exists nowhere else."""
    monkeypatch.setenv(cli.NATIVE_ENV, "0")
    assert cli.native_core() is None and cli.native_binary() == "/opt/bin/termstats-native"
    run_main(monkeypatch, "recommend")
    assert len(installed) == 1


def test_without_the_native_core_the_command_says_how_to_get_it(installed, monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(sys, "argv", ["termstats", "record", "install"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 2 and installed == []
    message = capsys.readouterr().err
    assert "termstats-native" in message and "install.sh | sh" in message


def test_on_windows_the_history_commands_say_so(installed, monkeypatch, capsys):
    monkeypatch.setattr(cli, "IS_WINDOWS", True)
    monkeypatch.setattr(sys, "argv", ["termstats", "recommend"])
    with pytest.raises(SystemExit):
        cli.main()
    assert installed == [] and "macOS and Linux only" in capsys.readouterr().err


def test_a_word_that_is_not_a_command_is_still_an_unknown_option(installed, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["termstats", "recorder"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 2 and installed == []
