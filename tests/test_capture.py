"""termstats.capture - the microphone, behind a fake sounddevice so no device is touched."""
import sys
import types

import pytest

np = pytest.importorskip("numpy")

from termstats import audio  # noqa: E402

DEVICES = [
    {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000.0},
    {"name": "MacBook Pro Microphone", "max_input_channels": 1, "default_samplerate": 48000.0},
    {"name": "USB Audio Device", "max_input_channels": 2, "default_samplerate": 44100.0},
]


class FakeStream:
    instances = []

    def __init__(self, **kw):
        self.kw = kw
        self.started = self.closed = False
        FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


@pytest.fixture
def capture(monkeypatch):
    FakeStream.instances.clear()
    fake = types.SimpleNamespace(InputStream=FakeStream, query_devices=lambda: list(DEVICES),
                                 default=types.SimpleNamespace(device=[2, 0]))   # the default is NOT the first input
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    from termstats import capture as cap
    return cap


def test_list_devices_reports_only_inputs(capture):
    assert capture.list_devices() == [(1, "MacBook Pro Microphone", 1, 48000.0), (2, "USB Audio Device", 2, 44100.0)]


def test_resolve_device_by_case_insensitive_substring(capture):
    assert capture.resolve_device("usb") == 2
    assert capture.resolve_device("macbook") == 1


def test_resolve_device_defaults_to_the_system_input_not_the_first(capture):
    assert capture.resolve_device(None) == 2


def test_resolve_device_names_the_candidates_when_nothing_matches(capture):
    with pytest.raises(capture.AudioUnavailable) as e:
        capture.resolve_device("bluetooth")
    assert "USB Audio Device" in str(e.value) and "MacBook Pro Microphone" in str(e.value)


def test_mic_source_takes_the_devices_own_sample_rate(capture):
    assert capture.MicSource("usb").samplerate == 44100.0
    assert capture.MicSource(None).samplerate == 44100.0       # the default input's rate, not the first's
    assert capture.MicSource("macbook").samplerate == 48000.0
    assert capture.MicSource("usb").name == "USB Audio Device"


def test_mic_source_opens_a_mono_float_stream_of_one_block(capture):
    src = capture.MicSource("usb")
    got = []
    src.start(lambda block: got.append(block))
    stream = FakeStream.instances[-1]
    assert stream.started
    assert stream.kw["channels"] == 1 and stream.kw["dtype"] == "float32"
    assert stream.kw["blocksize"] == audio.BLOCK and stream.kw["samplerate"] == 44100.0
    assert stream.kw["device"] == 2
    stream.kw["callback"](np.ones((audio.BLOCK, 1), dtype=np.float32), audio.BLOCK, None, None)
    assert len(got) == 1 and got[0].shape == (audio.BLOCK,) and got[0].dtype == np.float32


def test_mic_source_stop_closes_the_stream_and_is_idempotent(capture):
    src = capture.MicSource("usb")
    src.start(lambda block: None)
    stream = FakeStream.instances[-1]
    src.stop(); src.stop()
    assert stream.closed and not stream.started


def test_callback_errors_never_reach_the_audio_thread(capture):
    src = capture.MicSource("usb")
    src.start(lambda block: 1 / 0)
    stream = FakeStream.instances[-1]
    stream.kw["callback"](np.zeros((audio.BLOCK, 1), dtype=np.float32), audio.BLOCK, None, None)   # must not raise


def test_missing_sounddevice_is_explained(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)          # import raises ImportError
    from termstats import capture as cap
    with pytest.raises(cap.AudioUnavailable) as e:
        cap.MicSource(None)
    assert "termstats[audio]" in str(e.value)


def test_missing_portaudio_is_explained(monkeypatch):
    class Broken(types.ModuleType):
        def __getattr__(self, name):
            raise OSError("PortAudio library not found")
    monkeypatch.setitem(sys.modules, "sounddevice", Broken("sounddevice"))
    from termstats import capture as cap
    monkeypatch.setattr(cap, "_import_sounddevice", cap._import_sounddevice)
    with pytest.raises(cap.AudioUnavailable) as e:
        cap.list_devices()
    assert "PortAudio" in str(e.value)
