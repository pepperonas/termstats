"""The microphone, for -eq / -bpm / -db.

`sounddevice` (PortAudio) is imported lazily and every failure becomes an `AudioUnavailable`
with a sentence a person can act on. Blocks arrive on PortAudio's thread as mono float32 of
`audio.BLOCK` samples; nothing raised in the consumer may reach that thread.
"""
from typing import Callable, List, Optional, Tuple

from termstats import audio

INSTALL_HINT = "pip install 'termstats[audio]'"


class AudioUnavailable(RuntimeError):
    """No usable microphone path: library, PortAudio or device missing."""


def _import_sounddevice():
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise AudioUnavailable(f"the microphone modes need the audio extra ({INSTALL_HINT}): {exc}") from exc
    except OSError as exc:
        raise AudioUnavailable(_portaudio_message(exc)) from exc
    return sd


def _portaudio_message(exc):
    return (f"PortAudio is not available ({exc}). On Debian/Ubuntu: apt install libportaudio2; "
            f"the macOS and Windows wheels bundle it - reinstall with {INSTALL_HINT}.")


def _devices():
    sd = _import_sounddevice()
    try:
        devices = list(sd.query_devices())
        default = sd.default.device
    except OSError as exc:
        raise AudioUnavailable(_portaudio_message(exc)) from exc
    try:
        default_in = int(default[0])
    except (TypeError, IndexError, ValueError):
        default_in = int(default) if isinstance(default, int) else -1
    return devices, default_in


def list_devices() -> List[Tuple[int, str, int, float]]:
    """(index, name, input channels, default sample rate) for every device that can record."""
    devices, _ = _devices()
    return [(i, d["name"], int(d["max_input_channels"]), float(d["default_samplerate"]))
            for i, d in enumerate(devices) if int(d.get("max_input_channels", 0)) > 0]


def resolve_device(name: Optional[str]) -> int:
    """The device index for a name fragment (case-insensitive), or the system's default input."""
    devices, default_in = _devices()
    inputs = [(i, d) for i, d in enumerate(devices) if int(d.get("max_input_channels", 0)) > 0]
    if not inputs:
        raise AudioUnavailable("no input device found - is a microphone connected?")
    if name is None:
        if any(i == default_in for i, _ in inputs):
            return default_in
        return inputs[0][0]
    wanted = name.lower()
    for i, d in inputs:
        if wanted in d["name"].lower():
            return i
    names = ", ".join(d["name"] for _, d in inputs)
    raise AudioUnavailable(f"no input device matches '{name}'. Inputs: {names}")


class MicSource:
    """Opens the microphone at its own default sample rate and hands mono blocks to a callback."""

    def __init__(self, device: Optional[str] = None):
        devices, _ = _devices()
        self.index = resolve_device(device)
        info = devices[self.index]
        self.name = info["name"]
        self.samplerate = float(info["default_samplerate"])
        self._stream = None

    def start(self, on_block: Callable):
        sd = _import_sounddevice()

        def callback(indata, frames, time_info, status):
            try:
                on_block(indata[:, 0].copy())
            except Exception:
                pass                       # the audio thread must never die on a consumer error

        try:
            self._stream = sd.InputStream(device=self.index, channels=1, samplerate=self.samplerate,
                                          blocksize=audio.BLOCK, dtype="float32", callback=callback)
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise AudioUnavailable(f"could not open '{self.name}': {exc}") from exc

    def stop(self):
        stream, self._stream = self._stream, None
        if stream is None:
            return
        for step in (stream.stop, stream.close):
            try:
                step()
            except Exception:
                pass
