"""Incremental raw PCM speaker playback using PortAudio."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .wav import PcmAudio


class AudioDeviceError(RuntimeError):
    """Raised when speaker hardware cannot be opened or written."""


def _sounddevice():
    try:
        import sounddevice
    except Exception as exc:
        raise AudioDeviceError(f"sounddevice/PortAudio is unavailable: {exc}") from exc
    return sounddevice


@dataclass(frozen=True)
class OutputDevice:
    index: int
    name: str


def list_output_devices() -> list[OutputDevice]:
    return [
        OutputDevice(index=index, name=str(info.get("name", index)))
        for index, info in enumerate(_sounddevice().query_devices())
        if info.get("max_output_channels", 0) > 0
    ]


class AudioPlayback:
    def __init__(self, device: str | None = None) -> None:
        self.device = int(device) if device and device.isdigit() else device

    def play(self, audio: PcmAudio) -> None:
        self.play_stream([audio.frames], sample_rate=audio.sample_rate)

    def play_stream(self, chunks: Iterable[bytes], *, sample_rate: int) -> None:
        sounddevice = _sounddevice()
        try:
            with sounddevice.RawOutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                device=self.device,
            ) as stream:
                pending = b""
                for chunk in chunks:
                    data = pending + chunk
                    usable = len(data) - (len(data) % 2)
                    if usable:
                        stream.write(data[:usable])
                    pending = data[usable:]
                if pending:
                    raise AudioDeviceError("Response PCM ended with an incomplete sample.")
        except AudioDeviceError:
            raise
        except Exception as exc:
            raise AudioDeviceError(f"Speaker playback failed: {exc}") from exc
