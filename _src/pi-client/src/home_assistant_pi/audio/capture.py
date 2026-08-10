"""Low-overhead raw PCM microphone capture using PortAudio."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class AudioDeviceError(RuntimeError):
    """Raised when microphone hardware cannot be opened or read."""


def _sounddevice():
    try:
        import sounddevice
    except Exception as exc:
        raise AudioDeviceError(f"sounddevice/PortAudio is unavailable: {exc}") from exc
    return sounddevice


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str


def list_input_devices() -> list[InputDevice]:
    return [
        InputDevice(index=index, name=str(info.get("name", index)))
        for index, info in enumerate(_sounddevice().query_devices())
        if info.get("max_input_channels", 0) > 0
    ]


class AudioCapture:
    def __init__(self, device: str | None = None, sample_rate: int = 16_000) -> None:
        self.device = int(device) if device and device.isdigit() else device
        self.sample_rate = sample_rate

    def stream_chunks(self, frame_length: int):
        sounddevice = _sounddevice()
        try:
            with sounddevice.RawInputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=frame_length,
                device=self.device,
            ) as stream:
                while True:
                    data, overflowed = stream.read(frame_length)
                    if overflowed:
                        logger.warning("Microphone input overflow; audio may be incomplete")
                    yield bytes(data)
        except AudioDeviceError:
            raise
        except Exception as exc:
            raise AudioDeviceError(f"Microphone capture failed: {exc}") from exc
