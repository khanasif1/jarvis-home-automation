"""Calibrated openWakeWord TFLite ensemble with a lightweight import path."""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path
from typing import Any

from .base import WakewordDetector, WakewordError

logger = logging.getLogger(__name__)

_FEATURE_MODEL_FILES = {
    "embedding": "embedding_model.tflite",
    "melspectrogram": "melspectrogram.tflite",
}
_BUILTIN_MODEL_FILES = (
    "hey_jarvis_v0.1.tflite",
    "jarvis_v2.tflite",
)
_COMPANION_THRESHOLD_RATIO = 2.0 / 3.0
_WARMUP_FRAMES = 5


def _package_directory() -> Path:
    existing = sys.modules.get("openwakeword")
    existing_path = getattr(existing, "__path__", None)
    if existing_path:
        return Path(next(iter(existing_path)))
    spec = importlib.util.find_spec("openwakeword")
    if spec is None or not spec.submodule_search_locations:
        raise WakewordError("openWakeWord 0.6.0 is not installed.")
    return Path(next(iter(spec.submodule_search_locations)))


def validate_runtime(model_path: str | None = None) -> None:
    package_dir = _package_directory()
    required_models = list(_FEATURE_MODEL_FILES.values())
    if model_path is None:
        required_models.extend(_BUILTIN_MODEL_FILES)
    missing = [
        name
        for name in required_models
        if not (package_dir / "resources" / "models" / name).is_file()
    ]
    if missing:
        raise WakewordError(
            "Missing openWakeWord TFLite models: " + ", ".join(sorted(missing))
        )
    if model_path is not None:
        custom_model = Path(model_path)
        if not custom_model.is_file():
            raise WakewordError(f"Custom wake-word model does not exist: {custom_model}")
        if custom_model.suffix.lower() != ".tflite":
            raise WakewordError("Custom wake-word model must be a .tflite file.")
    try:
        import numpy  # noqa: F401
        import tflite_runtime.interpreter  # noqa: F401
    except ImportError as exc:
        raise WakewordError(f"TFLite wake-word dependency is unavailable: {exc}") from exc


def _load_model_class() -> type[Any]:
    """Load model.py without openWakeWord's unused ONNX/training imports."""

    existing = sys.modules.get("openwakeword")
    if existing is not None and hasattr(existing, "get_pretrained_model_paths"):
        from openwakeword.model import Model

        return Model

    package_dir = _package_directory()
    model_dir = package_dir / "resources" / "models"
    package = types.ModuleType("openwakeword")
    package.__file__ = str(package_dir / "__init__.py")
    package.__path__ = [str(package_dir)]
    package.__package__ = "openwakeword"
    package.FEATURE_MODELS = {
        "embedding": {
            "model_path": str(model_dir / _FEATURE_MODEL_FILES["embedding"])
        },
        "melspectrogram": {
            "model_path": str(model_dir / _FEATURE_MODEL_FILES["melspectrogram"])
        },
    }
    package.MODELS = {
        "hey_jarvis": {"model_path": str(model_dir / _BUILTIN_MODEL_FILES[0])},
        "jarvis": {"model_path": str(model_dir / _BUILTIN_MODEL_FILES[1])},
    }
    package.model_class_mappings = {}

    def get_pretrained_model_paths(inference_framework: str = "tflite") -> list[str]:
        suffix = ".tflite" if inference_framework == "tflite" else ".onnx"
        return [
            value["model_path"].replace(".tflite", suffix)
            for value in package.MODELS.values()
        ]

    package.get_pretrained_model_paths = get_pretrained_model_paths
    sys.modules["openwakeword"] = package
    try:
        from openwakeword.model import Model
    except Exception:
        sys.modules.pop("openwakeword", None)
        sys.modules.pop("openwakeword.model", None)
        sys.modules.pop("openwakeword.utils", None)
        raise
    return Model


class OpenWakewordDetector(WakewordDetector):
    def __init__(
        self,
        threshold: float = 0.15,
        model_path: str | None = None,
    ) -> None:
        try:
            validate_runtime(model_path)
            model_class = _load_model_class()
            if model_path is not None:
                selected_models = [model_path]
            else:
                model_dir = _package_directory() / "resources" / "models"
                selected_models = [
                    str(model_dir / name)
                    for name in _BUILTIN_MODEL_FILES
                ]
            self._model = model_class(
                wakeword_models=selected_models,
                inference_framework="tflite",
            )
            import numpy

            self._numpy = numpy
        except WakewordError:
            raise
        except Exception as exc:
            description = model_path or "the built-in Jarvis model ensemble"
            raise WakewordError(
                f"Could not load {description} as a TFLite wake-word model: {exc}"
            ) from exc
        self._threshold = threshold
        self._uses_builtin_ensemble = model_path is None
        self._model_name = (
            Path(model_path).stem
            if model_path
            else "hey_jarvis+jarvis"
        )
        self._warm_up()
        logger.info(
            "Wake-word listener ready model=%s threshold=%.3f",
            self._model_name,
            self._threshold,
        )

    def _warm_up(self) -> None:
        silence = self._numpy.zeros(self.frame_length(), dtype=self._numpy.int16)
        for _ in range(_WARMUP_FRAMES):
            self._model.predict(silence)

    def frame_length(self) -> int:
        return 1_280

    def sample_rate(self) -> int:
        return 16_000

    def process(self, pcm16_chunk: bytes) -> bool:
        if len(pcm16_chunk) != self.frame_length() * 2:
            raise WakewordError(
                f"Wake-word frame must be {self.frame_length() * 2} bytes."
            )
        try:
            predictions = self._model.predict(
                self._numpy.frombuffer(pcm16_chunk, dtype=self._numpy.int16)
            )
        except Exception as exc:
            raise WakewordError(f"Wake-word inference failed: {exc}") from exc
        scored_models = [
            (
                str(name),
                float(score),
                (
                    self._threshold * _COMPANION_THRESHOLD_RATIO
                    if self._uses_builtin_ensemble
                    and Path(str(name)).stem == "jarvis_v2"
                    else self._threshold
                ),
            )
            for name, score in predictions.items()
        ]
        highest_model, highest_score, effective_threshold = max(
            scored_models,
            key=lambda item: item[1] / item[2],
            default=(self._model_name, 0.0, self._threshold),
        )
        detected = highest_score >= effective_threshold
        if detected:
            logger.info(
                "Wake word detected model=%s score=%.3f threshold=%.3f",
                highest_model,
                highest_score,
                effective_threshold,
            )
        else:
            logger.debug(
                "Wake word score model=%s score=%.3f threshold=%.3f",
                highest_model,
                highest_score,
                effective_threshold,
            )
        return detected

    def reset(self) -> None:
        reset = getattr(self._model, "reset", None)
        if callable(reset):
            reset()
            self._warm_up()

    def close(self) -> None:
        self._model = None
