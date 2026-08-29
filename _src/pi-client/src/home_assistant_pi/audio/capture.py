"""Buffered PortAudio capture with optional speech enhancement."""

from __future__ import annotations

import logging
import math
import queue
import threading
from dataclasses import dataclass
from typing import Iterator

import numpy

from .devices import (
    AudioDevice as InputDevice,
    AudioDeviceError,
    SelectedAudioDevice,
    get_sounddevice,
    list_audio_devices,
    resolve_audio_device,
)
from .enhancement import SpeexPreprocessor

logger = logging.getLogger(__name__)
_CAPTURE_FRAME_MS = 20
_QUEUE_SECONDS = 2
_STREAM_CLOSED = object()


def list_input_devices() -> list[InputDevice]:
    return list_audio_devices("input")


def resolve_input_device(
    device: str | None = None,
    sample_rate: int = 16_000,
) -> SelectedAudioDevice:
    return resolve_audio_device("input", device, sample_rate=sample_rate)


def _dbfs(root_mean_square: float) -> float:
    if root_mean_square <= 0:
        return -96.0
    return max(-96.0, 20.0 * math.log10(root_mean_square / 32_768.0))


@dataclass(frozen=True)
class CaptureStats:
    duration_ms: int
    raw_rms_dbfs: float
    processed_rms_dbfs: float
    peak_dbfs: float
    clipped_samples: int
    input_overflows: int
    dropped_frames: int
    enhanced: bool


class _LevelMeter:
    def __init__(self) -> None:
        self.samples = 0
        self.square_sum = 0.0
        self.peak = 0
        self.clipped = 0

    def add(self, frame: bytes) -> None:
        samples = numpy.frombuffer(frame, dtype=numpy.int16).astype(
            numpy.int32,
            copy=False,
        )
        if samples.size == 0:
            return
        absolute = numpy.abs(samples)
        self.samples += int(samples.size)
        self.square_sum += float(
            numpy.dot(samples.astype(numpy.float64), samples)
        )
        self.peak = max(self.peak, int(absolute.max()))
        self.clipped += int(numpy.count_nonzero(absolute >= 32_700))

    @property
    def rms_dbfs(self) -> float:
        rms = math.sqrt(self.square_sum / self.samples) if self.samples else 0.0
        return _dbfs(rms)

    @property
    def peak_dbfs(self) -> float:
        return _dbfs(float(self.peak))


class _MutableCaptureStats:
    def __init__(self, enhanced: bool) -> None:
        self.enhanced = enhanced
        self.frames = 0
        self.input_overflows = 0
        self.dropped_frames = 0
        self.raw = _LevelMeter()
        self.processed = _LevelMeter()

    def freeze(self) -> CaptureStats:
        return CaptureStats(
            duration_ms=self.frames * _CAPTURE_FRAME_MS,
            raw_rms_dbfs=self.raw.rms_dbfs,
            processed_rms_dbfs=self.processed.rms_dbfs,
            peak_dbfs=self.processed.peak_dbfs,
            clipped_samples=self.processed.clipped,
            input_overflows=self.input_overflows,
            dropped_frames=self.dropped_frames,
            enhanced=self.enhanced,
        )


class AudioCapture:
    def __init__(
        self,
        device: str | None = None,
        sample_rate: int = 16_000,
        *,
        enable_enhancement: bool = True,
    ) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.enable_enhancement = enable_enhancement
        self._selected_device: SelectedAudioDevice | None = None
        self._capture_frame_length = sample_rate * _CAPTURE_FRAME_MS // 1_000
        self._queue: queue.Queue[bytes | object] = queue.Queue(
            maxsize=max(1, _QUEUE_SECONDS * 1_000 // _CAPTURE_FRAME_MS)
        )
        self._stream = None
        self._accepting = threading.Event()
        self._consumer_lock = threading.Lock()
        self._active_stats: _MutableCaptureStats | None = None
        self.last_stats: CaptureStats | None = None

    @property
    def selected_device(self) -> SelectedAudioDevice | None:
        return self._selected_device

    def _callback(self, indata, frames, time_info, status) -> None:
        del time_info
        if not self._accepting.is_set():
            return
        stats = self._active_stats
        if stats is not None and getattr(status, "input_overflow", False):
            stats.input_overflows += 1
        if frames != self._capture_frame_length:
            if stats is not None:
                stats.dropped_frames += 1
            return
        frame = bytes(indata)
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            if stats is not None:
                stats.dropped_frames += 1
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(frame)
            except queue.Full:
                if stats is not None:
                    stats.dropped_frames += 1

    def _start(self) -> None:
        if self._stream is not None:
            return
        sounddevice = get_sounddevice()
        stream = None
        try:
            self._selected_device = resolve_input_device(
                self.device,
                self.sample_rate,
            )
            logger.info(
                "Using microphone device %s (%s)",
                self._selected_device.value,
                self._selected_device.name,
            )
            stream = sounddevice.RawInputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=self._capture_frame_length,
                device=self._selected_device.value,
                callback=self._callback,
            )
            stream.start()
            self._stream = stream
        except AudioDeviceError:
            raise
        except Exception as exc:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    logger.exception("Could not close failed microphone stream")
            raise AudioDeviceError(f"Microphone capture failed: {exc}") from exc

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def _next_frame(self) -> bytes:
        while True:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                if self._stream is None or not self._stream.active:
                    raise AudioDeviceError("Microphone stream stopped unexpectedly.")
                continue
            if item is _STREAM_CLOSED:
                raise AudioDeviceError("Microphone stream was closed.")
            return item

    def stream_chunks(
        self,
        frame_length: int,
        *,
        enhance: bool | None = None,
    ) -> Iterator[bytes]:
        if (
            frame_length <= 0
            or frame_length % self._capture_frame_length != 0
        ):
            raise ValueError(
                "Microphone frame length must be a positive multiple of "
                f"{self._capture_frame_length} samples."
            )
        return self._stream_chunks(
            frame_length,
            self.enable_enhancement if enhance is None else enhance,
        )

    def _stream_chunks(
        self,
        frame_length: int,
        enhance: bool,
    ) -> Iterator[bytes]:
        if not self._consumer_lock.acquire(blocking=False):
            raise AudioDeviceError("Microphone already has an active consumer.")
        processor: SpeexPreprocessor | None = None
        stats = _MutableCaptureStats(enhanced=enhance)
        try:
            self._start()
            if enhance:
                processor = SpeexPreprocessor(
                    self._capture_frame_length,
                    self.sample_rate,
                )
            self._drain_queue()
            self._active_stats = stats
            self._accepting.set()
            requested_bytes = frame_length * 2
            pending = bytearray()
            while True:
                raw = self._next_frame()
                stats.raw.add(raw)
                processed = processor.process(raw) if processor is not None else raw
                stats.processed.add(processed)
                stats.frames += 1
                pending.extend(processed)
                if len(pending) >= requested_bytes:
                    yield bytes(pending[:requested_bytes])
                    del pending[:requested_bytes]
        finally:
            self._accepting.clear()
            self._active_stats = None
            self._drain_queue()
            if processor is not None:
                processor.close()
            self.last_stats = stats.freeze()
            self._consumer_lock.release()
            if stats.input_overflows or stats.dropped_frames:
                logger.warning(
                    "Microphone buffering reported input_overflows=%s "
                    "dropped_frames=%s",
                    stats.input_overflows,
                    stats.dropped_frames,
                )

    def close(self) -> None:
        self._accepting.clear()
        self._drain_queue()
        try:
            self._queue.put_nowait(_STREAM_CLOSED)
        except queue.Full:
            pass
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
