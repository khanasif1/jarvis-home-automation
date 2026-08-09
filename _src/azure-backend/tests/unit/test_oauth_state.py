from __future__ import annotations

import pytest

from home_assistant_api.errors import ValidationError
from home_assistant_api.google.oauth_state import (
    create_signed_oauth_state,
    verify_signed_oauth_state,
)

SIGNING_KEY = "test-signing-key-0123456789"


def test_create_and_verify_round_trip():
    state = create_signed_oauth_state("device-one", SIGNING_KEY)
    assert verify_signed_oauth_state(state, SIGNING_KEY) == "device-one"


def test_state_is_opaque_and_not_prefixed_with_plaintext_device_id():
    state = create_signed_oauth_state("device-one", SIGNING_KEY)
    assert not state.startswith("device-one")


def test_verify_rejects_tampered_signature():
    state = create_signed_oauth_state("device-one", SIGNING_KEY)
    encoded_device_id, expires_at, signature = state.split(".")
    tampered = f"{encoded_device_id}.{expires_at}.{signature[:-1]}X"
    with pytest.raises(ValidationError):
        verify_signed_oauth_state(tampered, SIGNING_KEY)


def test_verify_rejects_state_signed_with_a_different_key():
    state = create_signed_oauth_state("device-one", SIGNING_KEY)
    with pytest.raises(ValidationError):
        verify_signed_oauth_state(state, "a-completely-different-key")


def test_verify_rejects_expired_state():
    now = 1_700_000_000
    state = create_signed_oauth_state(
        "device-one", SIGNING_KEY, ttl_seconds=60, now_epoch=now
    )
    with pytest.raises(ValidationError):
        verify_signed_oauth_state(state, SIGNING_KEY, now_epoch=now + 61)


def test_verify_accepts_state_at_exact_expiry_boundary():
    now = 1_700_000_000
    state = create_signed_oauth_state(
        "device-one", SIGNING_KEY, ttl_seconds=60, now_epoch=now
    )
    # now == expires_at is still valid; only strictly-after is expired.
    assert verify_signed_oauth_state(state, SIGNING_KEY, now_epoch=now + 60) == "device-one"


def test_verify_rejects_malformed_state_with_wrong_number_of_segments():
    with pytest.raises(ValidationError):
        verify_signed_oauth_state("not-a-valid-state", SIGNING_KEY)
    with pytest.raises(ValidationError):
        verify_signed_oauth_state("only.two", SIGNING_KEY)
    with pytest.raises(ValidationError):
        verify_signed_oauth_state("way.too.many.segments.here", SIGNING_KEY)


def test_verify_rejects_non_integer_expiry_segment():
    encoded_device_id = create_signed_oauth_state("device-one", SIGNING_KEY).split(".")[0]
    with pytest.raises(ValidationError):
        verify_signed_oauth_state(f"{encoded_device_id}.not-a-number.signature", SIGNING_KEY)


def test_verify_rejects_forged_state_naming_a_victim_device_without_signing_key():
    """Core CSRF regression test.

    An attacker who does not know the signing key cannot construct any
    state (however formatted) that verifies as belonging to a device id of
    their choosing.
    """

    forged = "victim-device:forged-suffix"
    with pytest.raises(ValidationError):
        verify_signed_oauth_state(forged, SIGNING_KEY)


def test_different_device_ids_produce_different_states():
    state_a = create_signed_oauth_state("device-a", SIGNING_KEY, now_epoch=1_700_000_000)
    state_b = create_signed_oauth_state("device-b", SIGNING_KEY, now_epoch=1_700_000_000)
    assert state_a != state_b


def test_verify_rejects_state_with_device_id_swapped_between_two_valid_states():
    """An attacker cannot splice the device-id segment from one valid state
    onto the expiry/signature segments of another and have it verify."""

    now = 1_700_000_000
    state_a = create_signed_oauth_state("device-a", SIGNING_KEY, now_epoch=now)
    state_b = create_signed_oauth_state("device-b", SIGNING_KEY, now_epoch=now)
    encoded_a, _, _ = state_a.split(".")
    _, expires_b, signature_b = state_b.split(".")
    spliced = f"{encoded_a}.{expires_b}.{signature_b}"
    with pytest.raises(ValidationError):
        verify_signed_oauth_state(spliced, SIGNING_KEY)
