"""Device bearer-token authentication.

The Pi client authenticates with ``Authorization: Bearer <token>`` (the
``deviceToken`` security scheme in ``contracts/openapi.yaml``). The token is
validated against the specific ``deviceId`` claimed in the request body, so a
stolen token for one device cannot be replayed while impersonating another.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from home_assistant_api.errors import AuthenticationError
from home_assistant_api.repositories.devices import DevicesRepository

_BEARER_PREFIX = "Bearer "


def extract_bearer_token(authorization_header: Optional[str]) -> str:
    """Extract the opaque bearer token from an ``Authorization`` header.

    Raises:
        AuthenticationError: If the header is missing or malformed.
    """

    if not authorization_header:
        raise AuthenticationError("Missing Authorization header.")
    if not authorization_header.startswith(_BEARER_PREFIX):
        raise AuthenticationError("Authorization header must use the Bearer scheme.")
    token = authorization_header[len(_BEARER_PREFIX):].strip()
    if not token:
        raise AuthenticationError("Bearer token must not be empty.")
    return token


def hash_token(token: str) -> str:
    """Hash a device token for at-rest storage (never store plaintext)."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authenticate_device(
    *,
    authorization_header: Optional[str],
    claimed_device_id: str,
    devices_repository: DevicesRepository,
) -> str:
    """Validate that the bearer token belongs to ``claimed_device_id``.

    Returns:
        The authenticated device id.

    Raises:
        AuthenticationError: If the token is missing, unknown, or does not
            match the device that registered it.
    """

    token = extract_bearer_token(authorization_header)
    record = devices_repository.get(claimed_device_id)
    if record is None:
        raise AuthenticationError(f"Unknown device '{claimed_device_id}'.")
    if not hmac.compare_digest(record.token_hash, hash_token(token)):
        raise AuthenticationError("Device token is invalid.")
    if not record.enabled:
        raise AuthenticationError(f"Device '{claimed_device_id}' is disabled.")
    return claimed_device_id


def authenticate_admin(*, provided_key: Optional[str], expected_key: str) -> None:
    """Validate an operator request against the configured admin API key.

    Raises:
        AuthenticationError: If no key is provided or it does not match.
    """

    if not provided_key:
        raise AuthenticationError("Missing admin API key header.")
    if not hmac.compare_digest(provided_key, expected_key):
        raise AuthenticationError("Admin API key is invalid.")
