"""Typed request/response models mirroring the shared API contract.

These models intentionally reproduce the field names, constraints, and
validation rules of ``contracts/schemas/voice-turn-request.json``,
``voice-turn-response.json``, and ``error-response.json`` byte-for-byte in
spirit (camelCase wire names via pydantic aliases, the same length/pattern
limits, and the same ``oneOf``/``dependentRequired`` rules) so the backend
can never silently drift from the published contract. The backend does not
import the contracts directory at runtime -- these are the generated API
models for this component.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from home_assistant_api.validation import (
    DEVICE_ID_PATTERN as _DEVICE_ID_PATTERN,
    LOCALE_PATTERN as _LOCALE_PATTERN,
    UUID_PATTERN as _UUID_PATTERN,
    validate_iana_timezone as _validate_iana_timezone,
)

AudioRequestContentType = Literal["audio/wav", "audio/x-wav"]
AudioResponseContentType = Literal["audio/wav", "audio/mpeg"]
ActionStatus = Literal["completed", "pending", "failed"]


class _ContractModel(BaseModel):
    """Base model configured to match strict contract semantics."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=False,
    )


class VoiceTurnRequest(_ContractModel):
    """Mirrors ``contracts/schemas/voice-turn-request.json``."""

    request_id: str = Field(alias="requestId")
    device_id: str = Field(alias="deviceId")
    timezone: str = Field(min_length=1, max_length=64)
    locale: str = Field(default="en-US")
    text: Optional[str] = Field(default=None, min_length=1, max_length=10000)
    audio_base64: Optional[str] = Field(default=None, alias="audioBase64", max_length=8_388_608)
    audio_content_type: Optional[AudioRequestContentType] = Field(
        default=None, alias="audioContentType"
    )
    conversation_id: Optional[str] = Field(
        default=None, alias="conversationId", min_length=1, max_length=128
    )

    @model_validator(mode="after")
    def _validate_contract_rules(self) -> "VoiceTurnRequest":
        if not _UUID_PATTERN.match(self.request_id):
            raise ValueError("requestId must be a UUID")
        if not _DEVICE_ID_PATTERN.match(self.device_id):
            raise ValueError("deviceId does not match the required pattern")
        if not _LOCALE_PATTERN.match(self.locale):
            raise ValueError("locale does not match the required pattern")
        _validate_iana_timezone(self.timezone)

        has_text = self.text is not None
        has_audio = self.audio_base64 is not None
        if has_text == has_audio:
            raise ValueError("Exactly one of 'text' or 'audioBase64' must be provided")
        if has_audio and self.audio_content_type is None:
            raise ValueError("audioContentType is required when audioBase64 is provided")
        if has_text and self.audio_content_type is not None:
            raise ValueError("audioContentType must not be provided alongside text")
        return self


class VoiceTurnAction(_ContractModel):
    """A single tool/action outcome surfaced back to the caller."""

    type: str = Field(min_length=1, max_length=64)
    status: ActionStatus
    summary: Optional[str] = Field(default=None, max_length=500)


class VoiceTurnResponse(_ContractModel):
    """Mirrors ``contracts/schemas/voice-turn-response.json``."""

    request_id: str = Field(alias="requestId")
    conversation_id: str = Field(alias="conversationId", min_length=1, max_length=128)
    text: str = Field(max_length=20000)
    audio_base64: Optional[str] = Field(default=None, alias="audioBase64")
    audio_content_type: Optional[AudioResponseContentType] = Field(
        default=None, alias="audioContentType"
    )
    actions: list[VoiceTurnAction] = Field(default_factory=list)
    correlation_id: str = Field(alias="correlationId", min_length=1, max_length=128)

    @model_validator(mode="after")
    def _validate_audio_pairing(self) -> "VoiceTurnResponse":
        has_audio = self.audio_base64 is not None
        has_content_type = self.audio_content_type is not None
        if has_audio != has_content_type:
            raise ValueError("audioBase64 and audioContentType must be provided together")
        return self

    def to_wire_dict(self) -> dict[str, Any]:
        """Serialize using contract field names, omitting unset optionals."""

        return self.model_dump(by_alias=True, exclude_none=True)


class ErrorDetail(_ContractModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool = False
    details: Optional[dict[str, Any]] = None


class ErrorResponse(_ContractModel):
    """Mirrors ``contracts/schemas/error-response.json``."""

    error: ErrorDetail
    correlation_id: str = Field(alias="correlationId", min_length=1, max_length=128)

    def to_wire_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


# ---------------------------------------------------------------------------
# Domain models used internally (not part of the published Pi<->backend
# contract) for the practical device/reminder/todo behavior the backend
# implements on top of it.
# ---------------------------------------------------------------------------


class Device(_ContractModel):
    device_id: str = Field(min_length=3, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    registered_at: str
    last_seen_at: Optional[str] = None


class Todo(_ContractModel):
    todo_id: str
    device_id: str
    title: str = Field(min_length=1, max_length=500)
    done: bool = False
    due_at: Optional[str] = None
    created_at: str
    updated_at: str


class Reminder(_ContractModel):
    reminder_id: str
    device_id: str
    title: str = Field(min_length=1, max_length=500)
    due_at: str
    created_at: str
    delivered: bool = False
    delivered_at: Optional[str] = None
    cancelled: bool = False
