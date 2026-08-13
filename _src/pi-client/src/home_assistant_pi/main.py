"""Wake word -> streamed command -> incremental playback orchestration."""

from __future__ import annotations

import itertools
import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .api import ApiClient, ApiError
from .audio.capture import AudioCapture
from .audio.playback import AudioPlayback
from .audio.vad import CommandAudioStream, NoSpeechDetected, VoiceActivityDetector
from .audio.wav import read_wav
from .config import Config, FRAME_DURATION_MS, OUTPUT_SAMPLE_RATE
from .state_machine import State, StateMachine
from .wakeword import WakewordDetector, create_detector

logger = logging.getLogger(__name__)
ASSETS_DIR = Path(__file__).parent / "assets"


def _log_activity(
    turn_id: str,
    event: str,
    *,
    direction: str | None = None,
    **details: int | str,
) -> None:
    fields = [f"turn={turn_id}", f"event={event}"]
    if direction is not None:
        fields.append(f"direction={direction}")
    fields.extend(f"{name}={value}" for name, value in details.items())
    logger.info("activity %s", " ".join(fields))


@dataclass
class Application:
    config: Config
    api_client: ApiClient
    capture: AudioCapture
    playback: AudioPlayback
    wakeword: WakewordDetector
    state: StateMachine

    def play_asset(self, name: str, *, turn_id: str | None = None) -> None:
        try:
            if turn_id is not None:
                _log_activity(
                    turn_id,
                    "cue_playback_started",
                    direction="output",
                    cue=name,
                )
            self.playback.play(read_wav(ASSETS_DIR / name))
            if turn_id is not None:
                _log_activity(
                    turn_id,
                    "cue_playback_completed",
                    direction="output",
                    cue=name,
                )
        except Exception:
            if turn_id is None:
                logger.exception("Could not play notification sound %s", name)
            else:
                logger.exception(
                    "activity turn=%s event=cue_playback_failed "
                    "direction=output cue=%s",
                    turn_id,
                    name,
                )

    def _finish_cooldown(self, turn_id: str) -> None:
        if self.state.state != State.COOLDOWN:
            self.state.transition(State.COOLDOWN)
        _log_activity(turn_id, "cooldown_started")
        if self.config.playback_cooldown_seconds:
            time.sleep(self.config.playback_cooldown_seconds)
        self.state.transition(State.IDLE_WAKEWORD)
        reset = getattr(self.wakeword, "reset", None)
        if callable(reset):
            reset()
        _log_activity(turn_id, "ready_for_wakeword")

    def run_turn(self) -> None:
        turn_id = uuid.uuid4().hex[:12]
        turn_started = time.monotonic()
        _log_activity(turn_id, "wake_detected", direction="input")
        self.state.transition(State.ACTIVATED)
        self.play_asset("activation.wav", turn_id=turn_id)
        self.state.transition(State.STREAMING_COMMAND)
        _log_activity(
            turn_id,
            "capture_started",
            direction="input",
            sample_rate_hz=16_000,
        )

        frame_length = 16_000 * FRAME_DURATION_MS // 1000
        source = self.capture.stream_chunks(frame_length)

        def input_activity(event: str, details: dict[str, int | str]) -> None:
            _log_activity(turn_id, event, direction="input", **details)

        command = CommandAudioStream(
            source,
            VoiceActivityDetector(mode=self.config.vad_mode),
            no_speech_timeout_seconds=self.config.no_speech_timeout_seconds,
            silence_timeout_seconds=self.config.silence_timeout_seconds,
            max_command_seconds=self.config.max_command_seconds,
            activity_callback=input_activity,
        )
        try:
            _log_activity(turn_id, "backend_request_started")
            with self.api_client.voice_response(command) as response_chunks:
                self.state.transition(State.WAITING_FOR_RESPONSE)
                _log_activity(turn_id, "backend_response_started")
                chunks = iter(response_chunks)
                first = next(chunks, b"")
                if not first:
                    raise ApiError("Backend returned no response audio.")
                self.state.transition(State.PLAYING_RESPONSE)
                output_bytes = 0

                def counted_output() -> Iterator[bytes]:
                    nonlocal output_bytes
                    for chunk in itertools.chain([first], chunks):
                        output_bytes += len(chunk)
                        yield chunk

                _log_activity(
                    turn_id,
                    "playback_started",
                    direction="output",
                    sample_rate_hz=OUTPUT_SAMPLE_RATE,
                )
                self.playback.play_stream(
                    counted_output(),
                    sample_rate=OUTPUT_SAMPLE_RATE,
                )
                output_duration_ms = (
                    output_bytes * 1000 // (OUTPUT_SAMPLE_RATE * 2)
                )
                _log_activity(
                    turn_id,
                    "playback_completed",
                    direction="output",
                    audio_bytes=output_bytes,
                    audio_ms=output_duration_ms,
                )
                _log_activity(
                    turn_id,
                    "turn_completed",
                    elapsed_ms=int((time.monotonic() - turn_started) * 1000),
                )
        except NoSpeechDetected:
            _log_activity(turn_id, "turn_cancelled", reason="no_speech")
            self.play_asset("cancellation.wav", turn_id=turn_id)
        except (ApiError, OSError):
            logger.exception(
                "activity turn=%s event=turn_failed error=voice_request", turn_id
            )
            self.play_asset("offline.wav", turn_id=turn_id)
        except Exception:
            logger.exception(
                "activity turn=%s event=turn_failed error=unexpected", turn_id
            )
            self.play_asset("offline.wav", turn_id=turn_id)
        finally:
            self._finish_cooldown(turn_id)


def build_application(config: Config) -> Application:
    return Application(
        config=config,
        api_client=ApiClient(config.api_base_url, config.device_guid),
        capture=AudioCapture(device=config.input_device),
        playback=AudioPlayback(device=config.output_device),
        wakeword=create_detector(config.wakeword_threshold),
        state=StateMachine(),
    )


def _wait_for_wakeword(app: Application) -> bool:
    stream = app.capture.stream_chunks(app.wakeword.frame_length())
    try:
        for chunk in stream:
            if app.wakeword.process(chunk):
                return True
    finally:
        stream.close()
    return False


def run_forever(config: Config) -> None:
    app = build_application(config)
    logger.info("Jarvis Pi client started")
    try:
        while True:
            if _wait_for_wakeword(app):
                app.run_turn()
    except KeyboardInterrupt:
        logger.info("Stopping Jarvis Pi client")
    finally:
        app.wakeword.close()
        app.api_client.close()
