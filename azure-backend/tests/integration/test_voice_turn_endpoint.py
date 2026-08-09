from __future__ import annotations

import base64
import json
import uuid

from home_assistant_api import routes

from tests.conftest import FakeChatClient, FakeToolCall, text_response, tool_call_response
from tests.integration.helpers import make_request

DEVICE_TOKEN = "device-one-token-0123456789"
VALID_REQUEST_ID = "11111111-1111-1111-1111-111111111111"


class FakeSTTClient:
    def __init__(self, text: str = "what's on my todo list"):
        self._text = text
        self.calls = 0

    def transcribe(self, audio_bytes: bytes, *, content_type: str, locale: str) -> str:
        self.calls += 1
        return self._text


class FakeTTSClient:
    def __init__(self):
        self.calls = 0

    def synthesize(self, text: str, *, locale: str, voice: str | None = None):
        self.calls += 1
        return (b"fake-mp3-bytes", "audio/mpeg")


def _voice_turn_context(app_context_factory, full_env, *, chat_responses, **overrides):
    return app_context_factory(
        full_env,
        chat_client=FakeChatClient(chat_responses),
        chat_deployment="test-deployment",
        **overrides,
    )


def _text_request(*, idempotency_key="idem-key-0001", request_id=VALID_REQUEST_ID, text="Hello there", device_id="device-one", auth=True):
    headers = {"Idempotency-Key": idempotency_key}
    if auth:
        headers["Authorization"] = f"Bearer {DEVICE_TOKEN}"
    return make_request(
        method="POST",
        url="http://localhost/api/voice-turn",
        headers=headers,
        json_body={
            "requestId": request_id,
            "deviceId": device_id,
            "timezone": "UTC",
            "text": text,
        },
    )


class TestVoiceTurnHappyPath:
    def test_text_only_turn_returns_reply_and_no_audio(self, app_context_factory, full_env):
        ctx = _voice_turn_context(
            app_context_factory, full_env, chat_responses=[text_response("Hi! How can I help?")]
        )
        req = _text_request()
        response = routes.voice_turn(req, ctx)
        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["text"] == "Hi! How can I help?"
        assert "audioBase64" not in body
        assert body["requestId"] == VALID_REQUEST_ID
        assert body["actions"] == []

    def test_text_turn_with_tool_call_returns_action(self, app_context_factory, full_env):
        ctx = _voice_turn_context(
            app_context_factory,
            full_env,
            chat_responses=[
                tool_call_response([FakeToolCall("call-1", "create_todo", '{"title": "Buy milk"}')]),
                text_response("Added buy milk to your todos."),
            ],
        )
        req = _text_request(text="Add buy milk to my todos")
        response = routes.voice_turn(req, ctx)
        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert len(body["actions"]) == 1
        assert body["actions"][0]["type"] == "create_todo"
        assert body["actions"][0]["status"] == "completed"

    def test_audio_turn_transcribes_and_synthesizes_reply(self, app_context_factory, full_env):
        stt = FakeSTTClient(text="what's on my todo list")
        tts = FakeTTSClient()
        ctx = _voice_turn_context(
            app_context_factory,
            full_env,
            chat_responses=[text_response("You have no todos.")],
            stt_client=stt,
            tts_client=tts,
        )
        audio_b64 = base64.b64encode(b"fake-wav-bytes").decode("ascii")
        req = make_request(
            method="POST",
            url="http://localhost/api/voice-turn",
            headers={"Idempotency-Key": "idem-key-audio", "Authorization": f"Bearer {DEVICE_TOKEN}"},
            json_body={
                "requestId": VALID_REQUEST_ID,
                "deviceId": "device-one",
                "timezone": "UTC",
                "audioBase64": audio_b64,
                "audioContentType": "audio/wav",
            },
        )
        response = routes.voice_turn(req, ctx)
        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["text"] == "You have no todos."
        assert body["audioBase64"] == base64.b64encode(b"fake-mp3-bytes").decode("ascii")
        assert body["audioContentType"] == "audio/mpeg"
        assert stt.calls == 1
        assert tts.calls == 1


class TestVoiceTurnIdempotency:
    def test_replaying_same_key_and_body_returns_cached_response_without_reinvoking_model(
        self, app_context_factory, full_env
    ):
        ctx = _voice_turn_context(
            app_context_factory, full_env, chat_responses=[text_response("First reply.")]
        )
        req1 = _text_request(idempotency_key="replay-key")
        first = routes.voice_turn(req1, ctx)
        assert first.status_code == 200

        req2 = _text_request(idempotency_key="replay-key")
        second = routes.voice_turn(req2, ctx)
        assert second.status_code == 200
        assert json.loads(second.get_body()) == json.loads(first.get_body())
        # Only one chat completion call should have been made; the fake would
        # raise if a second call were attempted since only one response was queued.
        assert len(ctx._chat_client.chat.completions.calls) == 1

    def test_replaying_same_key_with_different_body_conflicts(self, app_context_factory, full_env):
        ctx = _voice_turn_context(
            app_context_factory, full_env, chat_responses=[text_response("First reply.")]
        )
        req1 = _text_request(idempotency_key="conflict-key", text="Hello there")
        first = routes.voice_turn(req1, ctx)
        assert first.status_code == 200

        req2 = _text_request(idempotency_key="conflict-key", text="A different message")
        second = routes.voice_turn(req2, ctx)
        assert second.status_code == 409


class TestVoiceTurnValidation:
    def test_missing_idempotency_key_is_validation_error(self, app_context_factory, full_env):
        ctx = _voice_turn_context(app_context_factory, full_env, chat_responses=[])
        req = make_request(
            method="POST",
            url="http://localhost/api/voice-turn",
            headers={"Authorization": f"Bearer {DEVICE_TOKEN}"},
            json_body={
                "requestId": VALID_REQUEST_ID,
                "deviceId": "device-one",
                "timezone": "UTC",
                "text": "Hello",
            },
        )
        response = routes.voice_turn(req, ctx)
        assert response.status_code == 400

    def test_too_short_idempotency_key_is_validation_error(self, app_context_factory, full_env):
        ctx = _voice_turn_context(app_context_factory, full_env, chat_responses=[])
        req = _text_request(idempotency_key="short")
        response = routes.voice_turn(req, ctx)
        assert response.status_code == 400

    def test_missing_text_and_audio_is_validation_error(self, app_context_factory, full_env):
        ctx = _voice_turn_context(app_context_factory, full_env, chat_responses=[])
        req = make_request(
            method="POST",
            url="http://localhost/api/voice-turn",
            headers={"Idempotency-Key": "idem-key-0002", "Authorization": f"Bearer {DEVICE_TOKEN}"},
            json_body={"requestId": VALID_REQUEST_ID, "deviceId": "device-one", "timezone": "UTC"},
        )
        response = routes.voice_turn(req, ctx)
        assert response.status_code == 400

    def test_malformed_json_body_is_validation_error(self, app_context_factory, full_env):
        ctx = _voice_turn_context(app_context_factory, full_env, chat_responses=[])
        req = make_request(
            method="POST",
            url="http://localhost/api/voice-turn",
            headers={
                "Idempotency-Key": "idem-key-0003",
                "Authorization": f"Bearer {DEVICE_TOKEN}",
                "Content-Type": "application/json",
            },
            body=b"not-json",
        )
        response = routes.voice_turn(req, ctx)
        assert response.status_code == 400


class TestVoiceTurnAuthentication:
    def test_missing_authorization_header_is_401(self, app_context_factory, full_env):
        ctx = _voice_turn_context(app_context_factory, full_env, chat_responses=[])
        req = _text_request(auth=False)
        response = routes.voice_turn(req, ctx)
        assert response.status_code == 401

    def test_wrong_token_is_401(self, app_context_factory, full_env):
        ctx = _voice_turn_context(app_context_factory, full_env, chat_responses=[])
        req = make_request(
            method="POST",
            url="http://localhost/api/voice-turn",
            headers={"Idempotency-Key": "idem-key-0004", "Authorization": "Bearer wrong-token"},
            json_body={
                "requestId": VALID_REQUEST_ID,
                "deviceId": "device-one",
                "timezone": "UTC",
                "text": "Hello",
            },
        )
        response = routes.voice_turn(req, ctx)
        assert response.status_code == 401

    def test_unknown_device_is_401(self, app_context_factory, full_env):
        ctx = _voice_turn_context(app_context_factory, full_env, chat_responses=[])
        req = make_request(
            method="POST",
            url="http://localhost/api/voice-turn",
            headers={"Idempotency-Key": "idem-key-0005", "Authorization": f"Bearer {DEVICE_TOKEN}"},
            json_body={
                "requestId": VALID_REQUEST_ID,
                "deviceId": "unknown-device",
                "timezone": "UTC",
                "text": "Hello",
            },
        )
        response = routes.voice_turn(req, ctx)
        assert response.status_code == 401


class TestVoiceTurnRequestBodySize:
    def test_oversized_request_body_is_rejected_before_json_parsing(
        self, app_context_factory, full_env
    ):
        ctx = _voice_turn_context(app_context_factory, full_env, chat_responses=[])
        # Borrow the real Authorization header value from the existing
        # helper instead of retyping it.
        template = _text_request(idempotency_key="oversized-key")
        oversized_body = b"{" + (b"a" * (13 * 1024 * 1024)) + b"}"
        req = make_request(
            method="POST",
            url="http://localhost/api/voice-turn",
            headers={
                "Idempotency-Key": "oversized-key",
                "Authorization": template.headers.get("Authorization"),
                "Content-Type": "application/json",
            },
            body=oversized_body,
        )
        response = routes.voice_turn(req, ctx)
        assert response.status_code == 400
        # And it must not have reserved the idempotency key: a legitimate
        # request reusing the same key afterwards must succeed, not conflict.
        follow_up_ctx = _voice_turn_context(
            app_context_factory, full_env, chat_responses=[text_response("Hi there.")]
        )
        follow_up = _text_request(idempotency_key="oversized-key")
        follow_up_response = routes.voice_turn(follow_up, follow_up_ctx)
        assert follow_up_response.status_code == 200


class TestVoiceTurnAuthBeforeIdempotencyReservation:
    """Regression tests: reservation must happen strictly after
    authentication, so an unauthenticated or invalid request can never
    poison an Idempotency-Key that a legitimate retry then collides with."""

    def test_unauthenticated_request_does_not_poison_idempotency_key(
        self, app_context_factory, full_env
    ):
        ctx = _voice_turn_context(
            app_context_factory, full_env, chat_responses=[text_response("Hi there.")]
        )
        unauthenticated = _text_request(idempotency_key="shared-key", auth=False)
        first = routes.voice_turn(unauthenticated, ctx)
        assert first.status_code == 401

        legitimate = _text_request(idempotency_key="shared-key")
        second = routes.voice_turn(legitimate, ctx)
        assert second.status_code == 200
        body = json.loads(second.get_body())
        assert body["text"] == "Hi there."

    def test_unknown_device_request_does_not_poison_idempotency_key(
        self, app_context_factory, full_env
    ):
        ctx = _voice_turn_context(
            app_context_factory, full_env, chat_responses=[text_response("Hi there.")]
        )
        template = _text_request(idempotency_key="shared-key-2")
        bad_device = make_request(
            method="POST",
            url="http://localhost/api/voice-turn",
            headers={
                "Idempotency-Key": "shared-key-2",
                "Authorization": template.headers.get("Authorization"),
            },
            json_body={
                "requestId": VALID_REQUEST_ID,
                "deviceId": "unknown-device",
                "timezone": "UTC",
                "text": "Hello",
            },
        )
        first = routes.voice_turn(bad_device, ctx)
        assert first.status_code == 401

        legitimate = _text_request(idempotency_key="shared-key-2")
        second = routes.voice_turn(legitimate, ctx)
        assert second.status_code == 200

    def test_malformed_json_body_does_not_poison_idempotency_key(
        self, app_context_factory, full_env
    ):
        ctx = _voice_turn_context(
            app_context_factory, full_env, chat_responses=[text_response("Hi there.")]
        )
        template = _text_request(idempotency_key="shared-key-3")
        malformed = make_request(
            method="POST",
            url="http://localhost/api/voice-turn",
            headers={
                "Idempotency-Key": "shared-key-3",
                "Authorization": template.headers.get("Authorization"),
                "Content-Type": "application/json",
            },
            body=b"not-json",
        )
        first = routes.voice_turn(malformed, ctx)
        assert first.status_code == 400

        legitimate = _text_request(idempotency_key="shared-key-3")
        second = routes.voice_turn(legitimate, ctx)
        assert second.status_code == 200


class TestVoiceTurnReleaseOnFailureAllowsRetry:
    def test_failure_during_processing_releases_reservation_so_retry_succeeds(
        self, app_context_factory, full_env
    ):
        class _FailingSTTClient:
            def __init__(self) -> None:
                self.calls = 0

            def transcribe(self, audio_bytes: bytes, *, content_type: str, locale: str) -> str:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("simulated transient STT failure")
                return "what's on my todo list"

        stt = _FailingSTTClient()
        tts = FakeTTSClient()
        ctx = _voice_turn_context(
            app_context_factory,
            full_env,
            chat_responses=[text_response("You have no todos.")],
            stt_client=stt,
            tts_client=tts,
        )
        audio_b64 = base64.b64encode(b"fake-wav-bytes").decode("ascii")
        template = _text_request(idempotency_key="retry-key")

        def _audio_request():
            return make_request(
                method="POST",
                url="http://localhost/api/voice-turn",
                headers={
                    "Idempotency-Key": "retry-key",
                    "Authorization": template.headers.get("Authorization"),
                },
                json_body={
                    "requestId": VALID_REQUEST_ID,
                    "deviceId": "device-one",
                    "timezone": "UTC",
                    "audioBase64": audio_b64,
                    "audioContentType": "audio/wav",
                },
            )

        first = routes.voice_turn(_audio_request(), ctx)
        assert first.status_code == 500

        second = routes.voice_turn(_audio_request(), ctx)
        assert second.status_code == 200
        body = json.loads(second.get_body())
        assert body["text"] == "You have no todos."
        assert stt.calls == 2
