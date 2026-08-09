"""Tests for home_assistant_pi.audio.wav."""

from __future__ import annotations

import struct
from pathlib import Path

from home_assistant_pi.audio.wav import (
    WavAudio,
    from_wav_bytes,
    read_wav,
    wav_bytes,
    write_wav,
)


def _tone_frames(n_samples: int, amplitude: int = 1000) -> bytes:
    return b"".join(struct.pack("<h", amplitude) for _ in range(n_samples))


def test_write_and_read_wav_roundtrip(artifacts_dir: Path):
    frames = _tone_frames(1600)
    audio = WavAudio(frames=frames, sample_rate=16000, channels=1, sample_width=2)
    path = artifacts_dir / "roundtrip.wav"

    write_wav(path, audio)
    assert path.exists()

    loaded = read_wav(path)
    assert loaded.frames == frames
    assert loaded.sample_rate == 16000
    assert loaded.channels == 1
    assert loaded.sample_width == 2


def test_wav_bytes_roundtrip():
    frames = _tone_frames(800)
    audio = WavAudio(frames=frames, sample_rate=8000, channels=1, sample_width=2)
    data = wav_bytes(audio)
    assert data[:4] == b"RIFF"

    loaded = from_wav_bytes(data)
    assert loaded.frames == frames
    assert loaded.sample_rate == 8000


def test_duration_seconds():
    # 16000 Hz, mono, 16-bit -> 32000 bytes/sec
    audio = WavAudio(frames=b"\x00\x00" * 16000, sample_rate=16000)
    assert abs(audio.duration_seconds - 1.0) < 1e-9


def test_duration_seconds_handles_zero_rate_safely():
    audio = WavAudio(frames=b"\x00\x00", sample_rate=0)
    assert audio.duration_seconds == 0.0


def test_write_wav_creates_parent_directories(artifacts_dir: Path):
    nested = artifacts_dir / "nested" / "dir" / "clip.wav"
    audio = WavAudio(frames=_tone_frames(10), sample_rate=16000)
    write_wav(nested, audio)
    assert nested.exists()
