"""Wake word -> streamed command -> incremental playback orchestration."""

from __future__ import annotations

import itertools
import logging
import time
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


@dataclass
class Application:
    config: Config
    api_client: ApiClient
    capture: AudioCapture
    playback: AudioPlayback
    wakeword: WakewordDetector
    state: StateMachine

    def play_asset(self, name: str) -> None:
        try:
            self.playback.play(read_wav(ASSETS_DIR / name))
        except Exception:
            logger.exception("Could not play notification sound %s", name)

    def _finish_cooldown(self) -> None:
        if self.state.state != State.COOLDOWN:
            self.state.transition(State.COOLDOWN)
        if self.config.playback_cooldown_seconds:
            time.sleep(self.config.playback_cooldown_seconds)
        self.state.transition(State.IDLE_WAKEWORD)
        reset = getattr(self.wakeword, "reset", None)
        if callable(reset):
            reset()

    def run_turn(self) -> None:
        self.state.transition(State.ACTIVATED)
        self.play_asset("activation.wav")
        self.state.transition(State.STREAMING_COMMAND)

        frame_length = 16_000 * FRAME_DURATION_MS // 1000
        source = self.capture.stream_chunks(frame_length)
        command = CommandAudioStream(
            source,
            VoiceActivityDetector(mode=self.config.vad_mode),
            no_speech_timeout_seconds=self.config.no_speech_timeout_seconds,
            silence_timeout_seconds=self.config.silence_timeout_seconds,
            max_command_seconds=self.config.max_command_seconds,
        )
        try:
            with self.api_client.voice_response(command) as response_chunks:
                self.state.transition(State.WAITING_FOR_RESPONSE)
                chunks = iter(response_chunks)
                first = next(chunks, b"")
                if not first:
                    raise ApiError("Backend returned no response audio.")
                self.state.transition(State.PLAYING_RESPONSE)
                self.playback.play_stream(
                    itertools.chain([first], chunks),
                    sample_rate=OUTPUT_SAMPLE_RATE,
                )
        except NoSpeechDetected:
            logger.info("Wake detected but no command speech followed")
            self.play_asset("cancellation.wav")
        except (ApiError, OSError):
            logger.exception("Voice turn failed")
            self.play_asset("offline.wav")
        except Exception:
            logger.exception("Unexpected voice turn failure")
            self.play_asset("offline.wav")
        finally:
            self._finish_cooldown()


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
