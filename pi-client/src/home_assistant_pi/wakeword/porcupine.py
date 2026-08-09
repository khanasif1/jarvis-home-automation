"""Picovoice Porcupine wake-word engine wrapper.

``pvporcupine`` is an optional dependency (see the ``[porcupine]`` extra in
``pyproject.toml``) and is imported lazily so the base package/CLI works
even when it is not installed.
"""

from __future__ import annotations

from typing import Optional

from .base import WakewordDetector, WakewordError


class PorcupineWakewordDetector(WakewordDetector):
    """Wake-word detector backed by the Picovoice Porcupine engine."""

    def __init__(
        self,
        keyword: str = "jarvis",
        sensitivity: float = 0.5,
        access_key: Optional[str] = None,
        model_path: Optional[str] = None,
        keyword_path: Optional[str] = None,
    ) -> None:
        try:
            import pvporcupine  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised via monkeypatch
            raise WakewordError(
                "The 'pvporcupine' package is required for the porcupine "
                "wake-word engine but is not installed. Install the "
                "'home-assistant-pi[porcupine]' extra."
            ) from exc

        if not access_key:
            raise WakewordError(
                "Porcupine requires an access key (HAP_PORCUPINE_ACCESS_KEY)."
            )

        kwargs: dict = {
            "access_key": access_key,
            "sensitivities": [sensitivity],
        }
        if keyword_path:
            kwargs["keyword_paths"] = [keyword_path]
        else:
            kwargs["keywords"] = [keyword]
        if model_path:
            kwargs["model_path"] = model_path

        self._porcupine = pvporcupine.create(**kwargs)

    def frame_length(self) -> int:
        return self._porcupine.frame_length

    def sample_rate(self) -> int:
        return self._porcupine.sample_rate

    def process(self, pcm16_chunk: bytes) -> bool:
        import array

        samples = array.array("h")
        samples.frombytes(pcm16_chunk)
        result = self._porcupine.process(samples)
        return result >= 0

    def close(self) -> None:
        if getattr(self, "_porcupine", None) is not None:
            self._porcupine.delete()
            self._porcupine = None
