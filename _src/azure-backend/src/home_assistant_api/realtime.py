"""Microsoft Foundry Realtime bridge using Microsoft Entra authentication."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential
from openai import AsyncOpenAI

from .config import AppConfig, OUTPUT_SAMPLE_RATE

FOUNDRY_TOKEN_SCOPE = "https://ai.azure.com/.default"
FOUNDRY_HANDSHAKE_TIMEOUT_SECONDS = 30.0
logger = logging.getLogger(__name__)
JARVIS_QUERY = "JARVIS_QUERY"
JARVIS_SLEEP = "JARVIS_SLEEP"
FOLLOWUP_MAX_OUTPUT_TOKENS = 256
FOLLOWUP_INTENT_INSTRUCTIONS = (
    "Classify the user's follow-up audio by calling exactly one supplied function. "
    "Call jarvis_sleep when the user says they have no more questions, declines "
    "another query, asks to stop or sleep, says goodbye, says thanks or that's all, "
    "or the audio has no clear meaningful request. Call jarvis_query only when the "
    "audio contains a clear question or request that Jarvis should answer. If a "
    "clear new request accompanies a negation, call jarvis_query. Do not produce "
    "spoken or written output."
)
FOLLOWUP_INTENT_TOOLS = [
    {
        "type": "function",
        "name": "jarvis_sleep",
        "description": (
            "End the conversation because the user is done, declined another "
            "query, or did not provide a clear meaningful request."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "jarvis_query",
        "description": (
            "Continue because the user provided a clear question or request for "
            "Jarvis to answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
]


class FoundryRealtimeError(RuntimeError):
    """Raised when the upstream Realtime session fails."""


class FoundryRealtimeSession:
    """Owns one request-scoped Foundry Realtime WebSocket session."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._credential: Any = None
        self._client: AsyncOpenAI | None = None
        self._connection_manager: Any = None
        self._connection: Any = None

    async def open(self) -> None:
        if self._connection is not None:
            return

        try:
            async with asyncio.timeout(FOUNDRY_HANDSHAKE_TIMEOUT_SECONDS):
                self._credential = (
                    ManagedIdentityCredential()
                    if self._config.use_managed_identity
                    else DefaultAzureCredential()
                )
                token = await self._credential.get_token(FOUNDRY_TOKEN_SCOPE)
                self._client = AsyncOpenAI(
                    websocket_base_url=self._config.websocket_base_url,
                    api_key=token.token,
                )
                self._connection_manager = self._client.realtime.connect(
                    model=self._config.foundry_deployment
                )
                self._connection = await self._connection_manager.__aenter__()
                received_session_created = False
                async for event in self._connection:
                    event_type = getattr(event, "type", "")
                    if event_type == "session.created":
                        received_session_created = True
                        break
                    if event_type == "error":
                        error = getattr(event, "error", None)
                        message = getattr(
                            error, "message", "Unknown Realtime API error"
                        )
                        raise FoundryRealtimeError(str(message))
                if not received_session_created:
                    raise FoundryRealtimeError(
                        "Foundry closed the connection before creating the session."
                    )
                await self._connection.session.update(
                    session={
                        "type": "realtime",
                        "instructions": self._config.system_instructions,
                        "output_modalities": ["audio"],
                        "audio": {
                            "input": {
                                "format": {
                                    "type": "audio/pcm",
                                    "rate": OUTPUT_SAMPLE_RATE,
                                },
                                "turn_detection": None,
                            },
                            "output": {
                                "voice": self._config.foundry_voice,
                                "format": {
                                    "type": "audio/pcm",
                                    "rate": OUTPUT_SAMPLE_RATE,
                                },
                            },
                        },
                    }
                )
                received_session_updated = False
                async for event in self._connection:
                    event_type = getattr(event, "type", "")
                    if event_type == "session.updated":
                        received_session_updated = True
                        break
                    if event_type == "error":
                        error = getattr(event, "error", None)
                        message = getattr(
                            error, "message", "Unknown Realtime API error"
                        )
                        raise FoundryRealtimeError(str(message))
                if not received_session_updated:
                    raise FoundryRealtimeError(
                        "Foundry closed the connection before confirming the session."
                    )
        except FoundryRealtimeError:
            try:
                await self.close()
            except Exception:
                logger.exception("Realtime cleanup also failed while opening the session")
            raise
        except TimeoutError as exc:
            try:
                await self.close()
            except Exception:
                logger.exception("Realtime cleanup also failed after handshake timeout")
            raise FoundryRealtimeError(
                "Timed out while configuring the Foundry Realtime session."
            ) from exc
        except Exception as exc:
            try:
                await self.close()
            except Exception:
                logger.exception("Realtime cleanup also failed while opening the session")
            raise FoundryRealtimeError(
                "Could not open the Foundry Realtime session."
            ) from exc
        except BaseException:
            try:
                await self.close()
            except Exception:
                logger.exception("Realtime cleanup failed during cancellation")
            raise

    async def append_audio(self, pcm24: bytes) -> None:
        if not pcm24:
            return
        if self._connection is None:
            raise FoundryRealtimeError("Realtime session is not open.")
        try:
            await self._connection.input_audio_buffer.append(
                audio=base64.b64encode(pcm24).decode("ascii")
            )
        except Exception as exc:
            raise FoundryRealtimeError("Foundry rejected an audio chunk.") from exc

    async def commit_and_create_response(
        self,
        *,
        response_mode: Literal["audio", "followup_intent"] = "audio",
    ) -> None:
        if self._connection is None:
            raise FoundryRealtimeError("Realtime session is not open.")
        try:
            await self._connection.input_audio_buffer.commit()
            if response_mode == "followup_intent":
                await self._connection.response.create(
                    response={
                        "instructions": FOLLOWUP_INTENT_INSTRUCTIONS,
                        "output_modalities": ["text"],
                        "tools": FOLLOWUP_INTENT_TOOLS,
                        "tool_choice": "required",
                        "parallel_tool_calls": False,
                        "reasoning": {"effort": "minimal"},
                        "max_output_tokens": FOLLOWUP_MAX_OUTPUT_TOKENS,
                    }
                )
            else:
                await self._connection.response.create()
        except Exception as exc:
            raise FoundryRealtimeError(
                "Foundry could not start the requested response."
            ) from exc

    async def followup_intent(self) -> Literal["JARVIS_QUERY", "JARVIS_SLEEP"]:
        if self._connection is None:
            raise FoundryRealtimeError("Realtime session is not open.")

        calls: list[str] = []
        received_done = False
        terminal_status = ""
        terminal_reason = ""
        try:
            async with asyncio.timeout(self._config.response_timeout_seconds):
                async for event in self._connection:
                    event_type = getattr(event, "type", "")
                    if event_type == "response.function_call_arguments.done":
                        try:
                            arguments = json.loads(getattr(event, "arguments", ""))
                        except (TypeError, ValueError) as exc:
                            raise FoundryRealtimeError(
                                "Foundry returned malformed follow-up intent arguments."
                            ) from exc
                        if arguments != {}:
                            raise FoundryRealtimeError(
                                "Foundry follow-up intent arguments must be empty."
                            )
                        calls.append(str(getattr(event, "name", "")))
                    elif event_type == "error":
                        error = getattr(event, "error", None)
                        message = getattr(error, "message", "Unknown Realtime API error")
                        raise FoundryRealtimeError(str(message))
                    elif event_type == "response.done":
                        response = getattr(event, "response", None)
                        terminal_status = str(getattr(response, "status", "") or "")
                        status_details = getattr(response, "status_details", None)
                        terminal_reason = str(
                            getattr(status_details, "reason", "") or ""
                        )
                        received_done = True
                        break
        except TimeoutError as exc:
            raise FoundryRealtimeError(
                "Timed out waiting for Foundry follow-up intent."
            ) from exc
        except FoundryRealtimeError:
            raise
        except Exception as exc:
            raise FoundryRealtimeError(
                "Foundry follow-up intent connection ended unexpectedly."
            ) from exc

        if not received_done:
            raise FoundryRealtimeError(
                "Foundry connection ended before follow-up intent completed."
            )
        intent: Literal["JARVIS_QUERY", "JARVIS_SLEEP"] | None = None
        if calls == ["jarvis_query"]:
            intent = JARVIS_QUERY
        elif calls == ["jarvis_sleep"]:
            intent = JARVIS_SLEEP

        if terminal_status in {"", "completed"}:
            if intent is not None:
                return intent
            raise FoundryRealtimeError(
                "Foundry must return exactly one recognized follow-up intent."
            )

        status_detail = (
            f" (reason: {terminal_reason})" if terminal_reason else ""
        )
        if terminal_status == "incomplete":
            if intent is not None:
                logger.warning(
                    "Accepting completed follow-up intent from an incomplete "
                    "Foundry response reason=%s intent=%s",
                    terminal_reason or "unknown",
                    intent,
                )
                return intent
            logger.warning(
                "Foundry follow-up intent was incomplete without a usable tool "
                "call; defaulting to sleep reason=%s",
                terminal_reason or "unknown",
            )
            return JARVIS_SLEEP

        raise FoundryRealtimeError(
            f"Foundry response ended with status '{terminal_status}'"
            f"{status_detail}."
        )

    async def audio_chunks(self) -> AsyncIterator[bytes]:
        if self._connection is None:
            raise FoundryRealtimeError("Realtime session is not open.")

        received_audio = False
        received_done = False
        try:
            async with asyncio.timeout(self._config.response_timeout_seconds):
                async for event in self._connection:
                    event_type = getattr(event, "type", "")
                    if event_type in {
                        "response.output_audio.delta",
                        "response.audio.delta",
                    }:
                        try:
                            chunk = base64.b64decode(event.delta, validate=True)
                        except (AttributeError, binascii.Error, ValueError) as exc:
                            raise FoundryRealtimeError(
                                "Foundry returned an invalid audio delta."
                            ) from exc
                        if chunk:
                            received_audio = True
                            yield chunk
                    elif event_type == "error":
                        error = getattr(event, "error", None)
                        message = getattr(error, "message", "Unknown Realtime API error")
                        raise FoundryRealtimeError(str(message))
                    elif event_type == "response.done":
                        response = getattr(event, "response", None)
                        status = getattr(response, "status", "")
                        if status and status != "completed":
                            status_details = getattr(response, "status_details", None)
                            reason = getattr(status_details, "reason", "")
                            detail = f" (reason: {reason})" if reason else ""
                            raise FoundryRealtimeError(
                                f"Foundry response ended with status '{status}'"
                                f"{detail}."
                            )
                        received_done = True
                        break
        except TimeoutError as exc:
            raise FoundryRealtimeError("Timed out waiting for Foundry audio.") from exc
        except FoundryRealtimeError:
            raise
        except Exception as exc:
            raise FoundryRealtimeError(
                "Foundry Realtime connection ended unexpectedly."
            ) from exc

        if not received_done:
            raise FoundryRealtimeError(
                "Foundry Realtime connection ended before response.done."
            )
        if not received_audio:
            raise FoundryRealtimeError("Foundry completed without returning audio.")

    async def close(self) -> None:
        had_connection = self._connection is not None
        connection_manager, self._connection_manager = self._connection_manager, None
        self._connection = None
        client, self._client = self._client, None
        credential, self._credential = self._credential, None

        failures: list[Exception] = []
        for operation in (
            (
                lambda: connection_manager.__aexit__(None, None, None)
                if connection_manager is not None and had_connection
                else None
            ),
            (lambda: client.close() if client is not None else None),
            (lambda: credential.close() if credential is not None else None),
        ):
            try:
                pending = operation()
                if pending is not None:
                    await pending
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise FoundryRealtimeError(
                f"Failed to release {len(failures)} Realtime resource(s)."
            ) from failures[0]
