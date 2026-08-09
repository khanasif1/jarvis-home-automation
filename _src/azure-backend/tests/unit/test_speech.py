from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
import requests

from home_assistant_api.config import SpeechConfig
from home_assistant_api.errors import UpstreamServiceError, ValidationError
from home_assistant_api.speech.stt import AzureSpeechToTextClient
from home_assistant_api.speech.tts import AzureTextToSpeechClient


@dataclass
class FakeResponse:
    status_code: int
    _json: dict[str, Any] | None = None
    content: bytes = b""

    def json(self) -> dict[str, Any]:
        return self._json or {}


@dataclass
class FakeSession:
    response: Any = None
    exception: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def post(self, url: str, **kwargs: Any):
        self.calls.append({"url": url, **kwargs})
        if self.exception is not None:
            raise self.exception
        return self.response


SPEECH_CONFIG = SpeechConfig(region="eastus", api_key="fake-key", default_voice="en-US-JennyNeural")


class TestAzureSpeechToTextClient:
    def test_rejects_empty_audio(self):
        client = AzureSpeechToTextClient(SPEECH_CONFIG, session=FakeSession())
        with pytest.raises(ValidationError):
            client.transcribe(b"", content_type="audio/wav", locale="en-US")

    def test_rejects_oversized_audio(self):
        client = AzureSpeechToTextClient(SPEECH_CONFIG, session=FakeSession())
        with pytest.raises(ValidationError):
            client.transcribe(b"x" * (9 * 1024 * 1024), content_type="audio/wav", locale="en-US")

    def test_successful_transcription(self):
        session = FakeSession(
            response=FakeResponse(200, {"RecognitionStatus": "Success", "DisplayText": "turn on the lights"})
        )
        client = AzureSpeechToTextClient(SPEECH_CONFIG, session=session)
        text = client.transcribe(b"fake-audio-bytes", content_type="audio/wav", locale="en-US")
        assert text == "turn on the lights"
        assert session.calls[0]["headers"]["Ocp-Apim-Subscription-Key"] == "fake-key"

    def test_non_200_raises_upstream_error(self):
        session = FakeSession(response=FakeResponse(500))
        client = AzureSpeechToTextClient(SPEECH_CONFIG, session=session)
        with pytest.raises(UpstreamServiceError):
            client.transcribe(b"fake-audio-bytes", content_type="audio/wav", locale="en-US")

    def test_recognition_failure_raises_validation_error(self):
        session = FakeSession(response=FakeResponse(200, {"RecognitionStatus": "NoMatch"}))
        client = AzureSpeechToTextClient(SPEECH_CONFIG, session=session)
        with pytest.raises(ValidationError):
            client.transcribe(b"fake-audio-bytes", content_type="audio/wav", locale="en-US")

    def test_transport_failure_raises_upstream_error(self):
        session = FakeSession(exception=requests.ConnectionError("network down"))
        client = AzureSpeechToTextClient(SPEECH_CONFIG, session=session)
        with pytest.raises(UpstreamServiceError):
            client.transcribe(b"fake-audio-bytes", content_type="audio/wav", locale="en-US")


class TestAzureTextToSpeechClient:
    def test_rejects_empty_text(self):
        client = AzureTextToSpeechClient(SPEECH_CONFIG, session=FakeSession())
        with pytest.raises(ValidationError):
            client.synthesize("   ", locale="en-US")

    def test_successful_synthesis(self):
        session = FakeSession(response=FakeResponse(200, content=b"mp3-bytes"))
        client = AzureTextToSpeechClient(SPEECH_CONFIG, session=session)
        audio, content_type = client.synthesize("Hello there", locale="en-US")
        assert audio == b"mp3-bytes"
        assert content_type == "audio/mpeg"

    def test_non_200_raises_upstream_error(self):
        session = FakeSession(response=FakeResponse(401))
        client = AzureTextToSpeechClient(SPEECH_CONFIG, session=session)
        with pytest.raises(UpstreamServiceError):
            client.synthesize("Hello there", locale="en-US")

    def test_transport_failure_raises_upstream_error(self):
        session = FakeSession(exception=requests.Timeout("slow"))
        client = AzureTextToSpeechClient(SPEECH_CONFIG, session=session)
        with pytest.raises(UpstreamServiceError):
            client.synthesize("Hello there", locale="en-US")
