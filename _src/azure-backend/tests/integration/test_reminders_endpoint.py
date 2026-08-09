from __future__ import annotations

import json

from home_assistant_api import routes

from tests.integration.helpers import make_request

DEVICE_TOKEN = "device-one-token-0123456789"


def test_list_due_reminders_requires_device_id_param(app_context_factory, full_env):
    ctx = app_context_factory(full_env)
    req = make_request(
        method="GET",
        url="http://localhost/api/reminders/due",
        headers={"Authorization": f"Bearer {DEVICE_TOKEN}"},
    )
    response = routes.list_due_reminders(req, ctx)
    assert response.status_code == 400


def test_list_due_reminders_requires_valid_auth(app_context_factory, full_env):
    ctx = app_context_factory(full_env)
    req = make_request(
        method="GET",
        url="http://localhost/api/reminders/due",
        headers={"Authorization": "Bearer wrong-token"},
        params={"deviceId": "device-one"},
    )
    response = routes.list_due_reminders(req, ctx)
    assert response.status_code == 401


def test_list_due_reminders_returns_only_past_due(app_context_factory, full_env):
    ctx = app_context_factory(full_env)
    ctx.reminders_repo.create("device-one", "Past reminder", "2000-01-01T00:00:00Z")
    ctx.reminders_repo.create("device-one", "Future reminder", "2099-01-01T00:00:00Z")
    req = make_request(
        method="GET",
        url="http://localhost/api/reminders/due",
        headers={"Authorization": f"Bearer {DEVICE_TOKEN}"},
        params={"deviceId": "device-one"},
    )
    response = routes.list_due_reminders(req, ctx)
    assert response.status_code == 200
    body = json.loads(response.get_body())
    assert len(body["reminders"]) == 1
    assert body["reminders"][0]["title"] == "Past reminder"


def test_acknowledge_reminder_marks_delivered(app_context_factory, full_env):
    ctx = app_context_factory(full_env)
    reminder = ctx.reminders_repo.create("device-one", "Past reminder", "2000-01-01T00:00:00Z")
    req = make_request(
        method="POST",
        url=f"http://localhost/api/reminders/{reminder.reminder_id}/ack",
        headers={"Authorization": f"Bearer {DEVICE_TOKEN}"},
        route_params={"reminder_id": reminder.reminder_id},
        json_body={"deviceId": "device-one"},
    )
    response = routes.acknowledge_reminder(req, ctx)
    assert response.status_code == 200
    body = json.loads(response.get_body())
    assert body["delivered"] is True
    assert ctx.reminders_repo.list_due("device-one") == []


def test_acknowledge_reminder_unknown_id_returns_404(app_context_factory, full_env):
    ctx = app_context_factory(full_env)
    unknown_uuid = "00000000-0000-0000-0000-000000000000"
    req = make_request(
        method="POST",
        url=f"http://localhost/api/reminders/{unknown_uuid}/ack",
        headers={"Authorization": f"Bearer {DEVICE_TOKEN}"},
        route_params={"reminder_id": unknown_uuid},
        json_body={"deviceId": "device-one"},
    )
    response = routes.acknowledge_reminder(req, ctx)
    assert response.status_code == 404


def test_acknowledge_reminder_malformed_id_returns_400(app_context_factory, full_env):
    ctx = app_context_factory(full_env)
    req = make_request(
        method="POST",
        url="http://localhost/api/reminders/not-a-uuid/ack",
        headers={"Authorization": f"Bearer {DEVICE_TOKEN}"},
        route_params={"reminder_id": "not-a-uuid"},
        json_body={"deviceId": "device-one"},
    )
    response = routes.acknowledge_reminder(req, ctx)
    assert response.status_code == 400
