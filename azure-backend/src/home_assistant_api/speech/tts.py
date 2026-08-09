"""Text-to-speech adapter (Azure AI Speech REST synthesis)."""

from __future__ import annotations

from typing import Protocol
from xml.sax.saxutils import escape

import requests

from home_assistant_api.config import SpeechConfig
from home_assistant_api.errors import UpstreamServiceError, ValidationError

_SYNTHESIS_FORMAT = "audio-16khz-32kbitrate-mono-mp3"
_RESPONSE_CONTENT_TYPE = "audio/mpeg"


class TextToSpeechClient(Protocol):
    def synthesize(self, text: str, *, locale: str, voice: str | None = None) -> tuple[bytes, str]:
        """Synthesize ``text`` to audio.

        Returns:
            A ``(audio_bytes, content_type)`` tuple.

        Raises:
            ValidationError: If ``text`` is empty.
            UpstreamServiceError: If the speech service call failed.
        """


class AzureTextToSpeechClient:
    """Calls the Azure AI Speech REST synthesis endpoint."""

    def __init__(self, config: SpeechConfig, *, session: "requests.Session | None" = None) -> None:
        self._config = config
        self._session = session or requests.Session()

    def synthesize(self, text: str, *, locale: str, voice: str | None = None) -> tuple[bytes, str]:
        if not text or not text.strip():
            raise ValidationError("Text to synthesize must not be empty.")

        voice_name = voice or self._config.default_voice
        ssml = (
            f'<speak version="1.0" xml:lang="{escape(locale)}">'
            f'<voice name="{escape(voice_name)}">{escape(text)}</voice>'
            "</speak>"
        )
        url = f"https://{self._config.region}.tts.speech.microsoft.com/cognitiveservices/v1"
        try:
            response = self._session.post(
                url,
                headers={
                    "Ocp-Apim-Subscription-Key": self._config.api_key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": _SYNTHESIS_FORMAT,
                    "User-Agent": "home-assistant-backend",
                },
                data=ssml.encode("utf-8"),
                timeout=15,
            )
        except requests.RequestException as exc:
            raise UpstreamServiceError("Text-to-speech request failed.") from exc

        if response.status_code != 200:
            raise UpstreamServiceError(
                f"Text-to-speech service returned status {response.status_code}."
            )
        return response.content, _RESPONSE_CONTENT_TYPE
