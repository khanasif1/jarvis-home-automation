"""Microphone capture built on top of ``sounddevice``.

``sounddevice`` (and the PortAudio native library it wraps) is only
imported lazily, inside functions, so that importing this module never
fails on a machine without PortAudio installed (e.g. a CI runner). This
also makes the module easy to unit test by monkeypatching :func:`_sd`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from .vad import VoiceActivityDetector
from .wav import WavAudio

logger = logging.getLogger(__name__)


class AudioDeviceError(RuntimeError):
    """Raised when audio capture hardware cannot be used."""


def _sd():
    """Import and return the ``sounddevice`` module.

    Raises:
        AudioDeviceError: if the ``sounddevice`` package (or its native
            PortAudio dependency) is not available.
    """
    try:
        import sounddevice as sd  # type: ignore
    except Exception as exc:  # pragma: no cover - exercised via monkeypatch
        raise AudioDeviceError(
            "The 'sounddevice' package (and PortAudio) is required for "
            "microphone capture but is not available: " + str(exc)
        ) from exc
    return sd


@dataclass
class InputDevice:
    """A microphone-capable audio device."""

    index: int
    name: str
    max_input_channels: int
    default_sample_rate: float


def list_input_devices() -> list[InputDevice]:
    """Return all audio devices that support input (microphones)."""
    sd = _sd()
    devices = []
    for index, info in enumerate(sd.query_devices()):
        if info.get("max_input_channels", 0) > 0:
            devices.append(
                InputDevice(
                    index=index,
                    name=info.get("name", f"device-{index}"),
                    max_input_channels=info["max_input_channels"],
                    default_sample_rate=info.get("default_samplerate", 0.0),
                )
            )
    return devices


class AudioCapture:
    """Records microphone audio, optionally gated by a VAD.

    This class only holds configuration; the actual PortAudio stream is
    opened per-call so that a capture failure (device unplugged, busy,
    etc.) never leaves a stale stream handle around.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        device: Optional[str] = None,
        chunk_frames: int = 320,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device
        self.chunk_frames = chunk_frames

    def stream_chunks(self, frame_length: int):
        """Yield PCM16 chunks of ``frame_length`` samples forever.

        Intended for feeding a wake-word engine that needs continuous audio.
        The caller is responsible for breaking out of iteration (e.g. via
        ``break``) to close the underlying stream.
        """
        sd = _sd()
        try:
            with sd.RawInputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                blocksize=frame_length,
                device=self.device,
            ) as stream:
                while True:
                    data, _overflowed = stream.read(frame_length)
                    yield bytes(data)
        except AudioDeviceError:
            raise
        except Exception as exc:  # pragma: no cover - hardware-dependent
            raise AudioDeviceError(f"Microphone streaming failed: {exc}") from exc

    def record_utterance(
        self,
        vad: Optional[VoiceActivityDetector] = None,
        max_seconds: float = 15.0,
        on_chunk: Optional[Callable[[bytes], None]] = None,
    ) -> WavAudio:
        """Record audio until the VAD detects the end of speech or a timeout.

        Args:
            vad: Voice activity detector used to decide when to stop. If
                omitted, a fresh default detector is created.
            max_seconds: Hard ceiling on recording duration regardless of
                VAD state, to guarantee this method always returns.
            on_chunk: Optional callback invoked with each raw PCM16 chunk
                (useful for tests/dev tools that want to inspect audio as
                it streams in).

        Returns:
            The recorded utterance as a :class:`WavAudio`.
        """
        sd = _sd()
        vad = vad or VoiceActivityDetector()
        vad.reset()

        max_chunks = max(1, int((max_seconds * self.sample_rate) / self.chunk_frames))
        collected = bytearray()

        try:
            with sd.RawInputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                blocksize=self.chunk_frames,
                device=self.device,
            ) as stream:
                for _ in range(max_chunks):
                    data, overflowed = stream.read(self.chunk_frames)
                    chunk_bytes = bytes(data)
                    collected.extend(chunk_bytes)
                    if on_chunk is not None:
                        on_chunk(chunk_bytes)
                    if vad.process_chunk(chunk_bytes):
                        break
        except AudioDeviceError:
            raise
        except Exception as exc:  # pragma: no cover - hardware-dependent
            raise AudioDeviceError(f"Microphone capture failed: {exc}") from exc

        return WavAudio(
            frames=bytes(collected),
            sample_rate=self.sample_rate,
            channels=self.channels,
            sample_width=2,
        )
