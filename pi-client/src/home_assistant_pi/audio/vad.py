"""Simple, dependency-free energy-based voice activity detection (VAD).

This is intentionally a lightweight RMS-threshold detector rather than a
machine-learning VAD: it needs no extra runtime dependency and is more than
sufficient for deciding when a user has stopped speaking after a wake word,
which keeps the Pi release small.
"""

from __future__ import annotations

import array
import math
from dataclasses import dataclass


def rms_energy(pcm16_bytes: bytes) -> float:
    """Compute the root-mean-square energy of a PCM16 little-endian buffer.

    Returns 0.0 for empty input instead of raising, so callers can treat
    silence/empty chunks uniformly.
    """
    if not pcm16_bytes:
        return 0.0
    samples = array.array("h")
    # array module reads native byte order; PCM16 WAV/streams are little
    # endian which matches virtually all Raspberry Pi / x86 hosts. Guard
    # against odd-length buffers (a truncated final sample).
    usable_len = len(pcm16_bytes) - (len(pcm16_bytes) % 2)
    samples.frombytes(pcm16_bytes[:usable_len])
    if len(samples) == 0:
        return 0.0
    total = sum(float(s) * float(s) for s in samples)
    return math.sqrt(total / len(samples))


@dataclass
class VoiceActivityDetector:
    """Tracks consecutive silent chunks to decide when speech has ended.

    Args:
        threshold: RMS energy below which a chunk is considered silence.
        silence_chunks_to_stop: number of consecutive silent chunks required
            before :meth:`process_chunk` reports that speech has ended.
    """

    threshold: float = 300.0
    silence_chunks_to_stop: int = 15

    _silent_streak: int = 0
    _has_heard_speech: bool = False

    def reset(self) -> None:
        self._silent_streak = 0
        self._has_heard_speech = False

    def process_chunk(self, pcm16_bytes: bytes) -> bool:
        """Feed one audio chunk to the detector.

        Returns:
            True once speech has been detected and then followed by enough
            consecutive silent chunks to conclude the utterance is over.
        """
        energy = rms_energy(pcm16_bytes)
        is_speech = energy >= self.threshold

        if is_speech:
            self._has_heard_speech = True
            self._silent_streak = 0
            return False

        if not self._has_heard_speech:
            # Still waiting for the user to start speaking at all.
            return False

        self._silent_streak += 1
        return self._silent_streak >= self.silence_chunks_to_stop
