"""HTTP route handlers.

Each handler is a plain function of ``(req, ctx, correlation_id)`` wrapped
by :func:`with_error_handling`, so it can be exercised directly in tests
with a hand-built ``azure.functions.HttpRequest`` and an ``AppContext``
populated with fakes -- no running Function host required.
"""

from __future__ import annotations

import base64
import binascii
import functools
import hashlib
import json
import secrets
import uuid
from typing import Any, Callable, Optional

import azure.functions as func
from pydantic import ValidationError as PydanticValidationError

from home_assistant_api.ai.orchestrator import AssistantOrchestrator
from home_assistant_api.ai.prompt import build_system_prompt
from home_assistant_api.app_context import AppContext
from home_assistant_api.auth import authenticate_admin, authenticate_device, hash_token
from home_assistant_api.errors import AppError, InternalError, ValidationError
from home_assistant_api.google.oauth_state import (
    create_signed_oauth_state,
    verify_signed_oauth_state,
)
from home_assistant_api.models import ErrorDetail, ErrorResponse, VoiceTurnRequest, VoiceTurnResponse
from home_assistant_api.time_utils import utc_now
from home_assistant_api.validation import validate_device_id, validate_uuid

_IDEMPOTENCY_HEADER = "Idempotency-Key"
_AUTHORIZATION_HEADER = "Authorization"
_ADMIN_KEY_HEADER = "x-admin-api-key"

# Bounds the request body read/decoded before any JSON parsing or base64
# decoding is attempted, so a hostile or buggy oversized payload cannot
# consume unbounded memory/CPU before validation even begins. Sized to
# comfortably exceed the contract's audioBase64 max length (8,388,608
# characters, ~8 MiB) plus JSON/header overhead.
_MAX_REQUEST_BODY_BYTES = 12 * 1024 * 1024

RouteHandler = Callable[[func.HttpRequest, AppContext, str], func.HttpResponse]


def with_error_handling(handler: RouteHandler) -> Callable[[func.HttpRequest, AppContext], func.HttpResponse]:
    @functools.wraps(handler)
    def wrapper(req: func.HttpRequest, ctx: AppContext) -> func.HttpResponse:
        correlation_id = str(uuid.uuid4())
        try:
            return handler(req, ctx, correlation_id)
        except AppError as exc:
            ctx.telemetry.track_exception(
                exc, {"correlation_id": correlation_id, "code": exc.code}
            )
            return _error_response(exc, correlation_id)
        except Exception as exc:  # last-resort HTTP boundary safety net; always logged
            ctx.telemetry.track_exception(exc, {"correlation_id": correlation_id})
            return _error_response(
                InternalError("An unexpected error occurred while processing the request."),
                correlation_id,
            )

    return wrapper


def _json_response(status_code: int, payload: dict[str, Any]) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload),
        status_code=status_code,
        mimetype="application/json",
    )


def _error_response(exc: AppError, correlation_id: str) -> func.HttpResponse:
    error_response = ErrorResponse(
        error=ErrorDetail(
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            details=exc.details,
        ),
        correlationId=correlation_id,
    )
    return _json_response(exc.http_status, error_response.to_wire_dict())


def _enforce_max_body_size(raw_body: bytes) -> None:
    if len(raw_body) > _MAX_REQUEST_BODY_BYTES:
        raise ValidationError(
            f"Request body exceeds the maximum allowed size of {_MAX_REQUEST_BODY_BYTES} bytes."
        )


def _parse_json_body(req: func.HttpRequest) -> dict[str, Any]:
    _enforce_max_body_size(req.get_body())
    try:
        body = req.get_json()
    except ValueError as exc:
        raise ValidationError("Request body must be valid JSON.") from exc
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object.")
    return body


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@with_error_handling
def health(req: func.HttpRequest, ctx: AppContext, correlation_id: str) -> func.HttpResponse:
    # Matches contracts/schemas' additionalProperties:false health object
    # exactly: no extra fields, ever.
    return _json_response(200, {"status": "ok"})


# ---------------------------------------------------------------------------
# Voice turn
# ---------------------------------------------------------------------------


def _validate_idempotency_key(raw_key: Optional[str]) -> str:
    if raw_key is None or not (8 <= len(raw_key) <= 128):
        raise ValidationError("Idempotency-Key header is required and must be 8-128 characters.")
    return raw_key


@with_error_handling
def voice_turn(req: func.HttpRequest, ctx: AppContext, correlation_id: str) -> func.HttpResponse:
    # Ordering is deliberate and security-relevant: the idempotency key is
    # only *reserved* after the request has been fully parsed, schema
    # validated, and the caller authenticated. Reserving earlier would let
    # an unauthenticated or malformed request poison a key that a
    # legitimate, correctly-authenticated retry then collides with.
    idempotency_key = _validate_idempotency_key(req.headers.get(_IDEMPOTENCY_HEADER))
    raw_body = req.get_body()
    _enforce_max_body_size(raw_body)
    fingerprint = hashlib.sha256(raw_body).hexdigest()

    body_dict = _parse_json_body(req)
    try:
        turn_request = VoiceTurnRequest.model_validate(body_dict)
    except PydanticValidationError as exc:
        raise ValidationError(f"Request body failed validation: {exc.errors()[0]['msg']}") from exc

    authenticate_device(
        authorization_header=req.headers.get(_AUTHORIZATION_HEADER),
        claimed_device_id=turn_request.device_id,
        devices_repository=ctx.devices_repo,
    )

    cached = ctx.idempotency_repo.reserve(idempotency_key, fingerprint)
    if cached is not None:
        return _json_response(cached.status_code, cached.response_body)

    reservation_completed = False
    try:
        ctx.devices_repo.touch_last_seen(turn_request.device_id)

        is_voice = turn_request.audio_base64 is not None
        if turn_request.text is not None:
            user_text = turn_request.text
        else:
            try:
                audio_bytes = base64.b64decode(turn_request.audio_base64, validate=True)
            except binascii.Error as exc:
                raise ValidationError("audioBase64 is not valid base64.") from exc
            stt_client = ctx.get_stt_client()
            user_text = stt_client.transcribe(
                audio_bytes,
                content_type=turn_request.audio_content_type or "audio/wav",
                locale=turn_request.locale,
            )

        session = ctx.sessions_repo.get_or_create(
            turn_request.device_id, turn_request.conversation_id
        )
        history = ctx.sessions_repo.get_history(
            session.device_id, session.conversation_id
        )

        system_prompt = build_system_prompt(
            device_id=turn_request.device_id,
            timezone=turn_request.timezone,
            now=utc_now(),
            google_configured=ctx.google_configured(),
        )
        chat_client, deployment = ctx.get_chat_client()
        orchestrator = AssistantOrchestrator(
            chat_client=chat_client,
            deployment=deployment,
            max_iterations=ctx.config.max_tool_iterations,
            telemetry=ctx.telemetry,
        )
        tool_context = ctx.build_tool_context(turn_request.device_id)
        result = orchestrator.run_turn(
            system_prompt=system_prompt,
            history=history,
            user_text=user_text,
            tool_context=tool_context,
        )
        for message in result.new_messages:
            ctx.sessions_repo.append_message(
                session.device_id, session.conversation_id, message
            )

        audio_base64: Optional[str] = None
        audio_content_type: Optional[str] = None
        if is_voice and result.reply_text:
            tts_client = ctx.get_tts_client()
            audio_bytes, content_type = tts_client.synthesize(
                result.reply_text, locale=turn_request.locale
            )
            audio_base64 = base64.b64encode(audio_bytes).decode("ascii")
            audio_content_type = content_type

        response_model = VoiceTurnResponse(
            requestId=turn_request.request_id,
            conversationId=session.conversation_id,
            text=result.reply_text,
            audioBase64=audio_base64,
            audioContentType=audio_content_type,
            actions=result.actions,
            correlationId=correlation_id,
        )
        wire = response_model.to_wire_dict()
        ctx.idempotency_repo.complete(
            idempotency_key, fingerprint, wire, 200, ctx.config.idempotency_ttl_seconds
        )
        reservation_completed = True
        return _json_response(200, wire)
    finally:
        # Any failure path (validation after this point, an upstream
        # dependency error, an unexpected exception) releases the
        # reservation so a client retry with the same Idempotency-Key and
        # body can succeed instead of being permanently stuck behind a
        # failed first attempt.
        if not reservation_completed:
            ctx.idempotency_repo.release(idempotency_key, fingerprint)


# ---------------------------------------------------------------------------
# Admin: device registration
# ---------------------------------------------------------------------------


@with_error_handling
def register_device(req: func.HttpRequest, ctx: AppContext, correlation_id: str) -> func.HttpResponse:
    authenticate_admin(
        provided_key=req.headers.get(_ADMIN_KEY_HEADER),
        expected_key=ctx.config.require_admin_api_key(),
    )
    body = _parse_json_body(req)
    device_id = validate_device_id(body.get("deviceId"))
    display_name = body.get("displayName")
    if not isinstance(display_name, str) or not display_name.strip():
        raise ValidationError("displayName is required.")

    token = secrets.token_urlsafe(32)
    record = ctx.devices_repo.register(device_id, display_name, hash_token(token))
    return _json_response(
        201,
        {
            "deviceId": record.device_id,
            "displayName": record.display_name,
            "registeredAt": record.registered_at,
            # Returned exactly once; provision it to the device out of band
            # and it is never retrievable again through this API.
            "token": token,
        },
    )


@with_error_handling
def list_devices(req: func.HttpRequest, ctx: AppContext, correlation_id: str) -> func.HttpResponse:
    authenticate_admin(
        provided_key=req.headers.get(_ADMIN_KEY_HEADER),
        expected_key=ctx.config.require_admin_api_key(),
    )
    devices = ctx.devices_repo.list_all()
    return _json_response(
        200,
        {
            "devices": [
                {
                    "deviceId": d.device_id,
                    "displayName": d.display_name,
                    "registeredAt": d.registered_at,
                    "lastSeenAt": d.last_seen_at,
                }
                for d in devices
            ]
        },
    )


# ---------------------------------------------------------------------------
# Reminders (polled by the Pi client's reminder poller)
# ---------------------------------------------------------------------------


def _authenticate_device_from_query(req: func.HttpRequest, ctx: AppContext) -> str:
    device_id = validate_device_id(req.params.get("deviceId"))
    return authenticate_device(
        authorization_header=req.headers.get(_AUTHORIZATION_HEADER),
        claimed_device_id=device_id,
        devices_repository=ctx.devices_repo,
    )


@with_error_handling
def list_due_reminders(req: func.HttpRequest, ctx: AppContext, correlation_id: str) -> func.HttpResponse:
    device_id = _authenticate_device_from_query(req, ctx)
    reminders = ctx.reminders_repo.list_due(device_id)
    return _json_response(
        200,
        {
            "reminders": [
                {"reminderId": r.reminder_id, "title": r.title, "dueAt": r.due_at}
                for r in reminders
            ]
        },
    )


@with_error_handling
def acknowledge_reminder(req: func.HttpRequest, ctx: AppContext, correlation_id: str) -> func.HttpResponse:
    body = _parse_json_body(req)
    device_id = validate_device_id(body.get("deviceId"))
    authenticate_device(
        authorization_header=req.headers.get(_AUTHORIZATION_HEADER),
        claimed_device_id=device_id,
        devices_repository=ctx.devices_repo,
    )
    reminder_id = validate_uuid(req.route_params.get("reminder_id"), field_name="reminder_id")
    reminder = ctx.reminders_repo.acknowledge(device_id, reminder_id)
    return _json_response(
        200, {"reminderId": reminder.reminder_id, "delivered": reminder.delivered}
    )


# ---------------------------------------------------------------------------
# Google OAuth (operator-driven)
# ---------------------------------------------------------------------------


@with_error_handling
def google_oauth_start(req: func.HttpRequest, ctx: AppContext, correlation_id: str) -> func.HttpResponse:
    authenticate_admin(
        provided_key=req.headers.get(_ADMIN_KEY_HEADER),
        expected_key=ctx.config.require_admin_api_key(),
    )
    device_id = validate_device_id(req.params.get("deviceId"))
    oauth_client = ctx.get_google_oauth_client()
    # Signed and expiring so the callback below can verify this state was
    # actually issued by this backend for this device id, rather than
    # trusting the caller-supplied device_id embedded in an unsigned state
    # value (which would let an attacker attach their own Google grant to
    # an arbitrary victim device).
    state = create_signed_oauth_state(device_id, ctx.config.oauth_state_signing_key())
    authorization_url = oauth_client.build_authorization_url(state=state)
    return _json_response(200, {"authorizationUrl": authorization_url})


@with_error_handling
def google_oauth_callback(req: func.HttpRequest, ctx: AppContext, correlation_id: str) -> func.HttpResponse:
    code = req.params.get("code")
    state = req.params.get("state")
    if not code or not state:
        raise ValidationError("code and state query parameters are required.")
    device_id = verify_signed_oauth_state(state, ctx.config.oauth_state_signing_key())

    oauth_client = ctx.get_google_oauth_client()
    credential_data = oauth_client.exchange_code(code=code, state=state)
    ctx.credential_store.save(device_id, credential_data)
    return _json_response(200, {"status": "connected", "deviceId": device_id})
