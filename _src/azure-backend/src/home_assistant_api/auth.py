"""Fixed-device UUID authentication."""

from __future__ import annotations

import secrets
import uuid


class DeviceAuthenticationError(ValueError):
    """Raised when the Pi UUID header is absent, malformed, or incorrect."""


def _canonical_header_uuid(value: str | None) -> str:
    if value is None or not value.strip():
        raise DeviceAuthenticationError("X-Device-Guid header is required.")
    candidate = value.strip()
    try:
        parsed = uuid.UUID(candidate)
    except ValueError as exc:
        raise DeviceAuthenticationError("X-Device-Guid must be a canonical UUID.") from exc
    canonical = str(parsed)
    if candidate != canonical:
        raise DeviceAuthenticationError("X-Device-Guid must be a canonical UUID.")
    return canonical


def authenticate_device(provided: str | None, expected: str) -> None:
    candidate = _canonical_header_uuid(provided)
    if not secrets.compare_digest(candidate, expected):
        raise DeviceAuthenticationError("Device is not authorized.")
