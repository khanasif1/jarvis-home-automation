"""Signed, expiring OAuth ``state`` values.

The ``state`` parameter in the Google OAuth authorization-code flow exists
specifically to prevent CSRF: without verification, an attacker can start
their own OAuth flow, obtain a valid ``code``, and then call this backend's
callback with a ``state`` naming an arbitrary victim device id, silently
attaching *their own* Google account credentials to that device. Signing
``state`` with an HMAC over ``device_id`` and an expiry timestamp -- keyed
by a secret only this backend knows -- makes it infeasible for a caller to
construct a ``state`` for a device id they were not issued one for, and a
short TTL bounds how long a leaked/captured ``state`` value remains usable.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import time
from hashlib import sha256
from typing import Optional

from home_assistant_api.errors import ValidationError

_DEFAULT_TTL_SECONDS = 600
_SEPARATOR = "."


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(signing_key: str, device_id: str, expires_at: int) -> str:
    payload = f"{device_id}{_SEPARATOR}{expires_at}".encode("utf-8")
    digest = hmac.new(signing_key.encode("utf-8"), payload, sha256).digest()
    return _b64url_encode(digest)


def create_signed_oauth_state(
    device_id: str,
    signing_key: str,
    *,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    now_epoch: Optional[int] = None,
) -> str:
    """Build a signed ``state`` value binding ``device_id`` to this OAuth flow."""

    now = now_epoch if now_epoch is not None else int(time.time())
    expires_at = now + ttl_seconds
    signature = _sign(signing_key, device_id, expires_at)
    encoded_device_id = _b64url_encode(device_id.encode("utf-8"))
    return f"{encoded_device_id}{_SEPARATOR}{expires_at}{_SEPARATOR}{signature}"


def verify_signed_oauth_state(
    state: str,
    signing_key: str,
    *,
    now_epoch: Optional[int] = None,
) -> str:
    """Verify ``state`` and return the ``device_id`` it was issued for.

    Raises:
        ValidationError: If ``state`` is malformed, its signature does not
            match (tampered or forged), or it has expired. Never leaks
            *why* verification failed beyond "invalid or expired" to avoid
            giving an attacker an oracle for forging a valid state.
    """

    parts = state.split(_SEPARATOR)
    if len(parts) != 3:
        raise ValidationError("state parameter is invalid or expired.")
    encoded_device_id, expires_at_raw, signature = parts
    try:
        device_id = _b64url_decode(encoded_device_id).decode("utf-8")
        expires_at = int(expires_at_raw)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValidationError("state parameter is invalid or expired.") from exc

    expected_signature = _sign(signing_key, device_id, expires_at)
    if not hmac.compare_digest(expected_signature, signature):
        raise ValidationError("state parameter is invalid or expired.")

    now = now_epoch if now_epoch is not None else int(time.time())
    if now > expires_at:
        raise ValidationError("state parameter is invalid or expired.")

    if not device_id:
        raise ValidationError("state parameter is invalid or expired.")

    return device_id
