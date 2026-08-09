"""Application orchestration for the pi-client voice assistant.

``Application`` wires together the wake-word detector, microphone capture,
API client, and speaker playback around the :class:`~home_assistant_pi.
state_machine.StateMachine`. All collaborators are constructor-injected so
the orchestration logic can be exercised in unit tests without any real
hardware or network access; :func:`build_application` is what wires up the
real, hardware-backed collaborators for production use.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .api.client import ApiClient, ApiError
from .api.models import AUDIO_WAV, Reminder, VoiceTurnRequest
from .audio.capture import AudioCapture
from .audio.playback import AudioPlayback
from .audio.vad import VoiceActivityDetector
from .audio.wav import from_wav_bytes, read_wav, wav_bytes
from .config import Config
from .reminders.poller import ReminderPoller
from .state_machine import Event, StateMachine
from .wakeword.base import WakewordDetector, WakewordError

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent / "assets"


@dataclass
class Application:
    """Coordinates one or more conversation turns for the voice assistant."""

    config: Config
    api_client: ApiClient
    capture: AudioCapture
    playback: AudioPlayback
    wakeword_detector: WakewordDetector
    state_machine: StateMachine
    reminder_poller: Optional[ReminderPoller] = None

    def __post_init__(self) -> None:
        # ``None`` until the backend assigns one on the first successful
        # turn; subsequent turns pass it back so the backend can maintain
        # multi-turn conversation history.
        self.conversation_id: Optional[str] = None

    def play_asset(self, name: str) -> None:
        """Play one of the bundled notification sounds (activation/cancellation/offline)."""
        path = ASSETS_DIR / name
        if not path.exists():
            logger.warning("Notification sound asset not found: %s", path)
            return
        try:
            self.playback.play(read_wav(path))
        except Exception:  # pragma: no cover - hardware-dependent
            logger.exception("Failed to play notification sound %s", name)

    def handle_reminder(self, reminder: Reminder) -> bool:
        """Callback invoked by the reminder poller for each due reminder.

        Returns:
            True if the reminder was announced to the user (safe to
            acknowledge to the backend); False if it was deferred because
            the assistant is mid-conversation (must NOT be acknowledged, so
            the poller retries it on the next poll instead of losing it).
        """
        if not self.state_machine.can_handle(Event.REMINDER_DUE):
            logger.info(
                "Deferring reminder %s: assistant is busy (state=%s)",
                reminder.id,
                self.state_machine.state.value,
            )
            return False
        self.state_machine.handle(Event.REMINDER_DUE)
        logger.info("Reminder due: %s", reminder.title)
        self.play_asset("activation.wav")
        self.state_machine.handle(Event.PLAYBACK_FINISHED)
        return True

    def run_conversation_turn(self) -> None:
        """Run exactly one wake-word -> record -> respond -> speak cycle."""
        self.state_machine.handle(Event.WAKE_WORD_DETECTED)
        self.play_asset("activation.wav")

        self.state_machine.handle(Event.RECORDING_STARTED)
        vad = VoiceActivityDetector()
        try:
            audio = self.capture.record_utterance(vad=vad)
        except Exception:
            logger.exception("Microphone capture failed")
            self.state_machine.handle(Event.ERROR_OCCURRED)
            self.play_asset("offline.wav")
            self.state_machine.reset()
            return

        self.state_machine.handle(Event.SPEECH_ENDED)

        # Serialize the captured PCM into a real WAV container (RIFF header
        # + fmt/data chunks) before marking it audio/wav; the backend
        # validates audioContentType against the actual bytes, so sending
        # bare/raw PCM here would be silently invalid.
        request = VoiceTurnRequest.from_audio(
            device_id=self.config.device_id,
            timezone=self.config.timezone,
            audio_bytes=wav_bytes(audio),
            audio_content_type=AUDIO_WAV,
            conversation_id=self.conversation_id,
        )
        try:
            response = self.api_client.send_voice_turn(request)
        except ApiError:
            logger.exception("Backend request failed")
            self.state_machine.handle(Event.CONNECTIVITY_LOST)
            self.play_asset("offline.wav")
            self.state_machine.handle(Event.CONNECTIVITY_RESTORED)
            return

        self.conversation_id = response.conversation_id or self.conversation_id
        self.state_machine.handle(Event.RESPONSE_READY)
        reply_audio_bytes = response.reply_audio_bytes()
        if reply_audio_bytes and response.audio_content_type == AUDIO_WAV:
            try:
                self.playback.play(from_wav_bytes(reply_audio_bytes))
            except Exception:  # pragma: no cover - hardware-dependent
                logger.exception("Failed to play backend reply audio")
        else:
            if reply_audio_bytes:
                logger.info(
                    "Reply audio content type %r is not directly playable; "
                    "speaking text reply instead: %s",
                    response.audio_content_type,
                    response.text,
                )
            else:
                logger.info("Assistant reply: %s", response.text)
        self.state_machine.handle(Event.PLAYBACK_FINISHED)

    def start_background_services(self) -> None:
        if self.reminder_poller is not None:
            self.reminder_poller.start()

    def stop_background_services(self) -> None:
        if self.reminder_poller is not None:
            self.reminder_poller.stop()


def build_application(config: Config) -> Application:
    """Construct an :class:`Application` wired with real hardware/network backends."""
    from .wakeword import create_detector

    api_client = ApiClient(
        base_url=config.api_base_url,
        device_token=config.device_token,
        timeout_seconds=config.request_timeout_seconds,
        retries=config.request_retries,
    )
    capture = AudioCapture(
        sample_rate=config.sample_rate,
        device=config.input_device,
    )
    playback = AudioPlayback(device=config.output_device)
    wakeword_detector = create_detector(
        engine=config.wakeword_engine,
        keyword=config.wakeword_keyword,
        sensitivity=config.wakeword_sensitivity,
    )
    state_machine = StateMachine()

    app = Application(
        config=config,
        api_client=api_client,
        capture=capture,
        playback=playback,
        wakeword_detector=wakeword_detector,
        state_machine=state_machine,
    )
    app.reminder_poller = ReminderPoller(
        api_client=api_client,
        device_id=config.device_id,
        on_reminder=app.handle_reminder,
        poll_interval_seconds=config.reminder_poll_interval_seconds,
    )
    return app


def _listen_for_wakeword(app: Application, frame_length: int) -> bool:
    """Stream microphone chunks through the wake-word detector until it
    fires, then close the capture stream before returning.

    The wake-word engine needs a continuously open capture stream, but
    :meth:`AudioCapture.record_utterance` (used by ``run_conversation_turn``
    right after this returns) opens its own stream for the same audio
    device. Most audio backends (including PortAudio) do not allow two
    concurrent input streams on one device, so the detection stream here is
    explicitly closed (via the generator's ``close()``, which unwinds the
    ``with`` block inside :meth:`AudioCapture.stream_chunks`) before a
    conversation turn starts, and a fresh stream is opened again on the
    next call once the turn has finished.
    """
    stream = app.capture.stream_chunks(frame_length)
    detected = False
    try:
        for chunk in stream:
            if app.wakeword_detector.process(chunk):
                detected = True
                break
    finally:
        stream.close()
    return detected


def _run_keyboard_loop(app: Application, should_continue) -> None:
    while should_continue():
        if app.wakeword_detector.process(b""):
            _run_turn_safely(app)


def _run_streaming_loop(app: Application, should_continue) -> None:
    frame_length = app.wakeword_detector.frame_length()
    while should_continue():
        if _listen_for_wakeword(app, frame_length):
            _run_turn_safely(app)


def ensure_wakeword_engine_is_usable(config: Config, stdin_isatty: bool) -> None:
    """Raise ``WakewordError`` if the configured wake-word engine cannot
    possibly function given the current terminal's interactivity.

    Only the ``keyboard`` engine has this restriction: it blocks on
    ``stdin.readline()`` to detect a simulated wake word, which requires an
    interactive terminal. Split out from :func:`run_forever` so it can be
    unit tested directly without entering the (intentionally infinite)
    main loop.
    """
    if config.wakeword_engine == "keyboard" and not stdin_isatty:
        raise WakewordError(
            "wakeword_engine='keyboard' requires an interactive terminal to "
            "receive keypresses, but stdin is not a TTY (this is always the "
            "case under systemd). The 'keyboard' engine only works for "
            "interactive development/testing and cannot run as an "
            "unattended service. Install a production wake-word engine "
            "(install.sh --wakeword-extra porcupine|openwakeword, or "
            "'pip install \"home-assistant-pi[porcupine]\"' / "
            "'[openwakeword]' manually) and set wakeword_engine=porcupine "
            "or wakeword_engine=openwakeword in config.env instead."
        )


def run_forever(config: Config, stdin_isatty: Optional[bool] = None) -> None:
    """Run the assistant's main loop until interrupted (used by the CLI/systemd).

    Args:
        stdin_isatty: Overrides the interactive-terminal detection below
            (mainly for tests). Defaults to the real ``sys.stdin.isatty()``.

    Raises:
        WakewordError: if ``wakeword_engine`` is ``"keyboard"`` and stdin is
            not an interactive terminal. That is always true when running
            under systemd (stdin is redirected from /dev/null, never a
            TTY), so the keyboard engine would otherwise start an outwardly
            "active" service that can never actually detect a wake word --
            it would silently loop forever doing nothing useful. Failing
            loudly here instead lets systemd report the service as failed,
            so the misconfiguration is visible immediately rather than
            masked as a running-but-useless service.
    """
    if stdin_isatty is None:
        stdin_isatty = sys.stdin.isatty()
    ensure_wakeword_engine_is_usable(config, stdin_isatty)

    app = build_application(config)
    app.start_background_services()
    logger.info("home-assistant-pi started for device %s", config.device_id)
    try:
        if config.wakeword_engine == "keyboard":
            # The keyboard engine ignores audio content and instead blocks
            # on stdin, so no real microphone stream is needed to poll it.
            _run_keyboard_loop(app, should_continue=lambda: True)
        else:
            _run_streaming_loop(app, should_continue=lambda: True)
    except KeyboardInterrupt:  # pragma: no cover - manual interrupt
        logger.info("Shutting down on keyboard interrupt")
    finally:
        app.stop_background_services()
        app.wakeword_detector.close()
        app.api_client.close()


def _run_turn_safely(app: Application) -> None:
    try:
        app.run_conversation_turn()
    except Exception:
        logger.exception("Unhandled error during conversation turn")
        app.state_machine.reset()
