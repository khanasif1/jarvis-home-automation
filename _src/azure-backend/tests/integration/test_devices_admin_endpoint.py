from __future__ import annotations

import json

from home_assistant_api import routes

from tests.integration.helpers import make_request

ADMIN_KEY = "admin-key-0123456789"


def test_register_device_requires_admin_key(app_context_factory, full_env):
    ctx = app_context_factory(full_env)
    req = make_request(
        method="POST",
        url="http://localhost/api/admin/devices",
        json_body={"deviceId": "pi-new", "displayName": "New Pi"},
    )
    response = routes.register_device(req, ctx)
    assert response.status_code == 401
    body = json.loads(response.get_body())
    assert body["error"]["code"] == "unauthorized_device"


def test_register_device_success_returns_token_once(app_context_factory, full_env):
    ctx = app_context_factory(full_env)
    req = make_request(
        method="POST",
        url="http://localhost/api/admin/devices",
        headers={"x-admin-api-key": ADMIN_KEY},
        json_body={"deviceId": "pi-new", "displayName": "New Pi"},
    )
    response = routes.register_device(req, ctx)
    assert response.status_code == 201
    body = json.loads(response.get_body())
    assert body["deviceId"] == "pi-new"
    assert body["displayName"] == "New Pi"
    assert "token" in body and len(body["token"]) > 0


def test_register_device_duplicate_conflicts(app_context_factory, full_env):
    ctx = app_context_factory(full_env)
    req = make_request(
        method="POST",
        url="http://localhost/api/admin/devices",
        headers={"x-admin-api-key": ADMIN_KEY},
        json_body={"deviceId": "device-one", "displayName": "Duplicate"},
    )
    # "device-one" is already seeded from DEVICE_API_TOKENS in full_env.
    response = routes.register_device(req, ctx)
    assert response.status_code == 409


def test_register_device_missing_display_name_is_validation_error(app_context_factory, full_env):
    ctx = app_context_factory(full_env)
    req = make_request(
        method="POST",
        url="http://localhost/api/admin/devices",
        headers={"x-admin-api-key": ADMIN_KEY},
        json_body={"deviceId": "pi-new"},
    )
    response = routes.register_device(req, ctx)
    assert response.status_code == 400


def test_list_devices_requires_admin_key(app_context_factory, full_env):
    ctx = app_context_factory(full_env)
    req = make_request(method="GET", url="http://localhost/api/admin/devices")
    response = routes.list_devices(req, ctx)
    assert response.status_code == 401


def test_list_devices_returns_seeded_device(app_context_factory, full_env):
    ctx = app_context_factory(full_env)
    req = make_request(
        method="GET",
        url="http://localhost/api/admin/devices",
        headers={"x-admin-api-key": ADMIN_KEY},
    )
    response = routes.list_devices(req, ctx)
    assert response.status_code == 200
    body = json.loads(response.get_body())
    assert any(d["deviceId"] == "device-one" for d in body["devices"])
