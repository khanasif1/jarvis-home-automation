from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from home_assistant_api.models import (
    ErrorDetail,
    ErrorResponse,
    VoiceTurnAction,
    VoiceTurnRequest,
    VoiceTurnResponse,
)

VALID_REQUEST_ID = "11111111-1111-1111-1111-111111111111"


def test_voice_turn_request_valid_text_only():
    req = VoiceTurnRequest.model_validate(
        {
            "requestId": VALID_REQUEST_ID,
            "deviceId": "pi-kitchen-01",
            "timezone": "America/Los_Angeles",
            "text": "What's on my todo list?",
        }
    )
    assert req.locale == "en-US"
    assert req.audio_base64 is None


def test_voice_turn_request_valid_audio_only():
    req = VoiceTurnRequest.model_validate(
        {
            "requestId": VALID_REQUEST_ID,
            "deviceId": "pi-kitchen-01",
            "timezone": "UTC",
            "audioBase64": "AAAA",
            "audioContentType": "audio/wav",
        }
    )
    assert req.text is None
    assert req.audio_content_type == "audio/wav"


def test_voice_turn_request_rejects_both_text_and_audio():
    with pytest.raises(PydanticValidationError):
        VoiceTurnRequest.model_validate(
            {
                "requestId": VALID_REQUEST_ID,
                "deviceId": "pi-kitchen-01",
                "timezone": "UTC",
                "text": "hello",
                "audioBase64": "AAAA",
                "audioContentType": "audio/wav",
            }
        )


def test_voice_turn_request_rejects_neither_text_nor_audio():
    with pytest.raises(PydanticValidationError):
        VoiceTurnRequest.model_validate(
            {"requestId": VALID_REQUEST_ID, "deviceId": "pi-kitchen-01", "timezone": "UTC"}
        )


def test_voice_turn_request_rejects_audio_without_content_type():
    with pytest.raises(PydanticValidationError):
        VoiceTurnRequest.model_validate(
            {
                "requestId": VALID_REQUEST_ID,
                "deviceId": "pi-kitchen-01",
                "timezone": "UTC",
                "audioBase64": "AAAA",
            }
        )


def test_voice_turn_request_rejects_bad_device_id():
    with pytest.raises(PydanticValidationError):
        VoiceTurnRequest.model_validate(
            {
                "requestId": VALID_REQUEST_ID,
                "deviceId": "!!",
                "timezone": "UTC",
                "text": "hi",
            }
        )


def test_voice_turn_request_rejects_bad_locale():
    with pytest.raises(PydanticValidationError):
        VoiceTurnRequest.model_validate(
            {
                "requestId": VALID_REQUEST_ID,
                "deviceId": "pi-kitchen-01",
                "timezone": "UTC",
                "text": "hi",
                "locale": "not-a-locale!",
            }
        )


def test_voice_turn_request_rejects_non_uuid_request_id():
    with pytest.raises(PydanticValidationError):
        VoiceTurnRequest.model_validate(
            {
                "requestId": "not-a-uuid",
                "deviceId": "pi-kitchen-01",
                "timezone": "UTC",
                "text": "hi",
            }
        )


def test_voice_turn_response_wire_dict_omits_none_fields():
    response = VoiceTurnResponse(
        requestId=VALID_REQUEST_ID,
        conversationId="conv-1",
        text="Hello there",
        correlationId="corr-1",
    )
    wire = response.to_wire_dict()
    assert wire == {
        "requestId": VALID_REQUEST_ID,
        "conversationId": "conv-1",
        "text": "Hello there",
        "actions": [],
        "correlationId": "corr-1",
    }


def test_voice_turn_response_includes_audio_when_present():
    response = VoiceTurnResponse(
        requestId=VALID_REQUEST_ID,
        conversationId="conv-1",
        text="Hello there",
        audioBase64="AAAA",
        audioContentType="audio/mpeg",
        actions=[VoiceTurnAction(type="create_todo", status="completed", summary="ok")],
        correlationId="corr-1",
    )
    wire = response.to_wire_dict()
    assert wire["audioBase64"] == "AAAA"
    assert wire["audioContentType"] == "audio/mpeg"
    assert wire["actions"] == [{"type": "create_todo", "status": "completed", "summary": "ok"}]


def test_voice_turn_response_rejects_audio_without_content_type():
    with pytest.raises(PydanticValidationError):
        VoiceTurnResponse(
            requestId=VALID_REQUEST_ID,
            conversationId="conv-1",
            text="hi",
            audioBase64="AAAA",
            correlationId="corr-1",
        )


def test_error_response_wire_dict_shape():
    error_response = ErrorResponse(
        error=ErrorDetail(code="invalid_request", message="Bad request."),
        correlationId="corr-1",
    )
    wire = error_response.to_wire_dict()
    assert wire["error"]["code"] == "invalid_request"
    assert wire["error"]["retryable"] is False
    assert wire["correlationId"] == "corr-1"
    assert "details" not in wire["error"]
