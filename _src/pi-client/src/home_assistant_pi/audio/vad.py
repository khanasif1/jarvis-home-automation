"""WebRTC VAD and bounded command-stream termination."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol


class NoSpeechDetected(RuntimeError):
    """Raised when the user does not begin a command after wake activation."""


class _VadEngine(Protocol):
    def is_speech(self, frame: bytes, sample_rate: int) -> bool: ...


class VoiceActivityDetector:
    def __init__(self, mode: int = 2, engine: _VadEngine | None = None) -> None:
        if mode not in {0, 1, 2, 3}:
            raise ValueError("WebRTC VAD mode must be 0, 1, 2, or 3.")
        if engine is None:
            try:
                import webrtcvad
            except ImportError as exc:
                raise RuntimeError("webrtcvad-wheels is required for command detection") from exc
            engine = webrtcvad.Vad(mode)
        self._engine = engine

    def is_speech(self, frame: bytes, sample_rate: int = 16_000) -> bool:
        return bool(self._engine.is_speech(frame, sample_rate))


class CommandAudioStream(Iterator[bytes]):
    """Yields live PCM frames until silence or the 30-second hard ceiling."""

    def __init__(
        self,
        chunks: Iterator[bytes],
        vad: VoiceActivityDetector,
        *,
        sample_rate: int = 16_000,
        frame_duration_ms: int = 20,
        no_speech_timeout_seconds: float = 3.0,
        silence_timeout_seconds: float = 1.2,
        max_command_seconds: float = 30.0,
    ) -> None:
        if frame_duration_ms not in {10, 20, 30}:
            raise ValueError("WebRTC VAD frames must be 10, 20, or 30 ms.")
        if max_command_seconds > 30.0:
            raise ValueError("Command duration cannot exceed 30 seconds.")
        self._chunks = chunks
        self._vad = vad
        self._sample_rate = sample_rate
        self._frame_duration_ms = frame_duration_ms
        self._frame_bytes = sample_rate * frame_duration_ms // 1000 * 2
        self._no_speech_frames = max(
            1, int(no_speech_timeout_seconds * 1000 / frame_duration_ms)
        )
        self._silence_frames = max(
            1, int(silence_timeout_seconds * 1000 / frame_duration_ms)
        )
        self._max_frames = max(1, int(max_command_seconds * 1000 / frame_duration_ms))
        self._frames = 0
        self._silent_frames = 0
        self._heard_speech = False
        self._stop_next = False
        self.no_speech_detected = False

    def __iter__(self) -> "CommandAudioStream":
        return self

    def __next__(self) -> bytes:
        if self._stop_next or self._frames >= self._max_frames:
            if not self._heard_speech:
                self.no_speech_detected = True
                raise NoSpeechDetected("No command speech was detected.")
            raise StopIteration

        try:
            frame = next(self._chunks)
        except StopIteration:
            if not self._heard_speech:
                self.no_speech_detected = True
                raise NoSpeechDetected("Microphone stream ended before speech began.")
            raise
        if len(frame) != self._frame_bytes:
            raise RuntimeError(
                f"Expected {self._frame_bytes} PCM bytes per frame, received {len(frame)}."
            )

        self._frames += 1
        if self._vad.is_speech(frame, self._sample_rate):
            self._heard_speech = True
            self._silent_frames = 0
        elif self._heard_speech:
            self._silent_frames += 1
            if self._silent_frames >= self._silence_frames:
                self._stop_next = True
        elif self._frames >= self._no_speech_frames:
            self.no_speech_detected = True
            raise NoSpeechDetected("No command speech began before the activation timeout.")

        return frame

    def close(self) -> None:
        close = getattr(self._chunks, "close", None)
        if callable(close):
            close()
