"""Microsoft Foundry Realtime bridge using Microsoft Entra authentication."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from collections.abc import AsyncIterator
from typing import Any

from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential
from openai import AsyncOpenAI

from .config import AppConfig, OUTPUT_SAMPLE_RATE

FOUNDRY_TOKEN_SCOPE = "https://ai.azure.com/.default"
logger = logging.getLogger(__name__)


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

    async def commit_and_create_response(self) -> None:
        if self._connection is None:
            raise FoundryRealtimeError("Realtime session is not open.")
        try:
            await self._connection.input_audio_buffer.commit()
            await self._connection.response.create()
        except Exception as exc:
            raise FoundryRealtimeError(
                "Foundry could not start the audio response."
            ) from exc

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
                        status = getattr(getattr(event, "response", None), "status", "")
                        if status and status != "completed":
                            raise FoundryRealtimeError(
                                f"Foundry response ended with status '{status}'."
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
