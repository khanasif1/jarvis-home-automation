"""A keyboard-driven "wake word" for development and headless testing.

Instead of listening for a spoken keyword, this detector treats pressing
Enter (or any configured trigger callback returning True) as the wake
event. It requires no microphone, no native libraries, and no model files,
making it the safe default engine and ideal for CI/unit testing and for
devices without a wake-word model installed yet.
"""

from __future__ import annotations

import sys
import time
from typing import Callable, Optional

from .base import WakewordDetector

#: Brief pause after an EOF poll so a systemd-managed process (stdin
#: redirected from /dev/null, which always immediately returns EOF) does
#: not spin the CPU at 100% re-reading an already-closed stdin forever.
_EOF_BACKOFF_SECONDS = 0.2


class KeyboardWakewordDetector(WakewordDetector):
    """Treats an external trigger (default: stdin readline) as the wake event."""

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_length: int = 512,
        trigger: Optional[Callable[[], bool]] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sample_rate = sample_rate
        self._frame_length = frame_length
        self._sleep_fn = sleep_fn
        # `trigger` lets tests/dev tools inject a fake "was Enter pressed?"
        # check instead of blocking on real stdin.
        self._trigger = trigger if trigger is not None else self._default_trigger

    def _default_trigger(self) -> bool:
        line = sys.stdin.readline()
        if line == "":
            # readline() returns "" only at EOF (a real keypress always
            # includes at least the newline). Treating EOF as "wake word
            # detected" would trigger a conversation turn every single
            # iteration once stdin is closed/redirected from /dev/null (the
            # normal case under systemd), so EOF must be False. Sleep
            # briefly so a closed stdin does not spin the CPU at 100%.
            self._sleep_fn(_EOF_BACKOFF_SECONDS)
            return False
        return True

    def frame_length(self) -> int:
        return self._frame_length

    def sample_rate(self) -> int:
        return self._sample_rate

    def process(self, pcm16_chunk: bytes) -> bool:
        # The keyboard engine ignores audio content entirely; any call to
        # process() is treated as a poll of the trigger source.
        return bool(self._trigger())
