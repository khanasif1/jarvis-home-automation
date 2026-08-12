"""Single source of truth for the Pi client version."""

from __future__ import annotations

__version__ = "2.0.2"


def get_version() -> str:
    return __version__
