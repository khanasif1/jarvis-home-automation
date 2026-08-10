"""The single supported wake-word implementation."""

from .base import WakewordDetector, WakewordError
from .openwakeword import OpenWakewordDetector


def create_detector(threshold: float = 0.5) -> WakewordDetector:
    return OpenWakewordDetector(threshold=threshold)


__all__ = [
    "OpenWakewordDetector",
    "WakewordDetector",
    "WakewordError",
    "create_detector",
]
