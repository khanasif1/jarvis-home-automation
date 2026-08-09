"""Speech-to-text adapter.

Defines an explicit interface so the orchestrator never depends on a
concrete vendor SDK, plus a REST-based Azure AI Speech implementation. Using
the plain REST short-audio recognition endpoint (rather than the native
Speech SDK) avoids a platform-specific binary dependency inside the Azure
Functions Python worker and keeps the adapter trivially fakeable in tests.
"""

from __future__ import annotations

from typing import Protocol

import requests

from home_assistant_api.config import SpeechConfig
from home_assistant_api.errors import UpstreamServiceError, ValidationError

_RECOGNITION_PATH = "/speech/recognition/conversation/cognitiveservices/v1"
_MAX_AUDIO_BYTES = 8 * 1024 * 1024


class SpeechToTextClient(Protocol):
    def transcribe(self, audio_bytes: bytes, *, content_type: str, locale: str) -> str:
        """Transcribe ``audio_bytes`` and return the recognized text.

        Raises:
            ValidationError: If the audio could not be understood/recognized.
            UpstreamServiceError: If the speech service call itself failed.
        """


class AzureSpeechToTextClient:
    """Calls the Azure AI Speech short-audio REST recognition endpoint."""

    def __init__(self, config: SpeechConfig, *, session: "requests.Session | None" = None) -> None:
        self._config = config
        self._session = session or requests.Session()

    def transcribe(self, audio_bytes: bytes, *, content_type: str, locale: str) -> str:
        if not audio_bytes:
            raise ValidationError("Audio payload must not be empty.")
        if len(audio_bytes) > _MAX_AUDIO_BYTES:
            raise ValidationError("Audio payload exceeds the maximum allowed size.")

        url = f"https://{self._config.region}.stt.speech.microsoft.com{_RECOGNITION_PATH}"
        try:
            response = self._session.post(
                url,
                params={"language": locale, "format": "detailed"},
                headers={
                    "Ocp-Apim-Subscription-Key": self._config.api_key,
                    "Content-Type": _wav_content_type(content_type),
                    "Accept": "application/json",
                },
                data=audio_bytes,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise UpstreamServiceError("Speech-to-text request failed.") from exc

        if response.status_code != 200:
            raise UpstreamServiceError(
                f"Speech-to-text service returned status {response.status_code}."
            )

        payload = response.json()
        status = payload.get("RecognitionStatus")
        if status != "Success":
            raise ValidationError(f"Speech could not be recognized (status={status}).")

        text = payload.get("DisplayText")
        if not text:
            raise ValidationError("Speech recognition returned no text.")
        return text


def _wav_content_type(content_type: str) -> str:
    return "audio/wav; codecs=audio/pcm; samplerate=16000" if "wav" in content_type else content_type
