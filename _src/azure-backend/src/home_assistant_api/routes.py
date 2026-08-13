"""FastAPI-compatible Azure Functions streaming route handlers."""

from __future__ import annotations

import asyncio
import audioop
import logging
from collections.abc import AsyncIterator, Callable
from typing import Literal

from azurefunctions.extensions.http.fastapi import JSONResponse, Request, StreamingResponse

from .auth import DeviceAuthenticationError, authenticate_device
from .config import (
    AppConfig,
    ConfigurationError,
    INPUT_SAMPLE_RATE,
    MAX_INPUT_BYTES,
    OUTPUT_SAMPLE_RATE,
    SAMPLE_WIDTH_BYTES,
)
from .realtime import FoundryRealtimeError, FoundryRealtimeSession

logger = logging.getLogger(__name__)


class VoiceRequestError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class Pcm16Resampler:
    """Stateful mono PCM16 resampler that tolerates odd HTTP chunk boundaries."""

    def __init__(self, input_rate: int, output_rate: int) -> None:
        self._input_rate = input_rate
        self._output_rate = output_rate
        self._state = None
        self._pending = b""

    def feed(self, chunk: bytes) -> bytes:
        data = self._pending + chunk
        usable = len(data) - (len(data) % SAMPLE_WIDTH_BYTES)
        self._pending = data[usable:]
        if usable == 0:
            return b""
        converted, self._state = audioop.ratecv(
            data[:usable],
            SAMPLE_WIDTH_BYTES,
            1,
            self._input_rate,
            self._output_rate,
            self._state,
        )
        return converted

    def finish(self) -> None:
        if self._pending:
            raise VoiceRequestError("PCM16 request body has an incomplete final sample.")


def _json_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status_code,
    )


def _validate_audio_headers(request: Request) -> None:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "audio/pcm":
        raise VoiceRequestError("Content-Type must be audio/pcm.", 415)

    expected = {
        "x-audio-sample-rate": str(INPUT_SAMPLE_RATE),
        "x-audio-channels": "1",
        "x-audio-sample-width": str(SAMPLE_WIDTH_BYTES),
    }
    for name, value in expected.items():
        if request.headers.get(name) != value:
            raise VoiceRequestError(f"{name} header must be {value}.")


async def health(
    request: Request,
    *,
    config_loader: Callable[[], AppConfig] = AppConfig.from_environment,
    session_factory: Callable[[AppConfig], FoundryRealtimeSession] = FoundryRealtimeSession,
) -> JSONResponse:
    deep_value = request.query_params.get("deep", "").strip().lower()
    if deep_value not in {"", "0", "false", "1", "true"}:
        return _json_error(400, "invalid_health_check", "deep must be true or false.")
    if deep_value in {"", "0", "false"}:
        return JSONResponse({"status": "ok"})

    session: FoundryRealtimeSession | None = None
    response: JSONResponse
    try:
        config = config_loader()
        authenticate_device(request.headers.get("x-device-guid"), config.device_guid)
        session = session_factory(config)
        await session.open()
        response = JSONResponse({"status": "ok", "foundry": "ready"})
    except DeviceAuthenticationError as exc:
        response = _json_error(401, "unauthorized_device", str(exc))
    except ConfigurationError as exc:
        logger.exception("Deep health check configuration failed")
        response = _json_error(500, "configuration_error", str(exc))
    except FoundryRealtimeError as exc:
        logger.exception("Deep health check could not open Foundry Realtime")
        response = _json_error(502, "foundry_error", str(exc))
    except Exception:
        logger.exception("Unexpected deep health check failure")
        response = _json_error(500, "internal_error", "The deep health check failed.")

    if session is not None and not await _close_session(session):
        if response.status_code == 200:
            return _json_error(
                502,
                "foundry_cleanup_error",
                "The Foundry Realtime session could not be released.",
            )
    return response


async def _prepare_stream(
    request: Request,
    config: AppConfig,
    session: FoundryRealtimeSession,
    *,
    response_mode: Literal["audio", "followup_intent"] = "audio",
) -> None:
    authenticate_device(request.headers.get("x-device-guid"), config.device_guid)
    _validate_audio_headers(request)

    await session.open()
    resampler = Pcm16Resampler(INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE)
    received = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        received += len(chunk)
        if received > MAX_INPUT_BYTES:
            raise VoiceRequestError(
                "PCM request exceeds the 30-second maximum.", status_code=413
            )
        await session.append_audio(resampler.feed(bytes(chunk)))

    if received == 0:
        raise VoiceRequestError("PCM request body cannot be empty.")
    resampler.finish()
    await session.commit_and_create_response(response_mode=response_mode)


def _response_stream(
    session: FoundryRealtimeSession,
    first_chunk: bytes,
    remaining_chunks: AsyncIterator[bytes],
) -> AsyncIterator[bytes]:
    async def generate() -> AsyncIterator[bytes]:
        try:
            yield first_chunk
            async for chunk in remaining_chunks:
                yield chunk
        finally:
            await _close_session(session)

    return generate()


async def _close_session(session: FoundryRealtimeSession) -> bool:
    try:
        await session.close()
    except Exception:
        logger.exception("Could not completely release the Foundry Realtime session")
        return False
    return True


async def voice_stream(
    request: Request,
    *,
    config_loader: Callable[[], AppConfig] = AppConfig.from_environment,
    session_factory: Callable[[AppConfig], FoundryRealtimeSession] = FoundryRealtimeSession,
) -> StreamingResponse | JSONResponse:
    session: FoundryRealtimeSession | None = None
    try:
        config = config_loader()
        session = session_factory(config)
        await _prepare_stream(request, config, session)
        upstream_chunks = session.audio_chunks()
        first_chunk = await anext(upstream_chunks)
        return StreamingResponse(
            _response_stream(session, first_chunk, upstream_chunks),
            status_code=200,
            media_type="audio/pcm",
            headers={
                "Cache-Control": "no-store",
                "X-Audio-Sample-Rate": str(OUTPUT_SAMPLE_RATE),
                "X-Audio-Channels": "1",
                "X-Audio-Sample-Width": str(SAMPLE_WIDTH_BYTES),
            },
        )
    except DeviceAuthenticationError as exc:
        if session is not None:
            await _close_session(session)
        return _json_error(401, "unauthorized_device", str(exc))
    except (ConfigurationError, VoiceRequestError) as exc:
        if session is not None:
            await _close_session(session)
        status = exc.status_code if isinstance(exc, VoiceRequestError) else 500
        code = "invalid_audio" if status < 500 else "configuration_error"
        logger.warning("Voice request rejected: %s", exc)
        return _json_error(status, code, str(exc))
    except FoundryRealtimeError as exc:
        if session is not None:
            await _close_session(session)
        logger.exception("Foundry Realtime request failed")
        return _json_error(502, "foundry_error", str(exc))
    except asyncio.CancelledError:
        if session is not None:
            await _close_session(session)
        raise
    except Exception:
        if session is not None:
            await _close_session(session)
        logger.exception("Unexpected voice-stream failure")
        return _json_error(500, "internal_error", "The voice request failed.")


async def voice_intent(
    request: Request,
    *,
    config_loader: Callable[[], AppConfig] = AppConfig.from_environment,
    session_factory: Callable[[AppConfig], FoundryRealtimeSession] = FoundryRealtimeSession,
) -> JSONResponse:
    session: FoundryRealtimeSession | None = None
    try:
        config = config_loader()
        session = session_factory(config)
        await _prepare_stream(
            request,
            config,
            session,
            response_mode="followup_intent",
        )
        intent = await session.followup_intent()
        if not await _close_session(session):
            return _json_error(
                502,
                "foundry_cleanup_error",
                "The Foundry Realtime session could not be released.",
            )
        session = None
        return JSONResponse(
            {"intent": intent},
            headers={"Cache-Control": "no-store"},
        )
    except DeviceAuthenticationError as exc:
        if session is not None:
            await _close_session(session)
        return _json_error(401, "unauthorized_device", str(exc))
    except (ConfigurationError, VoiceRequestError) as exc:
        if session is not None:
            await _close_session(session)
        status = exc.status_code if isinstance(exc, VoiceRequestError) else 500
        code = "invalid_audio" if status < 500 else "configuration_error"
        logger.warning("Follow-up intent request rejected: %s", exc)
        return _json_error(status, code, str(exc))
    except FoundryRealtimeError as exc:
        if session is not None:
            await _close_session(session)
        logger.exception("Foundry follow-up intent request failed")
        return _json_error(502, "foundry_error", str(exc))
    except asyncio.CancelledError:
        if session is not None:
            await _close_session(session)
        raise
    except Exception:
        if session is not None:
            await _close_session(session)
        logger.exception("Unexpected voice-intent failure")
        return _json_error(500, "internal_error", "The intent request failed.")
