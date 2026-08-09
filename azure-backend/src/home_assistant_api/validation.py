"""Shared identifier and value validators.

Centralizing these patterns means every route that accepts a ``deviceId``,
a reminder/todo id, or an IANA timezone validates it the same way the
``VoiceTurnRequest`` contract model does -- an admin registering a device or
a Pi polling for reminders cannot smuggle a value through a looser check
than the one the published contract enforces.
"""

from __future__ import annotations

import re
import zoneinfo
from typing import Optional

from home_assistant_api.errors import ValidationError

DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
LOCALE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")


def validate_device_id(value: Optional[str], *, field_name: str = "deviceId") -> str:
    """Validate a device id against the same pattern the contract uses.

    Raises:
        ValidationError: If ``value`` is missing or does not match the
            required device id pattern.
    """

    if not value or not DEVICE_ID_PATTERN.match(value):
        raise ValidationError(
            f"{field_name} is required and must match the pattern "
            f"'{DEVICE_ID_PATTERN.pattern}'."
        )
    return value


def validate_uuid(value: Optional[str], *, field_name: str) -> str:
    """Validate that ``value`` is a well-formed UUID string.

    Raises:
        ValidationError: If ``value`` is missing or not a UUID.
    """

    if not value or not UUID_PATTERN.match(value):
        raise ValidationError(f"{field_name} is required and must be a UUID.")
    return value


def is_valid_iana_timezone(value: str) -> bool:
    """Return whether ``value`` names a real IANA time zone (e.g. tzdata)."""

    if not value:
        return False
    try:
        zoneinfo.ZoneInfo(value)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return False
    return True


def validate_iana_timezone(value: str) -> str:
    """Validate ``value`` as a real IANA timezone name.

    Raises:
        ValueError: If ``value`` is not a recognized IANA zone. ``ValueError``
            (rather than ``ValidationError``) is used here so this can be
            called directly from a pydantic ``model_validator``, which only
            recognizes ``ValueError``/``AssertionError`` as validation
            failures.
    """

    if not is_valid_iana_timezone(value):
        raise ValueError(f"timezone '{value}' is not a recognized IANA time zone name")
    return value
