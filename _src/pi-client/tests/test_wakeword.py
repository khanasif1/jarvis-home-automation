"""Tests for home_assistant_pi.wakeword."""

from __future__ import annotations

import pytest

from home_assistant_pi.wakeword import create_detector
from home_assistant_pi.wakeword.base import WakewordError
from home_assistant_pi.wakeword.keyboard import KeyboardWakewordDetector


def test_keyboard_detector_uses_injected_trigger():
    calls = []

    def trigger():
        calls.append(1)
        return True

    detector = KeyboardWakewordDetector(trigger=trigger)
    assert detector.process(b"ignored") is True
    assert len(calls) == 1


def test_keyboard_detector_frame_and_sample_rate_defaults():
    detector = KeyboardWakewordDetector()
    assert detector.frame_length() == 512
    assert detector.sample_rate() == 16000


def test_keyboard_detector_close_is_noop():
    detector = KeyboardWakewordDetector(trigger=lambda: False)
    detector.close()  # must not raise


def test_keyboard_detector_default_trigger_eof_returns_false(monkeypatch):
    """readline() returning "" (EOF) must be treated as "no wake word", not
    as a wake event -- otherwise a closed/redirected stdin (the normal case
    under systemd) would trigger a conversation turn on every single poll."""
    import io

    sleeps = []
    detector = KeyboardWakewordDetector(sleep_fn=lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert detector.process(b"ignored") is False
    # EOF must back off briefly instead of allowing a tight CPU spin.
    assert len(sleeps) == 1
    assert sleeps[0] > 0


def test_keyboard_detector_default_trigger_real_line_returns_true(monkeypatch):
    import io

    sleeps = []
    detector = KeyboardWakewordDetector(sleep_fn=lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))

    assert detector.process(b"ignored") is True
    # A genuine keypress must not incur the EOF backoff sleep.
    assert sleeps == []


def test_keyboard_detector_repeated_eof_does_not_spin_without_sleeping(monkeypatch):
    """Simulates a systemd-managed process whose stdin is /dev/null: every
    readline() call immediately returns EOF, so the loop must sleep on
    every iteration rather than spinning the CPU."""
    import io

    sleeps = []
    detector = KeyboardWakewordDetector(sleep_fn=lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    for _ in range(5):
        assert detector.process(b"ignored") is False
    assert len(sleeps) == 5


def test_create_detector_keyboard():
    detector = create_detector("keyboard")
    assert isinstance(detector, KeyboardWakewordDetector)


def test_create_detector_unknown_engine_raises():
    with pytest.raises(WakewordError, match="Unknown wake-word engine"):
        create_detector("not-a-real-engine")


def test_create_detector_porcupine_without_dependency_raises(monkeypatch):
    monkeypatch.delenv("HAP_PORCUPINE_ACCESS_KEY", raising=False)
    with pytest.raises(WakewordError):
        create_detector("porcupine")


def test_create_detector_openwakeword_without_dependency_raises():
    with pytest.raises(WakewordError):
        create_detector("openwakeword")


def test_wakeword_detector_context_manager_calls_close():
    closed = []

    class Dummy(KeyboardWakewordDetector):
        def close(self):
            closed.append(True)

    with Dummy(trigger=lambda: False) as detector:
        assert isinstance(detector, Dummy)
    assert closed == [True]
