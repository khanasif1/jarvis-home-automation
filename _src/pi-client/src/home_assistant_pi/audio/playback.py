"""Incremental raw PCM speaker playback using PortAudio."""

from __future__ import annotations

from collections.abc import Iterable

from .devices import (
    AudioDevice as OutputDevice,
    AudioDeviceError,
    SelectedAudioDevice,
    get_sounddevice,
    list_audio_devices,
    resolve_audio_device,
)
from .wav import PcmAudio


def list_output_devices() -> list[OutputDevice]:
    return list_audio_devices("output")


def resolve_output_device(
    device: str | None = None,
    sample_rate: int = 24_000,
) -> SelectedAudioDevice:
    return resolve_audio_device("output", device, sample_rate=sample_rate)


class AudioPlayback:
    def __init__(self, device: str | None = None) -> None:
        self.device = device
        self._selected_device: SelectedAudioDevice | None = None

    def play(self, audio: PcmAudio) -> None:
        self.play_stream([audio.frames], sample_rate=audio.sample_rate)

    def play_stream(self, chunks: Iterable[bytes], *, sample_rate: int) -> None:
        sounddevice = get_sounddevice()
        try:
            if self._selected_device is None:
                self._selected_device = resolve_output_device(
                    self.device,
                    sample_rate,
                )
            with sounddevice.RawOutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                device=self._selected_device.value,
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
