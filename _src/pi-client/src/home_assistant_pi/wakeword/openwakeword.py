"""Single-model openWakeWord TFLite detector with a lightweight import path."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

from .base import WakewordDetector, WakewordError

_MODEL_FILES = {
    "embedding": "embedding_model.tflite",
    "melspectrogram": "melspectrogram.tflite",
    "hey_jarvis": "hey_jarvis_v0.1.tflite",
}


def _package_directory() -> Path:
    existing = sys.modules.get("openwakeword")
    existing_path = getattr(existing, "__path__", None)
    if existing_path:
        return Path(next(iter(existing_path)))
    spec = importlib.util.find_spec("openwakeword")
    if spec is None or not spec.submodule_search_locations:
        raise WakewordError("openWakeWord 0.6.0 is not installed.")
    return Path(next(iter(spec.submodule_search_locations)))


def validate_runtime() -> None:
    package_dir = _package_directory()
    missing = [
        name
        for name in _MODEL_FILES.values()
        if not (package_dir / "resources" / "models" / name).is_file()
    ]
    if missing:
        raise WakewordError(
            "Missing openWakeWord TFLite models: " + ", ".join(sorted(missing))
        )
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
        "embedding": {"model_path": str(model_dir / _MODEL_FILES["embedding"])},
        "melspectrogram": {
            "model_path": str(model_dir / _MODEL_FILES["melspectrogram"])
        },
    }
    package.MODELS = {
        "hey_jarvis": {"model_path": str(model_dir / _MODEL_FILES["hey_jarvis"])}
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
    def __init__(self, threshold: float = 0.5) -> None:
        try:
            validate_runtime()
            model_class = _load_model_class()
            self._model = model_class(
                wakeword_models=["hey jarvis"],
                inference_framework="tflite",
            )
        except WakewordError:
            raise
        except Exception as exc:
            raise WakewordError(f"Could not load the 'hey jarvis' TFLite model: {exc}") from exc
        self._threshold = threshold

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
            import numpy

            predictions = self._model.predict(
                numpy.frombuffer(pcm16_chunk, dtype=numpy.int16)
            )
        except Exception as exc:
            raise WakewordError(f"Wake-word inference failed: {exc}") from exc
        return any(
            "jarvis" in name.lower() and float(score) >= self._threshold
            for name, score in predictions.items()
        )

    def reset(self) -> None:
        reset = getattr(self._model, "reset", None)
        if callable(reset):
            reset()

    def close(self) -> None:
        self._model = None
