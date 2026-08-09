"""Wake-word detection engines."""

from __future__ import annotations

import os

from .base import WakewordDetector, WakewordError

__all__ = ["WakewordDetector", "WakewordError", "create_detector"]


def create_detector(
    engine: str,
    keyword: str = "jarvis",
    sensitivity: float = 0.5,
) -> WakewordDetector:
    """Instantiate the configured wake-word engine by name.

    Args:
        engine: One of ``"keyboard"``, ``"porcupine"``, ``"openwakeword"``.
        keyword: The wake word/phrase name to listen for.
        sensitivity: Engine sensitivity in the ``[0.0, 1.0]`` range.

    Raises:
        WakewordError: if ``engine`` is unknown or its dependency is missing.
    """
    if engine == "keyboard":
        from .keyboard import KeyboardWakewordDetector

        return KeyboardWakewordDetector()
    if engine == "porcupine":
        from .porcupine import PorcupineWakewordDetector

        return PorcupineWakewordDetector(
            keyword=keyword,
            sensitivity=sensitivity,
            access_key=os.environ.get("HAP_PORCUPINE_ACCESS_KEY"),
        )
    if engine == "openwakeword":
        from .openwakeword import OpenWakewordDetector

        return OpenWakewordDetector(keyword=keyword, sensitivity=sensitivity)

    raise WakewordError(f"Unknown wake-word engine: {engine!r}")
