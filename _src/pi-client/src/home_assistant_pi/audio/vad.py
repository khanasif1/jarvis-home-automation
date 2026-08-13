"""WebRTC VAD and bounded command-stream termination."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterator
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
        pre_roll_seconds: float = 0.3,
        min_speech_seconds: float = 0.16,
        activity_callback: Callable[[str, dict[str, int | str]], None] | None = None,
    ) -> None:
        if frame_duration_ms not in {10, 20, 30}:
            raise ValueError("WebRTC VAD frames must be 10, 20, or 30 ms.")
        if max_command_seconds > 30.0:
            raise ValueError("Command duration cannot exceed 30 seconds.")
        if min_speech_seconds <= 0:
            raise ValueError("Minimum speech duration must be positive.")
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
        self._pre_roll_limit = max(0, int(pre_roll_seconds * 1000 / frame_duration_ms))
        self._min_speech_frames = max(
            1, int(min_speech_seconds * 1000 / frame_duration_ms)
        )
        self._pre_roll: deque[bytes] = deque(maxlen=self._pre_roll_limit)
        self._speech_candidate: deque[bytes] = deque()
        self._pending: deque[bytes] = deque()
        self._listened_frames = 0
        self._captured_frames = 0
        self._command_frames = 0
        self._silent_frames = 0
        self._heard_speech = False
        self._stop_next = False
        self._activity_callback = activity_callback
        self._completion_emitted = False
        self._speech_frames = 0
        self.no_speech_detected = False
        self._no_speech_deadline = time.monotonic() + no_speech_timeout_seconds

    def __iter__(self) -> "CommandAudioStream":
        return self

    @property
    def captured_bytes(self) -> int:
        return self._captured_frames * self._frame_bytes

    @property
    def captured_duration_ms(self) -> int:
        return self._captured_frames * self._frame_duration_ms

    @property
    def listened_duration_ms(self) -> int:
        return self._listened_frames * self._frame_duration_ms

    def _emit(self, event: str, **details: int | str) -> None:
        if self._activity_callback is not None:
            self._activity_callback(event, details)

    def _complete(self, reason: str) -> None:
        if self._completion_emitted:
            return
        self._completion_emitted = True
        details: dict[str, int | str] = {
            "reason": reason,
            "captured_ms": self.captured_duration_ms,
            "captured_bytes": self.captured_bytes,
            "listened_ms": self.listened_duration_ms,
        }
        if self._heard_speech:
            details["speech_ms"] = self._speech_frames * self._frame_duration_ms
            self._emit("speech_ended", **details)
        else:
            self._emit("capture_ended", **details)

    def _raise_no_speech_timeout(self) -> None:
        self.no_speech_detected = True
        self._complete("no_speech_timeout")
        raise NoSpeechDetected(
            "No command speech began before the activation timeout."
        )

    def __next__(self) -> bytes:
        if self._pending:
            self._captured_frames += 1
            return self._pending.popleft()
        if self._stop_next:
            self._complete("trailing_silence")
            raise StopIteration
        if self._heard_speech and self._command_frames >= self._max_frames:
            self._complete("maximum_duration")
            raise StopIteration

        while True:
            if (
                not self._heard_speech
                and time.monotonic() >= self._no_speech_deadline
            ):
                self._raise_no_speech_timeout()
            try:
                frame = next(self._chunks)
            except StopIteration:
                if not self._heard_speech:
                    self.no_speech_detected = True
                    self._complete("microphone_stream_ended_without_speech")
                    raise NoSpeechDetected(
                        "Microphone stream ended before speech began."
                    )
                self._complete("microphone_stream_ended")
                raise
            if len(frame) != self._frame_bytes:
                raise RuntimeError(
                    f"Expected {self._frame_bytes} PCM bytes per frame, "
                    f"received {len(frame)}."
                )

            self._listened_frames += 1
            is_speech = self._vad.is_speech(frame, self._sample_rate)
            if not self._heard_speech:
                if is_speech:
                    self._speech_candidate.append(frame)
                else:
                    self._speech_candidate.clear()
                    self._pre_roll.append(frame)
                if len(self._speech_candidate) >= self._min_speech_frames:
                    self._heard_speech = True
                    self._speech_frames = len(self._speech_candidate)
                    self._pending.extend(self._pre_roll)
                    self._pending.extend(self._speech_candidate)
                    self._command_frames = len(self._pending)
                    self._pre_roll.clear()
                    self._speech_candidate.clear()
                    self._emit(
                        "speech_started",
                        offset_ms=(
                            self._listened_frames - self._speech_frames
                        )
                        * self._frame_duration_ms,
                    )
                    self._captured_frames += 1
                    return self._pending.popleft()
                if self._listened_frames >= self._no_speech_frames:
                    self._raise_no_speech_timeout()
                continue

            self._command_frames += 1
            if is_speech:
                self._speech_frames += 1
                self._silent_frames = 0
            else:
                self._silent_frames += 1
                if self._silent_frames >= self._silence_frames:
                    self._stop_next = True
            self._captured_frames += 1
            return frame

    def close(self) -> None:
        close = getattr(self._chunks, "close", None)
        try:
            if callable(close):
                close()
        finally:
            self._complete("stream_closed")
