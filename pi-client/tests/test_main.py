"""Tests for home_assistant_pi.main (Application orchestration)."""

from __future__ import annotations

import base64

import pytest

from home_assistant_pi.api.models import Reminder, VoiceTurnResponse
from home_assistant_pi.audio.wav import WavAudio, wav_bytes
from home_assistant_pi.config import Config
from home_assistant_pi.main import (
    Application,
    _listen_for_wakeword,
    _run_streaming_loop,
    ensure_wakeword_engine_is_usable,
)
from home_assistant_pi.state_machine import Event, State, StateMachine
from home_assistant_pi.wakeword.base import WakewordError


class FakeApiClient:
    def __init__(self, response=None, raise_error=None):
        self.response = response
        self.raise_error = raise_error
        self.sent_requests = []

    def send_voice_turn(self, request):
        self.sent_requests.append(request)
        if self.raise_error is not None:
            raise self.raise_error
        return self.response


class FakeCapture:
    def __init__(self, audio=None, raise_error=None, chunks=None):
        self.audio = audio or WavAudio(frames=b"\x00\x00" * 10, sample_rate=16000)
        self.raise_error = raise_error
        self.calls = 0
        self._chunks = chunks or []
        self.stream_open_count = 0
        self.stream_close_count = 0
        self.stream_open_during_record = None

    def record_utterance(self, vad=None, max_seconds=15.0):
        self.calls += 1
        self.stream_open_during_record = self.stream_open_count > self.stream_close_count
        if self.raise_error is not None:
            raise self.raise_error
        return self.audio

    def stream_chunks(self, frame_length):
        self.stream_open_count += 1
        try:
            for chunk in self._chunks:
                yield chunk
        finally:
            self.stream_close_count += 1


class FakePlayback:
    def __init__(self):
        self.played = []

    def play(self, audio, block=True):
        self.played.append(audio)


class FakeWakewordDetector:
    def __init__(self, detect_on=None):
        self.detect_on = detect_on or set()
        self.processed = []
        self.closed = False

    def frame_length(self):
        return 320

    def sample_rate(self):
        return 16000

    def process(self, chunk):
        self.processed.append(chunk)
        return chunk in self.detect_on

    def close(self):
        self.closed = True


def make_config(**overrides) -> Config:
    defaults = dict(
        device_id="pi-1",
        device_token="secret",
        api_base_url="https://api.example.com/api",
    )
    defaults.update(overrides)
    return Config(**defaults)


def make_app(**kwargs) -> Application:
    return Application(
        config=kwargs.get("config") or make_config(),
        api_client=kwargs.get("api_client") or FakeApiClient(),
        capture=kwargs.get("capture") or FakeCapture(),
        playback=kwargs.get("playback") or FakePlayback(),
        wakeword_detector=kwargs.get("wakeword_detector") or FakeWakewordDetector(),
        state_machine=kwargs.get("state_machine") or StateMachine(),
    )


def make_response(**overrides) -> VoiceTurnResponse:
    defaults = dict(
        request_id="req-1",
        conversation_id="conv-1",
        text="All done",
        correlation_id="corr-1",
    )
    defaults.update(overrides)
    return VoiceTurnResponse(**defaults)


def test_run_conversation_turn_happy_path_returns_to_idle():
    response = make_response()
    api_client = FakeApiClient(response=response)
    app = make_app(api_client=api_client)

    app.run_conversation_turn()

    assert app.state_machine.state == State.IDLE
    assert len(api_client.sent_requests) == 1
    assert api_client.sent_requests[0].device_id == "pi-1"
    assert app.conversation_id == "conv-1"


def test_run_conversation_turn_sends_real_wav_container():
    """Captured PCM must be wrapped in a real WAV container, not sent raw."""
    response = make_response()
    api_client = FakeApiClient(response=response)
    audio = WavAudio(frames=b"\x01\x02" * 25, sample_rate=16000)
    app = make_app(api_client=api_client, capture=FakeCapture(audio=audio))

    app.run_conversation_turn()

    sent = api_client.sent_requests[0]
    assert sent.audio_content_type == "audio/wav"
    raw_bytes = base64.b64decode(sent.audio_base64)
    # A real WAV container starts with the RIFF/WAVE header, not bare PCM.
    assert raw_bytes[:4] == b"RIFF"
    assert raw_bytes[8:12] == b"WAVE"
    # And it must decode back to the exact same PCM frames that were captured.
    from home_assistant_pi.audio.wav import from_wav_bytes

    decoded = from_wav_bytes(raw_bytes)
    assert decoded.frames == audio.frames


def test_run_conversation_turn_carries_conversation_id_across_turns():
    response = make_response(conversation_id="conv-42")
    api_client = FakeApiClient(response=response)
    app = make_app(api_client=api_client)

    app.run_conversation_turn()
    assert app.conversation_id == "conv-42"

    app.run_conversation_turn()
    second_request = api_client.sent_requests[1]
    assert second_request.conversation_id == "conv-42"
    # Each turn gets its own fresh request id (used as the Idempotency-Key).
    assert api_client.sent_requests[0].request_id != second_request.request_id


def test_run_conversation_turn_plays_reply_audio_when_wav():
    reply_wav = wav_bytes(WavAudio(frames=b"\x01\x02" * 50, sample_rate=16000))
    audio_bytes = base64.b64encode(reply_wav).decode("ascii")
    response = make_response(
        audio_base64=audio_bytes,
        audio_content_type="audio/wav",
    )
    api_client = FakeApiClient(response=response)
    playback = FakePlayback()
    app = make_app(api_client=api_client, playback=playback)

    app.run_conversation_turn()

    assert app.state_machine.state == State.IDLE
    # One asset (activation.wav) plus the parsed reply audio.
    assert len(playback.played) == 2


def test_run_conversation_turn_skips_playback_for_unsupported_audio_type():
    """Non-WAV reply audio (e.g. audio/mpeg) must not be force-fed to the
    WAV-only playback path; the assistant should fall back to the text
    reply instead of raising."""
    response = make_response(
        audio_base64=base64.b64encode(b"not-really-mp3").decode("ascii"),
        audio_content_type="audio/mpeg",
    )
    api_client = FakeApiClient(response=response)
    playback = FakePlayback()
    app = make_app(api_client=api_client, playback=playback)

    app.run_conversation_turn()

    assert app.state_machine.state == State.IDLE
    # Only the activation.wav asset should have played; the mp3 was skipped.
    assert len(playback.played) == 1


def test_run_conversation_turn_handles_capture_failure():
    capture = FakeCapture(raise_error=RuntimeError("mic unplugged"))
    app = make_app(capture=capture)

    app.run_conversation_turn()

    assert app.state_machine.state == State.IDLE
    assert capture.calls == 1


def test_run_conversation_turn_handles_api_error():
    from home_assistant_pi.api.client import ApiError

    api_client = FakeApiClient(raise_error=ApiError("backend down"))
    app = make_app(api_client=api_client)

    app.run_conversation_turn()

    assert app.state_machine.state == State.IDLE


def test_handle_reminder_when_idle_plays_and_returns_true():
    app = make_app()
    reminder = Reminder(id="r1", title="Feed the cat", due_at="2026-01-01T00:00:00Z")

    result = app.handle_reminder(reminder)

    assert result is True
    assert app.state_machine.state == State.IDLE


def test_handle_reminder_deferred_when_busy_returns_false():
    app = make_app()

    app.state_machine.handle(Event.WAKE_WORD_DETECTED)
    assert app.state_machine.state == State.WAKE_DETECTED

    reminder = Reminder(id="r1", title="Feed the cat", due_at="2026-01-01T00:00:00Z")
    result = app.handle_reminder(reminder)

    assert result is False
    # State must be unaffected since the reminder was deferred.
    assert app.state_machine.state == State.WAKE_DETECTED


def test_play_asset_missing_file_does_not_raise(tmp_path, monkeypatch):
    import home_assistant_pi.main as main_mod

    monkeypatch.setattr(main_mod, "ASSETS_DIR", tmp_path)
    app = make_app()
    app.play_asset("does-not-exist.wav")  # must not raise


def test_listen_for_wakeword_closes_stream_before_returning():
    """The wake-word detection stream must be fully released (not left
    open) once a wake word has been detected, so that a subsequent
    record_utterance() call does not compete for the same audio device."""
    detector = FakeWakewordDetector(detect_on={b"wake"})
    capture = FakeCapture(chunks=[b"noise", b"noise", b"wake"])
    app = make_app(capture=capture, wakeword_detector=detector)

    detected = _listen_for_wakeword(app, frame_length=320)

    assert detected is True
    assert capture.stream_open_count == 1
    assert capture.stream_close_count == 1


def test_listen_for_wakeword_closes_stream_even_when_exhausted():
    """If the chunk stream runs out without a detection, the stream must
    still be closed (no detection == no leaked generator/stream)."""
    detector = FakeWakewordDetector(detect_on=set())
    capture = FakeCapture(chunks=[b"noise", b"noise"])
    app = make_app(capture=capture, wakeword_detector=detector)

    detected = _listen_for_wakeword(app, frame_length=320)

    assert detected is False
    assert capture.stream_close_count == 1


def test_streaming_loop_reopens_stream_after_each_turn():
    """Full orchestration: the detection stream must be closed before
    record_utterance() runs, and a fresh stream must be opened again for
    the next wake-word listen cycle."""
    response = make_response()
    api_client = FakeApiClient(response=response)
    detector = FakeWakewordDetector(detect_on={b"wake"})
    capture = FakeCapture(chunks=[b"wake"])
    app = make_app(api_client=api_client, capture=capture, wakeword_detector=detector)

    iterations = {"count": 0}

    def should_continue():
        iterations["count"] += 1
        return iterations["count"] <= 2

    _run_streaming_loop(app, should_continue)

    # Two full wake -> record cycles happened, and the stream was
    # reopened+closed once per cycle (never left open across a turn).
    assert capture.calls == 2
    assert capture.stream_open_count == 2
    assert capture.stream_close_count == 2
    # record_utterance() must never observe the detection stream as open.
    assert capture.stream_open_during_record is False


def test_ensure_wakeword_engine_is_usable_raises_for_keyboard_non_interactive():
    """The 'keyboard' engine can never work under systemd (no TTY stdin
    there); run_forever() must fail fast instead of starting a service
    that looks active but can never detect a wake word."""
    config = make_config(wakeword_engine="keyboard")
    with pytest.raises(WakewordError, match="interactive terminal"):
        ensure_wakeword_engine_is_usable(config, stdin_isatty=False)


def test_ensure_wakeword_engine_is_usable_allows_keyboard_when_interactive():
    config = make_config(wakeword_engine="keyboard")
    # Must not raise.
    ensure_wakeword_engine_is_usable(config, stdin_isatty=True)


@pytest.mark.parametrize("engine", ["porcupine", "openwakeword"])
@pytest.mark.parametrize("stdin_isatty", [True, False])
def test_ensure_wakeword_engine_is_usable_allows_production_engines(
    engine, stdin_isatty
):
    config = make_config(wakeword_engine=engine)
    # Must not raise regardless of interactivity for real engines.
    ensure_wakeword_engine_is_usable(config, stdin_isatty=stdin_isatty)


def test_run_forever_raises_wakeword_error_before_building_application(monkeypatch):
    """run_forever() must reject an unusable keyboard+non-interactive
    combination before it ever calls build_application(), so no hardware
    resources are touched for a configuration that can't work."""
    import home_assistant_pi.main as main_mod

    def fail_build_application(config):
        raise AssertionError("build_application must not be called")

    monkeypatch.setattr(main_mod, "build_application", fail_build_application)

    config = make_config(wakeword_engine="keyboard")
    with pytest.raises(WakewordError):
        main_mod.run_forever(config, stdin_isatty=False)
