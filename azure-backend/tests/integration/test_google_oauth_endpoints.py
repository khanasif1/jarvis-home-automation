from __future__ import annotations

import json

import pytest

from home_assistant_api import routes
from home_assistant_api.errors import ConfigurationError
from home_assistant_api.google.oauth import StoredCredentialData
from home_assistant_api.google.oauth_state import create_signed_oauth_state

from tests.integration.helpers import make_request

ADMIN_KEY = "admin-key-0123456789"


class FakeOAuthClient:
    def __init__(self, *, authorization_url: str = "https://accounts.google.com/o/oauth2/auth?fake=1"):
        self._authorization_url = authorization_url
        self.exchanged_codes: list[str] = []

    def build_authorization_url(self, *, state: str) -> str:
        return f"{self._authorization_url}&state={state}"

    def exchange_code(self, *, code: str, state=None) -> StoredCredentialData:
        self.exchanged_codes.append(code)
        return StoredCredentialData(
            token="fake-access-token",
            refresh_token="fake-refresh-token",
            token_uri="https://oauth2.googleapis.com/token",
            client_id="fake-client-id",
            client_secret="fake-client-secret",
            scopes=("https://www.googleapis.com/auth/calendar.events",),
            expiry_iso=None,
        )


def test_google_oauth_start_requires_admin_key(app_context_factory, full_env):
    ctx = app_context_factory(full_env, google_oauth_client=FakeOAuthClient())
    req = make_request(
        method="GET",
        url="http://localhost/api/google/oauth/start",
        params={"deviceId": "device-one"},
    )
    response = routes.google_oauth_start(req, ctx)
    assert response.status_code == 401


def test_google_oauth_start_returns_authorization_url(app_context_factory, full_env):
    fake_client = FakeOAuthClient()
    ctx = app_context_factory(full_env, google_oauth_client=fake_client)
    req = make_request(
        method="GET",
        url="http://localhost/api/google/oauth/start",
        headers={"x-admin-api-key": ADMIN_KEY},
        params={"deviceId": "device-one"},
    )
    response = routes.google_oauth_start(req, ctx)
    assert response.status_code == 200
    body = json.loads(response.get_body())
    assert body["authorizationUrl"].startswith("https://accounts.google.com/o/oauth2/auth")
    assert "state=" in body["authorizationUrl"]
    # The state is opaque and signed -- it must not simply be
    # "device-one:<random>" the way the pre-CSRF-fix implementation built
    # it, since that shape is what let an attacker forge an arbitrary
    # device id into an unsigned state value.
    state = body["authorizationUrl"].split("state=", 1)[1]
    assert state != "device-one"
    assert ":" not in state


def test_google_oauth_start_missing_device_id_is_validation_error(app_context_factory, full_env):
    ctx = app_context_factory(full_env, google_oauth_client=FakeOAuthClient())
    req = make_request(
        method="GET",
        url="http://localhost/api/google/oauth/start",
        headers={"x-admin-api-key": ADMIN_KEY},
    )
    response = routes.google_oauth_start(req, ctx)
    assert response.status_code == 400


def test_google_oauth_callback_saves_credentials(app_context_factory, full_env):
    fake_client = FakeOAuthClient()
    ctx = app_context_factory(full_env, google_oauth_client=fake_client)
    state = create_signed_oauth_state("device-one", ctx.config.oauth_state_signing_key())
    req = make_request(
        method="GET",
        url="http://localhost/api/google/oauth/callback",
        params={"code": "auth-code-123", "state": state},
    )
    response = routes.google_oauth_callback(req, ctx)
    assert response.status_code == 200
    body = json.loads(response.get_body())
    assert body == {"status": "connected", "deviceId": "device-one"}
    assert fake_client.exchanged_codes == ["auth-code-123"]
    # Credentials are now retrievable for this device without raising.
    credentials = ctx.credential_store.get_credentials("device-one")
    assert credentials.token == "fake-access-token"


def test_google_oauth_callback_full_flow_via_start_endpoint(app_context_factory, full_env):
    """The state minted by /start must be exactly what /callback accepts."""

    fake_client = FakeOAuthClient()
    ctx = app_context_factory(full_env, google_oauth_client=fake_client)
    start_req = make_request(
        method="GET",
        url="http://localhost/api/google/oauth/start",
        headers={"x-admin-api-key": ADMIN_KEY},
        params={"deviceId": "device-one"},
    )
    start_response = routes.google_oauth_start(start_req, ctx)
    authorization_url = json.loads(start_response.get_body())["authorizationUrl"]
    state = authorization_url.split("state=", 1)[1]

    callback_req = make_request(
        method="GET",
        url="http://localhost/api/google/oauth/callback",
        params={"code": "auth-code-123", "state": state},
    )
    response = routes.google_oauth_callback(callback_req, ctx)
    assert response.status_code == 200
    assert json.loads(response.get_body())["deviceId"] == "device-one"


def test_google_oauth_callback_missing_state_is_validation_error(app_context_factory, full_env):
    ctx = app_context_factory(full_env, google_oauth_client=FakeOAuthClient())
    req = make_request(
        method="GET",
        url="http://localhost/api/google/oauth/callback",
        params={"code": "auth-code-123"},
    )
    response = routes.google_oauth_callback(req, ctx)
    assert response.status_code == 400


def test_google_oauth_callback_rejects_forged_state(app_context_factory, full_env):
    """An attacker-crafted state naming a victim device must be rejected.

    This is the core CSRF regression test for item 5: without HMAC
    verification, a caller could previously attach their own Google grant
    to an arbitrary victim device id by crafting an unsigned
    "victim-device:random" state string.
    """

    ctx = app_context_factory(full_env, google_oauth_client=FakeOAuthClient())
    req = make_request(
        method="GET",
        url="http://localhost/api/google/oauth/callback",
        params={"code": "auth-code-123", "state": "victim-device:forged-suffix"},
    )
    response = routes.google_oauth_callback(req, ctx)
    assert response.status_code == 400
    # And no credentials must have been attached to the victim device.
    with pytest.raises(ConfigurationError):
        ctx.credential_store.get_credentials("victim-device")


def test_google_oauth_callback_rejects_tampered_state(app_context_factory, full_env):
    ctx = app_context_factory(full_env, google_oauth_client=FakeOAuthClient())
    state = create_signed_oauth_state("device-one", ctx.config.oauth_state_signing_key())
    tampered = state[:-1] + ("A" if state[-1] != "A" else "B")
    req = make_request(
        method="GET",
        url="http://localhost/api/google/oauth/callback",
        params={"code": "auth-code-123", "state": tampered},
    )
    response = routes.google_oauth_callback(req, ctx)
    assert response.status_code == 400


def test_google_oauth_callback_rejects_expired_state(app_context_factory, full_env):
    ctx = app_context_factory(full_env, google_oauth_client=FakeOAuthClient())
    expired_state = create_signed_oauth_state(
        "device-one",
        ctx.config.oauth_state_signing_key(),
        ttl_seconds=1,
        now_epoch=1_000_000,
    )
    req = make_request(
        method="GET",
        url="http://localhost/api/google/oauth/callback",
        params={"code": "auth-code-123", "state": expired_state},
    )
    response = routes.google_oauth_callback(req, ctx)
    assert response.status_code == 400


def test_google_oauth_start_unconfigured_google_raises_configuration_error(app_context_factory, base_env):
    admin_env = dict(base_env)
    admin_env["ADMIN_API_KEY"] = ADMIN_KEY
    ctx = app_context_factory(admin_env)
    req = make_request(
        method="GET",
        url="http://localhost/api/google/oauth/start",
        headers={"x-admin-api-key": ADMIN_KEY},
        params={"deviceId": "device-one"},
    )
    response = routes.google_oauth_start(req, ctx)
    assert response.status_code == 500
    body = json.loads(response.get_body())
    assert body["error"]["code"] == "configuration_error"
