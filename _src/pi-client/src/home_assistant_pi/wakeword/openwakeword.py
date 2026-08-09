"""openWakeWord engine wrapper (open-source, model-file based wake word).

``openwakeword`` is an optional dependency (see the ``[openwakeword]``
extra in ``pyproject.toml``) and is imported lazily.
"""

from __future__ import annotations

from typing import Optional

from .base import WakewordDetector, WakewordError


class OpenWakewordDetector(WakewordDetector):
    """Wake-word detector backed by the openWakeWord library."""

    def __init__(
        self,
        keyword: str = "jarvis",
        sensitivity: float = 0.5,
        model_path: Optional[str] = None,
        sample_rate: int = 16000,
        frame_length: int = 1280,
    ) -> None:
        try:
            from openwakeword.model import Model  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised via monkeypatch
            raise WakewordError(
                "The 'openwakeword' package is required for the "
                "openwakeword wake-word engine but is not installed. "
                "Install the 'home-assistant-pi[openwakeword]' extra."
            ) from exc

        self._keyword = keyword
        self._threshold = sensitivity
        self._sample_rate = sample_rate
        self._frame_length = frame_length
        model_paths = [model_path] if model_path else None
        self._model = Model(wakeword_models=model_paths)

    def frame_length(self) -> int:
        return self._frame_length

    def sample_rate(self) -> int:
        return self._sample_rate

    def process(self, pcm16_chunk: bytes) -> bool:
        import array

        samples = array.array("h")
        samples.frombytes(pcm16_chunk)
        predictions = self._model.predict(list(samples))
        for name, score in predictions.items():
            if self._keyword.lower() in name.lower() and score >= self._threshold:
                return True
        return False

    def close(self) -> None:
        self._model = None
