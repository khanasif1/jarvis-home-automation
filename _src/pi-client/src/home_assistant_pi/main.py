"""Wake word -> streamed command -> incremental playback orchestration."""

from __future__ import annotations

import itertools
import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .api import JARVIS_QUERY, JARVIS_SLEEP, ApiClient, ApiError
from .audio.capture import AudioCapture
from .audio.playback import AudioPlayback
from .audio.vad import CommandAudioStream, NoSpeechDetected, VoiceActivityDetector
from .audio.wav import read_wav
from .config import Config, FRAME_DURATION_MS, OUTPUT_SAMPLE_RATE
from .state_machine import State, StateMachine
from .wakeword import WakewordDetector, create_detector

logger = logging.getLogger(__name__)
ASSETS_DIR = Path(__file__).parent / "assets"
SPOKEN_PROMPTS = {
    "greeting": (
        "greeting.wav",
        "I am your AI assistant. How can I help?",
    ),
    "searching": (
        "searching.wav",
        "I will search for your query and get back soon.",
    ),
    "followup": (
        "followup.wav",
        "Do you have another query? Please say it now.",
    ),
    "sleep": (
        "sleep.wav",
        "I am going back to sleep mode. Wake me up if you want to talk again.",
    ),
}
GREETING_ASSET = SPOKEN_PROMPTS["greeting"][0]
SEARCHING_ASSET = SPOKEN_PROMPTS["searching"][0]
FOLLOWUP_ASSET = SPOKEN_PROMPTS["followup"][0]
SLEEP_ASSET = SPOKEN_PROMPTS["sleep"][0]


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

    def play_asset(
        self,
        name: str,
        *,
        turn_id: str | None = None,
        query_number: int | None = None,
    ) -> None:
        details: dict[str, int | str] = {"cue": name}
        if query_number is not None:
            details["query"] = query_number
        try:
            if turn_id is not None:
                _log_activity(
                    turn_id,
                    "cue_playback_started",
                    direction="output",
                    **details,
                )
            self.playback.play(read_wav(ASSETS_DIR / name))
            if turn_id is not None:
                _log_activity(
                    turn_id,
                    "cue_playback_completed",
                    direction="output",
                    **details,
                )
        except Exception:
            if turn_id is None:
                logger.exception("Could not play notification sound %s", name)
            else:
                logger.exception(
                    "activity turn=%s event=cue_playback_failed "
                    "direction=output cue=%s query=%s",
                    turn_id,
                    name,
                    query_number if query_number is not None else "session",
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

    def _capture_command(
        self,
        turn_id: str,
        query_number: int,
        no_speech_timeout_seconds: float,
    ) -> list[bytes]:
        _log_activity(
            turn_id,
            "capture_started",
            direction="input",
            query=query_number,
            sample_rate_hz=16_000,
            no_speech_timeout_ms=int(no_speech_timeout_seconds * 1000),
        )

        frame_length = 16_000 * FRAME_DURATION_MS // 1000
        source = self.capture.stream_chunks(frame_length)

        def input_activity(event: str, details: dict[str, int | str]) -> None:
            _log_activity(
                turn_id,
                event,
                direction="input",
                query=query_number,
                **details,
            )

        command = CommandAudioStream(
            source,
            VoiceActivityDetector(mode=self.config.vad_mode),
            no_speech_timeout_seconds=no_speech_timeout_seconds,
            silence_timeout_seconds=self.config.silence_timeout_seconds,
            max_command_seconds=self.config.max_command_seconds,
            activity_callback=input_activity,
        )
        try:
            return list(command)
        finally:
            command.close()

    def _run_query(self, turn_id: str, query_number: int, timeout: float) -> bool:
        command_chunks = self._capture_command(turn_id, query_number, timeout)
        if query_number > 1:
            _log_activity(
                turn_id,
                "followup_intent_started",
                query=query_number,
            )
            intent = self.api_client.classify_followup(iter(command_chunks))
            _log_activity(
                turn_id,
                "followup_intent_completed",
                query=query_number,
                intent=intent,
            )
            if intent == JARVIS_SLEEP:
                _log_activity(
                    turn_id,
                    "query_cancelled",
                    query=query_number,
                    reason="user_requested_sleep",
                )
                return False
            if intent != JARVIS_QUERY:
                raise ApiError("Backend returned an unknown follow-up intent.")

        self.state.transition(State.WAITING_FOR_RESPONSE)
        _log_activity(
            turn_id,
            "backend_request_started",
            query=query_number,
            audio_bytes=sum(len(chunk) for chunk in command_chunks),
        )

        with self.api_client.background_voice_response(
            iter(command_chunks)
        ) as response_chunks:
            _log_activity(
                turn_id,
                "backend_request_dispatched",
                query=query_number,
            )
            self.play_asset(
                SEARCHING_ASSET,
                turn_id=turn_id,
                query_number=query_number,
            )
            _log_activity(
                turn_id,
                "backend_response_waiting",
                query=query_number,
            )
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
                query=query_number,
                sample_rate_hz=OUTPUT_SAMPLE_RATE,
            )
            self.playback.play_stream(
                counted_output(),
                sample_rate=OUTPUT_SAMPLE_RATE,
            )
            output_duration_ms = output_bytes * 1000 // (OUTPUT_SAMPLE_RATE * 2)
            _log_activity(
                turn_id,
                "playback_completed",
                direction="output",
                query=query_number,
                audio_bytes=output_bytes,
                audio_ms=output_duration_ms,
            )
            _log_activity(
                turn_id,
                "query_completed",
                query=query_number,
            )
        return True

    def run_session(self) -> None:
        turn_id = uuid.uuid4().hex[:12]
        session_started = time.monotonic()
        _log_activity(turn_id, "wake_detected", direction="input")
        self.state.transition(State.ACTIVATED)
        self.play_asset(GREETING_ASSET, turn_id=turn_id)
        query_number = 1
        completed_queries = 0
        close_reason = "followup_timeout"
        try:
            while True:
                self.state.transition(State.STREAMING_COMMAND)
                timeout = (
                    self.config.no_speech_timeout_seconds
                    if query_number == 1
                    else self.config.followup_timeout_seconds
                )
                try:
                    should_continue = self._run_query(
                        turn_id,
                        query_number,
                        timeout,
                    )
                except NoSpeechDetected:
                    close_reason = (
                        "no_initial_query"
                        if query_number == 1
                        else "followup_timeout"
                    )
                    _log_activity(
                        turn_id,
                        "query_cancelled",
                        query=query_number,
                        reason=close_reason,
                    )
                    break
                except (ApiError, OSError):
                    close_reason = "voice_request_failed"
                    logger.exception(
                        "activity turn=%s event=query_failed query=%s "
                        "error=voice_request",
                        turn_id,
                        query_number,
                    )
                    self.play_asset(
                        "offline.wav",
                        turn_id=turn_id,
                        query_number=query_number,
                    )
                    break
                except Exception:
                    close_reason = "unexpected_failure"
                    logger.exception(
                        "activity turn=%s event=query_failed query=%s "
                        "error=unexpected",
                        turn_id,
                        query_number,
                    )
                    self.play_asset(
                        "offline.wav",
                        turn_id=turn_id,
                        query_number=query_number,
                    )
                    break

                if not should_continue:
                    close_reason = "user_requested_sleep"
                    break
                completed_queries += 1
                self.play_asset(
                    FOLLOWUP_ASSET,
                    turn_id=turn_id,
                    query_number=query_number,
                )
                query_number += 1
        finally:
            self.play_asset(SLEEP_ASSET, turn_id=turn_id)
            _log_activity(
                turn_id,
                "session_completed",
                queries=completed_queries,
                reason=close_reason,
                elapsed_ms=int((time.monotonic() - session_started) * 1000),
            )
            self._finish_cooldown(turn_id)

    def run_turn(self) -> None:
        self.run_session()


def build_application(config: Config) -> Application:
    return Application(
        config=config,
        api_client=ApiClient(config.api_base_url, config.device_guid),
        capture=AudioCapture(device=config.input_device),
        playback=AudioPlayback(device=config.output_device),
        wakeword=create_detector(
            config.wakeword_threshold,
            config.wakeword_model_path,
        ),
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
                app.run_session()
    except KeyboardInterrupt:
        logger.info("Stopping Jarvis Pi client")
    finally:
        app.wakeword.close()
        app.api_client.close()
