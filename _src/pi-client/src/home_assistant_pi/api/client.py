"""Chunked PCM upload and streamed PCM response client."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from typing import Any

import requests

from ..audio.vad import NoSpeechDetected
from ..config import INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE, SAMPLE_WIDTH_BYTES


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ApiClient:
    def __init__(
        self,
        base_url: str,
        device_guid: str,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._device_guid = device_guid
        self._session = session or requests.Session()

    def health(self) -> bool:
        try:
            response = self._session.get(f"{self.base_url}/health", timeout=(5, 5))
            return response.status_code == 200 and response.json() == {"status": "ok"}
        except (requests.RequestException, ValueError):
            return False

    @contextlib.contextmanager
    def voice_response(self, audio_chunks: Iterator[bytes]) -> Iterator[Iterator[bytes]]:
        response: requests.Response | None = None
        try:
            response = self._session.post(
                f"{self.base_url}/voice/stream",
                data=audio_chunks,
                headers={
                    "Content-Type": "audio/pcm",
                    "X-Device-Guid": self._device_guid,
                    "X-Audio-Sample-Rate": str(INPUT_SAMPLE_RATE),
                    "X-Audio-Channels": "1",
                    "X-Audio-Sample-Width": str(SAMPLE_WIDTH_BYTES),
                },
                timeout=(10, 75),
                stream=True,
                allow_redirects=False,
            )
        except NoSpeechDetected:
            raise
        except requests.RequestException as exc:
            if getattr(audio_chunks, "no_speech_detected", False):
                raise NoSpeechDetected("No command speech was detected.") from exc
            raise ApiError(f"Voice request failed: {exc}") from exc
        finally:
            close = getattr(audio_chunks, "close", None)
            if callable(close):
                close()

        try:
            if response.status_code != 200:
                raise ApiError(
                    self._error_message(response),
                    status_code=response.status_code,
                )
            expected = {
                "X-Audio-Sample-Rate": str(OUTPUT_SAMPLE_RATE),
                "X-Audio-Channels": "1",
                "X-Audio-Sample-Width": str(SAMPLE_WIDTH_BYTES),
            }
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if media_type != "audio/pcm":
                raise ApiError("Backend response Content-Type is not audio/pcm.")
            for name, value in expected.items():
                if response.headers.get(name) != value:
                    raise ApiError(f"Backend response {name} header must be {value}.")
            yield response.iter_content(chunk_size=4_800)
        finally:
            response.close()

    @staticmethod
    def _error_message(response: requests.Response) -> str:
        try:
            payload: Any = response.json()
            if isinstance(payload, dict):
                error = payload.get("error", {})
                if isinstance(error, dict) and error.get("message"):
                    return str(error["message"])
        except (ValueError, json.JSONDecodeError):
            pass
        return f"Backend returned HTTP {response.status_code}."

    def close(self) -> None:
        self._session.close()
