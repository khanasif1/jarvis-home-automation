"""Tests for home_assistant_pi.audio.vad."""

from __future__ import annotations

import struct

from home_assistant_pi.audio.vad import VoiceActivityDetector, rms_energy


def _silence(n_samples: int = 160) -> bytes:
    return b"\x00\x00" * n_samples


def _loud(n_samples: int = 160, amplitude: int = 20000) -> bytes:
    return b"".join(struct.pack("<h", amplitude) for _ in range(n_samples))


def test_rms_energy_of_silence_is_zero():
    assert rms_energy(_silence()) == 0.0


def test_rms_energy_of_empty_bytes_is_zero():
    assert rms_energy(b"") == 0.0


def test_rms_energy_of_constant_tone():
    amplitude = 1000
    energy = rms_energy(_loud(160, amplitude))
    assert abs(energy - amplitude) < 1e-6


def test_rms_energy_ignores_trailing_odd_byte():
    # One full 16-bit sample plus a stray trailing byte.
    data = struct.pack("<h", 500) + b"\x01"
    assert abs(rms_energy(data) - 500) < 1e-6


def test_vad_waits_for_speech_before_counting_silence():
    vad = VoiceActivityDetector(threshold=300.0, silence_chunks_to_stop=3)
    # Pure silence chunks before any speech must never end the utterance.
    for _ in range(10):
        assert vad.process_chunk(_silence()) is False


def test_vad_detects_end_of_speech_after_silence_streak():
    vad = VoiceActivityDetector(threshold=300.0, silence_chunks_to_stop=3)
    assert vad.process_chunk(_loud()) is False  # speech chunk
    assert vad.process_chunk(_silence()) is False  # silence 1
    assert vad.process_chunk(_silence()) is False  # silence 2
    assert vad.process_chunk(_silence()) is True  # silence 3 -> stop


def test_vad_resets_silence_streak_on_new_speech():
    vad = VoiceActivityDetector(threshold=300.0, silence_chunks_to_stop=2)
    vad.process_chunk(_loud())
    vad.process_chunk(_silence())  # 1 silent chunk
    vad.process_chunk(_loud())  # speech again resets streak
    assert vad.process_chunk(_silence()) is False  # only 1 silent chunk again


def test_vad_reset_clears_state():
    vad = VoiceActivityDetector(threshold=300.0, silence_chunks_to_stop=1)
    vad.process_chunk(_loud())
    vad.reset()
    # After reset, silence alone (with no speech yet) must not end the turn.
    assert vad.process_chunk(_silence()) is False
