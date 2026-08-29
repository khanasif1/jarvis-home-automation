"""Low-overhead SpeexDSP noise suppression and automatic gain control."""

from __future__ import annotations

import ctypes
import ctypes.util
from typing import Any

_SET_DENOISE = 0
_SET_AGC = 2
_SET_NOISE_SUPPRESS = 18
_SET_AGC_INCREMENT = 26
_SET_AGC_DECREMENT = 28
_SET_AGC_MAX_GAIN = 30
_SET_AGC_TARGET = 46


class AudioEnhancementError(RuntimeError):
    """Raised when microphone enhancement cannot be initialized or applied."""


def _load_library() -> Any:
    discovered = ctypes.util.find_library("speexdsp")
    candidates = [name for name in (discovered, "libspeexdsp.so.1") if name]
    failures: list[str] = []
    for name in dict.fromkeys(candidates):
        try:
            return ctypes.CDLL(name)
        except OSError as exc:
            failures.append(f"{name}: {exc}")
    detail = "; ".join(failures) or "library was not found"
    raise AudioEnhancementError(
        "SpeexDSP is unavailable. Install the libspeexdsp1 package "
        f"({detail})."
    )


def _configure_signatures(library: Any) -> None:
    library.speex_preprocess_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
    library.speex_preprocess_state_init.restype = ctypes.c_void_p
    library.speex_preprocess_state_destroy.argtypes = [ctypes.c_void_p]
    library.speex_preprocess_state_destroy.restype = None
    library.speex_preprocess_run.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int16),
    ]
    library.speex_preprocess_run.restype = ctypes.c_int
    library.speex_preprocess_ctl.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
    ]
    library.speex_preprocess_ctl.restype = ctypes.c_int


class SpeexPreprocessor:
    """Process fixed-size PCM16 frames through one SpeexDSP state."""

    def __init__(
        self,
        frame_size: int,
        sample_rate: int = 16_000,
        *,
        library: Any | None = None,
    ) -> None:
        if frame_size <= 0:
            raise ValueError("SpeexDSP frame_size must be positive.")
        if sample_rate <= 0:
            raise ValueError("SpeexDSP sample_rate must be positive.")

        self.frame_size = frame_size
        self.sample_rate = sample_rate
        self._library = library or _load_library()
        _configure_signatures(self._library)
        self._state = self._library.speex_preprocess_state_init(
            frame_size,
            sample_rate,
        )
        if not self._state:
            raise AudioEnhancementError("SpeexDSP could not allocate preprocessing state.")

        try:
            self._set_int(_SET_DENOISE, 1)
            self._set_int(_SET_NOISE_SUPPRESS, -25)
            self._set_int(_SET_AGC, 1)
            self._set_int(_SET_AGC_TARGET, 8_000)
            self._set_int(_SET_AGC_MAX_GAIN, 20)
            self._set_int(_SET_AGC_INCREMENT, 12)
            self._set_int(_SET_AGC_DECREMENT, -40)
        except Exception:
            self.close()
            raise

    def _set_int(self, request: int, value: int) -> None:
        setting = ctypes.c_int32(value)
        result = self._library.speex_preprocess_ctl(
            self._state,
            request,
            ctypes.byref(setting),
        )
        if result != 0:
            raise AudioEnhancementError(
                f"SpeexDSP rejected preprocessing setting {request}."
            )

    def process(self, frame: bytes) -> bytes:
        expected_bytes = self.frame_size * ctypes.sizeof(ctypes.c_int16)
        if len(frame) != expected_bytes:
            raise AudioEnhancementError(
                f"SpeexDSP expected {expected_bytes} PCM bytes, received {len(frame)}."
            )
        if self._state is None:
            raise AudioEnhancementError("SpeexDSP preprocessing state is closed.")

        samples_type = ctypes.c_int16 * self.frame_size
        samples = samples_type.from_buffer_copy(frame)
        self._library.speex_preprocess_run(self._state, samples)
        return bytes(samples)

    def close(self) -> None:
        state, self._state = self._state, None
        if state is not None:
            self._library.speex_preprocess_state_destroy(state)

    def __enter__(self) -> "SpeexPreprocessor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def validate_runtime() -> None:
    with SpeexPreprocessor(frame_size=320) as processor:
        processor.process(bytes(640))
