"""Common interface implemented by all wake-word detector backends."""

from __future__ import annotations

import abc


class WakewordError(RuntimeError):
    """Raised when a wake-word engine cannot be initialized or used."""


class WakewordDetector(abc.ABC):
    """Abstract base class for wake-word detection engines.

    Implementations are expected to be used as::

        detector = SomeDetector(...)
        try:
            while True:
                chunk = capture.read_chunk()
                if detector.process(chunk):
                    ... wake word detected ...
        finally:
            detector.close()
    """

    @abc.abstractmethod
    def frame_length(self) -> int:
        """Number of PCM16 samples the detector expects per call to ``process``."""
        raise NotImplementedError

    @abc.abstractmethod
    def sample_rate(self) -> int:
        """Sample rate (Hz) required by this detector."""
        raise NotImplementedError

    @abc.abstractmethod
    def process(self, pcm16_chunk: bytes) -> bool:
        """Process one PCM16 audio chunk.

        Returns:
            True if the wake word was detected in this chunk.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release any resources held by the detector. No-op by default."""
        return None

    def __enter__(self) -> "WakewordDetector":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
