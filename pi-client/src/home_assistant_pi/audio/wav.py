"""Minimal PCM16 mono WAV file helpers built on the standard library.

No third-party audio libraries are required for reading/writing WAV files,
which keeps the pi-client's dependency footprint (and disk usage) small.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Union


@dataclass
class WavAudio:
    """In-memory PCM16 audio buffer."""

    frames: bytes
    sample_rate: int
    channels: int = 1
    sample_width: int = 2

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate <= 0 or self.channels <= 0 or self.sample_width <= 0:
            return 0.0
        bytes_per_second = self.sample_rate * self.channels * self.sample_width
        if bytes_per_second == 0:
            return 0.0
        return len(self.frames) / bytes_per_second


def write_wav(path: Union[str, Path], audio: WavAudio) -> Path:
    """Write ``audio`` to ``path`` as a standard PCM WAV file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(audio.channels)
        wav_file.setsampwidth(audio.sample_width)
        wav_file.setframerate(audio.sample_rate)
        wav_file.writeframes(audio.frames)
    return path


def read_wav(path: Union[str, Path]) -> WavAudio:
    """Read a PCM WAV file from ``path`` into a :class:`WavAudio`."""
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())
    return WavAudio(
        frames=frames,
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width,
    )


def wav_bytes(audio: WavAudio) -> bytes:
    """Serialize ``audio`` to an in-memory WAV container (bytes)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(audio.channels)
        wav_file.setsampwidth(audio.sample_width)
        wav_file.setframerate(audio.sample_rate)
        wav_file.writeframes(audio.frames)
    return buffer.getvalue()


def from_wav_bytes(data: bytes) -> WavAudio:
    """Parse an in-memory WAV container (bytes) into a :class:`WavAudio`."""
    buffer = io.BytesIO(data)
    with wave.open(buffer, "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())
    return WavAudio(
        frames=frames,
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width,
    )
