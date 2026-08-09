"""Tests for home_assistant_pi.api.models."""

from __future__ import annotations

import base64

import pytest

from home_assistant_pi.api.models import (
    AUDIO_WAV,
    ErrorResponse,
    Reminder,
    VoiceTurnRequest,
    VoiceTurnResponse,
)


def test_voice_turn_request_from_text_to_dict():
    request = VoiceTurnRequest.from_text(
        "pi-1", "UTC", "turn on the lights", request_id="req-1"
    )
    payload = request.to_dict()
    assert payload == {
        "requestId": "req-1",
        "deviceId": "pi-1",
        "timezone": "UTC",
        "locale": "en-US",
        "text": "turn on the lights",
    }


def test_voice_turn_request_from_text_includes_conversation_id_when_set():
    request = VoiceTurnRequest.from_text(
        "pi-1", "UTC", "and the fan too", request_id="req-2", conversation_id="conv-1"
    )
    payload = request.to_dict()
    assert payload["conversationId"] == "conv-1"


def test_voice_turn_request_from_audio_to_dict():
    request = VoiceTurnRequest.from_audio(
        "pi-1", "UTC", b"\x00\x01\x02", request_id="req-3"
    )
    payload = request.to_dict()
    assert payload["deviceId"] == "pi-1"
    assert payload["audioContentType"] == AUDIO_WAV
    assert base64.b64decode(payload["audioBase64"]) == b"\x00\x01\x02"
    assert "text" not in payload


def test_voice_turn_request_generates_request_id_by_default():
    request = VoiceTurnRequest.from_text("pi-1", "UTC", "hi")
    assert request.request_id
    other = VoiceTurnRequest.from_text("pi-1", "UTC", "hi")
    assert other.request_id != request.request_id


def test_voice_turn_request_rejects_both_text_and_audio():
    with pytest.raises(ValueError):
        VoiceTurnRequest(
            device_id="pi-1",
            timezone="UTC",
            text="hi",
            audio_base64="AAA=",
            audio_content_type=AUDIO_WAV,
        )


def test_voice_turn_request_rejects_neither_text_nor_audio():
    with pytest.raises(ValueError):
        VoiceTurnRequest(device_id="pi-1", timezone="UTC")


def test_voice_turn_response_from_dict_with_audio():
    audio_b64 = base64.b64encode(b"reply-audio-bytes").decode("ascii")
    data = {
        "requestId": "req-1",
        "conversationId": "conv-1",
        "text": "Sure, done.",
        "audioBase64": audio_b64,
        "audioContentType": "audio/wav",
        "actions": [{"type": "light.on", "status": "ok"}],
        "correlationId": "corr-1",
    }
    response = VoiceTurnResponse.from_dict(data)
    assert response.request_id == "req-1"
    assert response.conversation_id == "conv-1"
    assert response.text == "Sure, done."
    assert response.reply_audio_bytes() == b"reply-audio-bytes"
    assert response.actions == [{"type": "light.on", "status": "ok"}]
    assert response.correlation_id == "corr-1"


def test_voice_turn_response_from_dict_without_audio():
    response = VoiceTurnResponse.from_dict(
        {
            "requestId": "req-1",
            "conversationId": "conv-1",
            "text": "hi",
            "correlationId": "corr-1",
        }
    )
    assert response.reply_audio_bytes() is None
    assert response.actions == []


def test_error_response_from_dict_parses_nested_error():
    """Errors are nested under an `error` key with a top-level correlationId,
    per contracts/schemas/error-response.json."""
    error = ErrorResponse.from_dict(
        {
            "error": {"code": "bad_request", "message": "Nope", "retryable": True},
            "correlationId": "corr-err",
        }
    )
    assert error.code == "bad_request"
    assert error.message == "Nope"
    assert error.retryable is True
    assert error.correlation_id == "corr-err"


def test_error_response_from_dict_defaults_retryable_false():
    error = ErrorResponse.from_dict(
        {"error": {"code": "x", "message": "y"}, "correlationId": "c"}
    )
    assert error.retryable is False


def test_reminder_from_dict_uses_backend_wire_field_names():
    reminder = Reminder.from_dict(
        {
            "reminderId": "r1",
            "title": "Take out the trash",
            "dueAt": "2026-01-01T00:00:00Z",
            "deviceId": "pi-1",
        }
    )
    assert reminder.id == "r1"
    assert reminder.title == "Take out the trash"
    assert reminder.due_at == "2026-01-01T00:00:00Z"
    assert reminder.device_id == "pi-1"
