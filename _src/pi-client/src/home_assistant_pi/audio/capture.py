"""Low-overhead raw PCM microphone capture using PortAudio."""

from __future__ import annotations

import logging

from .devices import (
    AudioDevice as InputDevice,
    AudioDeviceError,
    SelectedAudioDevice,
    get_sounddevice,
    list_audio_devices,
    resolve_audio_device,
)

logger = logging.getLogger(__name__)


def list_input_devices() -> list[InputDevice]:
    return list_audio_devices("input")


def resolve_input_device(
    device: str | None = None,
    sample_rate: int = 16_000,
) -> SelectedAudioDevice:
    return resolve_audio_device("input", device, sample_rate=sample_rate)


class AudioCapture:
    def __init__(self, device: str | None = None, sample_rate: int = 16_000) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self._selected_device: SelectedAudioDevice | None = None

    def stream_chunks(self, frame_length: int):
        sounddevice = get_sounddevice()
        try:
            if self._selected_device is None:
                self._selected_device = resolve_input_device(
                    self.device,
                    self.sample_rate,
                )
                logger.info(
                    "Using microphone device %s (%s)",
                    self._selected_device.value,
                    self._selected_device.name,
                )
            with sounddevice.RawInputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=frame_length,
                device=self._selected_device.value,
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
