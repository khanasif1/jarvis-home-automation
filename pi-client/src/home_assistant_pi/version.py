"""Single source of truth for the pi-client package version.

The value here must be kept in sync with the ``version`` field in
``pyproject.toml``. A unit test (``tests/test_version.py``) enforces this so
the two never drift apart.
"""

from __future__ import annotations

__version__ = "1.0.0"


def get_version() -> str:
    """Return the current pi-client package version string."""
    return __version__
