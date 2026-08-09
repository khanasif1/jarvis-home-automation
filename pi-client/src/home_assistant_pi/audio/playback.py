"""Speaker playback built on top of ``sounddevice``.

As with :mod:`home_assistant_pi.audio.capture`, ``sounddevice`` is imported
lazily so the module can be imported (and its logic unit tested) without
PortAudio installed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .wav import WavAudio

logger = logging.getLogger(__name__)


class AudioDeviceError(RuntimeError):
    """Raised when audio playback hardware cannot be used."""


def _sd():
    try:
        import sounddevice as sd  # type: ignore
    except Exception as exc:  # pragma: no cover - exercised via monkeypatch
        raise AudioDeviceError(
            "The 'sounddevice' package (and PortAudio) is required for "
            "audio playback but is not available: " + str(exc)
        ) from exc
    return sd


@dataclass
class OutputDevice:
    """A speaker-capable audio device."""

    index: int
    name: str
    max_output_channels: int
    default_sample_rate: float


def list_output_devices() -> list[OutputDevice]:
    """Return all audio devices that support output (speakers)."""
    sd = _sd()
    devices = []
    for index, info in enumerate(sd.query_devices()):
        if info.get("max_output_channels", 0) > 0:
            devices.append(
                OutputDevice(
                    index=index,
                    name=info.get("name", f"device-{index}"),
                    max_output_channels=info["max_output_channels"],
                    default_sample_rate=info.get("default_samplerate", 0.0),
                )
            )
    return devices


class AudioPlayback:
    """Plays PCM16 WAV audio out of a configured (or default) output device."""

    def __init__(self, device: Optional[str] = None) -> None:
        self.device = device

    def play(self, audio: WavAudio, block: bool = True) -> None:
        """Play ``audio`` synchronously (by default) on the output device.

        Raises:
            AudioDeviceError: if playback hardware is unavailable or the
                stream fails.
        """
        sd = _sd()
        try:
            with sd.RawOutputStream(
                samplerate=audio.sample_rate,
                channels=audio.channels,
                dtype="int16",
                device=self.device,
            ) as stream:
                stream.write(audio.frames)
        except AudioDeviceError:
            raise
        except Exception as exc:  # pragma: no cover - hardware-dependent
            raise AudioDeviceError(f"Speaker playback failed: {exc}") from exc
