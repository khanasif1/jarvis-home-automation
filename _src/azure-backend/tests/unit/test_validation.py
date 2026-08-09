from __future__ import annotations

import pytest

from home_assistant_api.errors import ValidationError
from home_assistant_api.validation import (
    is_valid_iana_timezone,
    validate_device_id,
    validate_iana_timezone,
    validate_uuid,
)


def test_validate_device_id_accepts_typical_id():
    assert validate_device_id("kitchen-pi-01") == "kitchen-pi-01"


def test_validate_device_id_rejects_missing():
    with pytest.raises(ValidationError):
        validate_device_id(None)
    with pytest.raises(ValidationError):
        validate_device_id("")


def test_validate_device_id_rejects_too_short():
    with pytest.raises(ValidationError):
        validate_device_id("ab")


def test_validate_device_id_rejects_invalid_characters():
    with pytest.raises(ValidationError):
        validate_device_id("device id with spaces")
    with pytest.raises(ValidationError):
        validate_device_id("device/id")


def test_validate_device_id_error_names_field():
    with pytest.raises(ValidationError) as exc_info:
        validate_device_id(None, field_name="targetDeviceId")
    assert "targetDeviceId" in str(exc_info.value)


def test_validate_uuid_accepts_well_formed_uuid():
    uuid_value = "123e4567-e89b-12d3-a456-426614174000"
    assert validate_uuid(uuid_value, field_name="reminderId") == uuid_value


def test_validate_uuid_rejects_missing():
    with pytest.raises(ValidationError):
        validate_uuid(None, field_name="reminderId")
    with pytest.raises(ValidationError):
        validate_uuid("", field_name="reminderId")


def test_validate_uuid_rejects_malformed():
    with pytest.raises(ValidationError):
        validate_uuid("not-a-uuid", field_name="reminderId")
    with pytest.raises(ValidationError):
        validate_uuid("123e4567-e89b-12d3-a456", field_name="reminderId")


def test_is_valid_iana_timezone_accepts_real_zones():
    assert is_valid_iana_timezone("America/New_York") is True
    assert is_valid_iana_timezone("UTC") is True
    assert is_valid_iana_timezone("Asia/Kolkata") is True


def test_is_valid_iana_timezone_rejects_bogus_and_empty():
    assert is_valid_iana_timezone("Not/A_Zone") is False
    assert is_valid_iana_timezone("") is False
    assert is_valid_iana_timezone("EST5EDT_bogus_suffix_zzz") is False


def test_validate_iana_timezone_returns_value_when_valid():
    assert validate_iana_timezone("America/Los_Angeles") == "America/Los_Angeles"


def test_validate_iana_timezone_raises_value_error_when_invalid():
    # ValueError (not ValidationError) is required here so pydantic model
    # validators recognize this as a validation failure.
    with pytest.raises(ValueError):
        validate_iana_timezone("Definitely/Not_A_Zone")
