"""The single supported wake-word implementation."""

from .base import WakewordDetector, WakewordError
from .openwakeword import OpenWakewordDetector


def create_detector(
    threshold: float = 0.25,
    model_path: str | None = None,
) -> WakewordDetector:
    return OpenWakewordDetector(threshold=threshold, model_path=model_path)


__all__ = [
    "OpenWakewordDetector",
    "WakewordDetector",
    "WakewordError",
    "create_detector",
]
