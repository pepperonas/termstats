"""-eq / -bpm / -db / --device / --list-devices on the command line."""
import sys

import pytest

from termstats import cli


class FakeSource:
    def __init__(self, device=None):
        self.device = device
        self.samplerate = 48000.0
        self.name = "Fake Mic"


@pytest.fixture
def invoke(monkeypatch):
    calls = {}

    def run_audio(mode, interval, source, once=False):
        calls.update(mode=mode, interval=interval, source=source, once=once)

    monkeypatch.setattr(cli, "run_audio", run_audio)
    monkeypatch.setattr(cli, "run_live", lambda interval: calls.update(live=interval))
    monkeypatch.setattr(cli, "run_once", lambda: calls.update(snapshot=True))
    monkeypatch.setattr(cli, "_stdout_is_interactive", lambda: True)
    monkeypatch.setattr(cli, "_mic_source", lambda device: FakeSource(device))

    def go(*argv):
        monkeypatch.setattr(sys, "argv", ["termstats", *argv])
        cli.main()
        return calls
    return go


@pytest.mark.parametrize("flag", ["-eq", "--eq", "--equalizer", "-equalizer"])
def test_equalizer_flags(invoke, flag):
    assert invoke(flag)["mode"] == "eq"


@pytest.mark.parametrize("flag,mode", [("-bpm", "bpm"), ("--bpm", "bpm"), ("-db", "db"), ("--db", "db")])
def test_tempo_and_level_flags(invoke, flag, mode):
    assert invoke(flag)["mode"] == mode


def test_only_one_audio_mode_at_a_time(invoke, capsys):
    with pytest.raises(SystemExit) as e:
        invoke("-eq", "-db")
    assert e.value.code == 2
    assert "one" in capsys.readouterr().err.lower()


def test_audio_modes_refresh_faster_than_the_dashboard(invoke):
    calls = invoke("-eq")
    assert calls["interval"] == cli.AUDIO_INTERVAL < cli.DEFAULT_INTERVAL


def test_interval_flag_still_wins_in_audio_mode(invoke):
    assert invoke("-eq", "-i", "0.1")["interval"] == pytest.approx(0.1)


def test_an_audio_mode_without_demo_opens_the_microphone(invoke):
    calls = invoke("-bpm")
    assert isinstance(calls["source"], FakeSource) and calls["source"].device is None


def test_device_flag_names_the_microphone(invoke):
    assert invoke("-eq", "--device", "USB")["source"].device == "USB"
    assert invoke("-eq", "-d", "MacBook")["source"].device == "MacBook"


def test_device_flag_needs_a_value(invoke, capsys):
    with pytest.raises(SystemExit) as e:
        invoke("-eq", "--device")
    assert e.value.code == 2 and "device" in capsys.readouterr().err


def test_device_without_an_audio_mode_is_an_error(invoke, capsys):
    with pytest.raises(SystemExit) as e:
        invoke("--device", "USB")
    assert e.value.code == 2


def test_demo_audio_needs_no_microphone(invoke):
    pytest.importorskip("numpy")
    from termstats import audio
    calls = invoke("--demo", "-eq")
    assert isinstance(calls["source"], audio.DemoAudio)


def test_once_in_audio_mode_takes_a_short_measurement(invoke):
    assert invoke("-db", "--once")["once"] is True
    assert invoke("-db")["once"] is False


def test_a_pipe_makes_an_audio_mode_a_measurement_too(invoke, monkeypatch):
    monkeypatch.setattr(cli, "_stdout_is_interactive", lambda: False)
    assert invoke("-db")["once"] is True


def test_list_devices_prints_inputs_and_exits(invoke, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_list_devices", lambda: [(1, "MacBook Pro Microphone", 1, 48000.0), (2, "USB Audio", 2, 44100.0)])
    with pytest.raises(SystemExit) as e:
        invoke("--list-devices")
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "MacBook Pro Microphone" in out and "USB Audio" in out and "48000" in out


def test_missing_audio_extra_is_a_clear_error(invoke, monkeypatch, capsys):
    def boom():
        raise ImportError("No module named 'numpy'")
    monkeypatch.setattr(cli, "_load_audio", boom)
    with pytest.raises(SystemExit) as e:
        invoke("-eq")
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "termstats[audio]" in err and "numpy" in err


def test_help_documents_the_audio_modes(capsys):
    cli.print_help()
    text = capsys.readouterr().out
    for flag in ("--equalizer", "--bpm", "--db", "--device", "--list-devices"):
        assert flag in text, flag
    assert "termstats[audio]" in text
