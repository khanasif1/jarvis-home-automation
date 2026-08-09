from __future__ import annotations

import pytest

from home_assistant_api.auth import (
    authenticate_admin,
    authenticate_device,
    extract_bearer_token,
    hash_token,
)
from home_assistant_api.errors import AuthenticationError
from home_assistant_api.repositories.devices import InMemoryDevicesRepository


def test_extract_bearer_token_success():
    assert extract_bearer_token("Bearer abc123") == "abc123"


def test_extract_bearer_token_missing_header_raises():
    with pytest.raises(AuthenticationError):
        extract_bearer_token(None)


def test_extract_bearer_token_wrong_scheme_raises():
    with pytest.raises(AuthenticationError):
        extract_bearer_token("Basic abc123")


def test_extract_bearer_token_empty_token_raises():
    with pytest.raises(AuthenticationError):
        extract_bearer_token("Bearer   ")


def test_hash_token_is_deterministic_and_not_plaintext():
    hashed = hash_token("my-secret-token")
    assert hashed == hash_token("my-secret-token")
    assert hashed != "my-secret-token"


def test_authenticate_device_success():
    repo = InMemoryDevicesRepository()
    repo.register("device-1", "Kitchen Pi", hash_token("secret-token"))
    device_id = authenticate_device(
        authorization_header="Bearer secret-token",
        claimed_device_id="device-1",
        devices_repository=repo,
    )
    assert device_id == "device-1"


def test_authenticate_device_unknown_device_raises():
    repo = InMemoryDevicesRepository()
    with pytest.raises(AuthenticationError):
        authenticate_device(
            authorization_header="Bearer whatever",
            claimed_device_id="missing",
            devices_repository=repo,
        )


def test_authenticate_device_wrong_token_raises():
    repo = InMemoryDevicesRepository()
    repo.register("device-1", "Kitchen Pi", hash_token("secret-token"))
    with pytest.raises(AuthenticationError):
        authenticate_device(
            authorization_header="Bearer wrong-token",
            claimed_device_id="device-1",
            devices_repository=repo,
        )


def test_authenticate_device_disabled_device_raises():
    repo = InMemoryDevicesRepository()
    repo.register("device-1", "Kitchen Pi", hash_token("secret-token"))
    repo.set_enabled("device-1", False)
    with pytest.raises(AuthenticationError):
        authenticate_device(
            authorization_header="Bearer secret-token",
            claimed_device_id="device-1",
            devices_repository=repo,
        )


def test_authenticate_device_reenabled_device_succeeds_again():
    repo = InMemoryDevicesRepository()
    repo.register("device-1", "Kitchen Pi", hash_token("secret-token"))
    repo.set_enabled("device-1", False)
    repo.set_enabled("device-1", True)
    device_id = authenticate_device(
        authorization_header="Bearer secret-token",
        claimed_device_id="device-1",
        devices_repository=repo,
    )
    assert device_id == "device-1"


def test_authenticate_admin_success():
    authenticate_admin(provided_key="admin-key", expected_key="admin-key")


def test_authenticate_admin_missing_key_raises():
    with pytest.raises(AuthenticationError):
        authenticate_admin(provided_key=None, expected_key="admin-key")


def test_authenticate_admin_wrong_key_raises():
    with pytest.raises(AuthenticationError):
        authenticate_admin(provided_key="wrong", expected_key="admin-key")
