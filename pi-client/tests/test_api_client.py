"""Tests for home_assistant_pi.api.client."""

from __future__ import annotations

import requests

from home_assistant_pi.api.client import ApiClient, ApiError
from home_assistant_pi.api.models import VoiceTurnRequest


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", reason="OK"):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.reason = reason
        self.content = text.encode() if json_data is None else b"{}"
        if json_data is not None:
            self.content = b"non-empty"

    def json(self):
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data


class FakeSession:
    def __init__(self, responses=None, exceptions=None):
        self.responses = list(responses or [])
        self.exceptions = list(exceptions or [])
        self.calls = []

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers})
        if self.exceptions:
            exc = self.exceptions.pop(0)
            if exc is not None:
                raise exc
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def make_client(session, **kwargs):
    return ApiClient(
        base_url="https://api.example.com/api",
        device_token="secret-token",
        session=session,
        **kwargs,
    )


def make_request(**overrides):
    defaults = dict(device_id="pi-1", timezone="UTC", text="hello")
    defaults.update(overrides)
    return VoiceTurnRequest(**defaults)


def test_send_voice_turn_sends_bearer_authorization_header():
    """Regression test for the literal `Authorization: ******` bug: the
    client must send the real device token as `Bearer <token>`."""
    session = FakeSession(
        responses=[
            FakeResponse(
                200,
                json_data={
                    "requestId": "req-1",
                    "conversationId": "conv-1",
                    "text": "Done",
                    "correlationId": "corr-1",
                },
            )
        ]
    )
    client = make_client(session)
    request = make_request()
    client.send_voice_turn(request)

    call = session.calls[0]
    assert call["headers"]["Authorization"] == "Bearer secret-token"


def test_send_voice_turn_never_sends_literal_masked_placeholder():
    """The previous bug sent the literal string '******' regardless of the
    configured token. Guard against any regression back to a hardcoded
    placeholder for *any* token value."""
    session = FakeSession(
        responses=[
            FakeResponse(
                200,
                json_data={
                    "requestId": "req-1",
                    "conversationId": "conv-1",
                    "text": "Done",
                    "correlationId": "corr-1",
                },
            )
        ]
    )
    client = make_client(session)
    client.send_voice_turn(make_request())
    assert session.calls[0]["headers"]["Authorization"] != "******"
    assert "*" not in session.calls[0]["headers"]["Authorization"]


def test_send_voice_turn_posts_to_voice_turn_without_duplicating_api_prefix():
    """base_url already includes /api (per infra's apiBaseUrl output and
    contracts/openapi.yaml's server URL); the client must not re-add /api."""
    session = FakeSession(
        responses=[
            FakeResponse(
                200,
                json_data={
                    "requestId": "req-1",
                    "conversationId": "conv-1",
                    "text": "Done",
                    "correlationId": "corr-1",
                },
            )
        ]
    )
    client = make_client(session)
    client.send_voice_turn(make_request())
    assert session.calls[0]["url"] == "https://api.example.com/api/voice-turn"


def test_send_voice_turn_includes_idempotency_key_header():
    session = FakeSession(
        responses=[
            FakeResponse(
                200,
                json_data={
                    "requestId": "req-123",
                    "conversationId": "conv-1",
                    "text": "Done",
                    "correlationId": "corr-1",
                },
            )
        ]
    )
    client = make_client(session)
    request = make_request(request_id="req-123")
    client.send_voice_turn(request)
    assert session.calls[0]["headers"]["Idempotency-Key"] == "req-123"


def test_send_voice_turn_request_body_matches_contract_field_names():
    session = FakeSession(
        responses=[
            FakeResponse(
                200,
                json_data={
                    "requestId": "req-1",
                    "conversationId": "conv-1",
                    "text": "Done",
                    "correlationId": "corr-1",
                },
            )
        ]
    )
    client = make_client(session)
    request = make_request(request_id="req-1", conversation_id="conv-0")
    client.send_voice_turn(request)
    body = session.calls[0]["json"]
    assert body == {
        "requestId": "req-1",
        "deviceId": "pi-1",
        "timezone": "UTC",
        "locale": "en-US",
        "text": "hello",
        "conversationId": "conv-0",
    }


def test_send_voice_turn_success_parses_response_fields():
    session = FakeSession(
        responses=[
            FakeResponse(
                200,
                json_data={
                    "requestId": "req-1",
                    "conversationId": "conv-9",
                    "text": "Done",
                    "correlationId": "corr-1",
                    "actions": [{"type": "reminder.created", "status": "ok"}],
                },
            )
        ]
    )
    client = make_client(session)
    response = client.send_voice_turn(make_request())
    assert response.text == "Done"
    assert response.conversation_id == "conv-9"
    assert response.correlation_id == "corr-1"
    assert response.actions == [{"type": "reminder.created", "status": "ok"}]


def test_send_voice_turn_error_response_parses_nested_error():
    """Errors are nested under an `error` key, per error-response.json."""
    session = FakeSession(
        responses=[
            FakeResponse(
                401,
                json_data={
                    "error": {"code": "unauthorized", "message": "bad token"},
                    "correlationId": "corr-err",
                },
            )
        ]
    )
    client = make_client(session)
    try:
        client.send_voice_turn(make_request())
        assert False, "expected ApiError"
    except ApiError as exc:
        assert exc.status_code == 401
        assert "unauthorized" in str(exc)
        assert "bad token" in str(exc)


def test_request_retries_transient_errors_then_succeeds():
    session = FakeSession(
        responses=[
            FakeResponse(
                200,
                json_data={
                    "requestId": "req-1",
                    "conversationId": "conv-1",
                    "text": "ok",
                    "correlationId": "corr-1",
                },
            )
        ],
        exceptions=[requests.ConnectionError("boom"), None],
    )
    client = make_client(session, retries=2)
    response = client.send_voice_turn(make_request(text="hi"))
    assert response.text == "ok"
    assert len(session.calls) == 2


def test_request_raises_after_exhausting_retries():
    session = FakeSession(
        exceptions=[
            requests.ConnectionError("boom1"),
            requests.ConnectionError("boom2"),
        ]
    )
    client = make_client(session, retries=1)
    try:
        client.send_voice_turn(make_request(text="hi"))
        assert False, "expected ApiError"
    except ApiError:
        pass
    assert len(session.calls) == 2  # initial attempt + 1 retry


def test_fetch_due_reminders_uses_reminders_due_path_without_api_prefix():
    session = FakeSession(
        responses=[
            FakeResponse(
                200,
                json_data={
                    "reminders": [
                        {
                            "reminderId": "r1",
                            "title": "Water plants",
                            "dueAt": "2026-01-01T00:00:00Z",
                        }
                    ]
                },
            )
        ]
    )
    client = make_client(session)
    reminders = client.fetch_due_reminders("pi-1")
    assert len(reminders) == 1
    assert reminders[0].id == "r1"
    assert reminders[0].title == "Water plants"
    assert session.calls[0]["url"] == "https://api.example.com/api/reminders/due?deviceId=pi-1"


def test_acknowledge_reminder_calls_ack_path_with_device_id_body():
    session = FakeSession(responses=[FakeResponse(200, text="")])
    client = make_client(session)
    client.acknowledge_reminder("r1", "pi-1")
    call = session.calls[0]
    assert call["url"] == "https://api.example.com/api/reminders/r1/ack"
    assert call["json"] == {"deviceId": "pi-1"}


def test_close_closes_session():
    session = FakeSession()
    client = make_client(session)
    client.close()
    assert getattr(session, "closed", False) is True


def test_context_manager_closes_session():
    session = FakeSession()
    with make_client(session) as client:
        assert client is not None
    assert getattr(session, "closed", False) is True
