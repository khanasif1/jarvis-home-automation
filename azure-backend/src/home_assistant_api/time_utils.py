"""UTC time helpers used throughout the backend.

Times are always stored and compared in UTC. Callers translate to a
device-local IANA timezone only for display purposes (the Pi client owns
that translation using the ``timezone`` field on ``VoiceTurnRequest``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""

    return datetime.now(timezone.utc)


def to_iso8601(value: datetime) -> str:
    """Render a datetime as an ISO-8601 string with an explicit ``Z`` suffix."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso8601(value: str) -> datetime:
    """Parse an ISO-8601 string into a timezone-aware UTC datetime.

    Raises:
        ValueError: If ``value`` is not a valid ISO-8601 timestamp. Callers
            that receive this from user input should translate it into a
            ``ValidationError``.
    """

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class Stopwatch:
    """A tiny elapsed-time helper used to report tool and turn latency.

    Usage::

        with Stopwatch() as sw:
            do_work()
        sw.elapsed_ms
    """

    _start: float = field(default=0.0, init=False, repr=False)
    _end: Optional[float] = field(default=None, init=False, repr=False)

    def __enter__(self) -> "Stopwatch":
        self._start = time.perf_counter()
        self._end = None
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._end = time.perf_counter()

    @property
    def elapsed_ms(self) -> float:
        end = self._end if self._end is not None else time.perf_counter()
        return round((end - self._start) * 1000.0, 3)
