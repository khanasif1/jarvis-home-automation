"""Data models exchanged with the azure-backend voice API.

These are lightweight, dependency-free dataclasses that mirror the exact
wire shapes defined by ``contracts/openapi.yaml`` and
``contracts/schemas/{voice-turn-request,voice-turn-response,error-response}.json``:

* ``VoiceTurnRequest`` -> ``voice-turn-request.json``: ``requestId``,
  ``deviceId``, ``timezone``, ``locale``, exactly one of ``text`` or
  ``audioBase64``/``audioContentType``, and an optional ``conversationId``.
* ``VoiceTurnResponse`` <- ``voice-turn-response.json``: ``requestId``,
  ``conversationId``, ``text``, optional ``audioBase64``/``audioContentType``,
  ``actions``, ``correlationId``.
* ``ErrorResponse`` <- ``error-response.json``: a top-level ``correlationId``
  with the actual error nested under an ``error`` object (``code``,
  ``message``, ``retryable``, ``details``).

The pi-client intentionally does not import anything from ``contracts/`` or
``azure-backend/`` at runtime -- the wheel must remain independently
installable on a Raspberry Pi with no access to the rest of the monorepo. If
the contracts change, these models should be updated to match, but the
pi-client build never depends on that directory being present.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

#: Content types the backend accepts for uploaded/returned audio.
AUDIO_WAV = "audio/wav"
AUDIO_X_WAV = "audio/x-wav"
AUDIO_MPEG = "audio/mpeg"


@dataclass
class VoiceTurnRequest:
    """A single request to the backend's ``POST /voice-turn`` endpoint.

    Exactly one of ``text`` or (``audio_base64`` and ``audio_content_type``)
    must be set, matching the ``oneOf`` constraint in
    ``contracts/schemas/voice-turn-request.json``.
    """

    device_id: str
    timezone: str
    text: Optional[str] = None
    audio_base64: Optional[str] = None
    audio_content_type: Optional[str] = None
    locale: str = "en-US"
    conversation_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        has_text = self.text is not None
        has_audio = self.audio_base64 is not None or self.audio_content_type is not None
        if has_text and has_audio:
            raise ValueError("VoiceTurnRequest cannot set both text and audio fields")
        if not has_text and not has_audio:
            raise ValueError("VoiceTurnRequest requires either text or audio fields")
        if has_audio and (self.audio_base64 is None or self.audio_content_type is None):
            raise ValueError(
                "VoiceTurnRequest audio turns require both audio_base64 and "
                "audio_content_type"
            )

    @classmethod
    def from_text(
        cls,
        device_id: str,
        timezone: str,
        text: str,
        *,
        locale: str = "en-US",
        conversation_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> "VoiceTurnRequest":
        kwargs: dict = {
            "device_id": device_id,
            "timezone": timezone,
            "text": text,
            "locale": locale,
            "conversation_id": conversation_id,
        }
        if request_id is not None:
            kwargs["request_id"] = request_id
        return cls(**kwargs)

    @classmethod
    def from_audio(
        cls,
        device_id: str,
        timezone: str,
        audio_bytes: bytes,
        *,
        audio_content_type: str = AUDIO_WAV,
        locale: str = "en-US",
        conversation_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> "VoiceTurnRequest":
        """Build a request from raw bytes of an *already-serialized* audio
        container (e.g. the output of
        :func:`home_assistant_pi.audio.wav.wav_bytes`).

        Callers must pass a real audio container (WAV header + PCM data),
        never bare/raw PCM samples, since ``audio_content_type`` declares the
        bytes as a specific container format.
        """
        kwargs: dict = {
            "device_id": device_id,
            "timezone": timezone,
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "audio_content_type": audio_content_type,
            "locale": locale,
            "conversation_id": conversation_id,
        }
        if request_id is not None:
            kwargs["request_id"] = request_id
        return cls(**kwargs)

    def to_dict(self) -> dict:
        payload: dict[str, Any] = {
            "requestId": self.request_id,
            "deviceId": self.device_id,
            "timezone": self.timezone,
            "locale": self.locale,
        }
        if self.text is not None:
            payload["text"] = self.text
        else:
            payload["audioBase64"] = self.audio_base64
            payload["audioContentType"] = self.audio_content_type
        if self.conversation_id is not None:
            payload["conversationId"] = self.conversation_id
        return payload


@dataclass
class VoiceTurnResponse:
    """The backend's reply to a voice turn (``voice-turn-response.json``)."""

    request_id: str
    conversation_id: str
    text: str
    audio_base64: Optional[str] = None
    audio_content_type: Optional[str] = None
    actions: list = field(default_factory=list)
    correlation_id: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "VoiceTurnResponse":
        return cls(
            request_id=data.get("requestId", ""),
            conversation_id=data.get("conversationId", ""),
            text=data.get("text", ""),
            audio_base64=data.get("audioBase64"),
            audio_content_type=data.get("audioContentType"),
            actions=list(data.get("actions", []) or []),
            correlation_id=data.get("correlationId", ""),
        )

    def reply_audio_bytes(self) -> Optional[bytes]:
        if not self.audio_base64:
            return None
        return base64.b64decode(self.audio_base64)


@dataclass
class ErrorResponse:
    """A structured error returned by the backend (``error-response.json``).

    The wire format nests the actual error under an ``error`` object
    alongside a top-level ``correlationId``::

        {"error": {"code": "...", "message": "...", "retryable": false},
         "correlationId": "..."}
    """

    code: str
    message: str
    retryable: bool = False
    details: Optional[dict] = None
    correlation_id: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "ErrorResponse":
        error = data.get("error") or {}
        return cls(
            code=error.get("code", "unknown_error"),
            message=error.get("message", "An unknown error occurred."),
            retryable=bool(error.get("retryable", False)),
            details=error.get("details"),
            correlation_id=data.get("correlationId", ""),
        )


@dataclass
class Reminder:
    """A single reminder returned by the backend's ``GET /reminders/due``."""

    id: str
    title: str
    due_at: str
    device_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "Reminder":
        return cls(
            id=data.get("reminderId", data.get("id", "")),
            title=data.get("title", data.get("text", "")),
            due_at=data.get("dueAt", ""),
            device_id=data.get("deviceId"),
        )
