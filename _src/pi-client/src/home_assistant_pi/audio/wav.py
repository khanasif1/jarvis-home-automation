"""Read-only WAV support for the three bundled notification sounds."""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PcmAudio:
    frames: bytes
    sample_rate: int


def read_wav(path: str | Path) -> PcmAudio:
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError(f"{path} must contain mono PCM16 audio.")
        return PcmAudio(
            frames=source.readframes(source.getnframes()),
            sample_rate=source.getframerate(),
        )
