"""Tests for home_assistant_pi.state_machine."""

from __future__ import annotations

import pytest

from home_assistant_pi.state_machine import (
    Event,
    InvalidTransitionError,
    State,
    StateMachine,
)


def test_initial_state_is_idle():
    sm = StateMachine()
    assert sm.state == State.IDLE
    assert not sm.is_busy()


def test_full_conversation_cycle():
    sm = StateMachine()
    assert sm.handle(Event.WAKE_WORD_DETECTED) == State.WAKE_DETECTED
    assert sm.is_busy()
    assert sm.handle(Event.RECORDING_STARTED) == State.LISTENING
    assert sm.handle(Event.SPEECH_ENDED) == State.PROCESSING
    assert sm.handle(Event.RESPONSE_READY) == State.SPEAKING
    assert sm.handle(Event.PLAYBACK_FINISHED) == State.IDLE
    assert not sm.is_busy()
    assert len(sm.history) == 5


def test_invalid_transition_raises():
    sm = StateMachine()
    assert not sm.can_handle(Event.SPEECH_ENDED)
    with pytest.raises(InvalidTransitionError):
        sm.handle(Event.SPEECH_ENDED)
    # State must not have changed.
    assert sm.state == State.IDLE


def test_cancel_from_listening_returns_to_idle():
    sm = StateMachine()
    sm.handle(Event.WAKE_WORD_DETECTED)
    sm.handle(Event.RECORDING_STARTED)
    assert sm.handle(Event.CANCELLED) == State.IDLE


def test_connectivity_lost_and_restored():
    sm = StateMachine()
    sm.handle(Event.CONNECTIVITY_LOST)
    assert sm.state == State.OFFLINE
    assert not sm.is_busy()
    assert sm.handle(Event.CONNECTIVITY_RESTORED) == State.IDLE


def test_error_requires_reset_event():
    sm = StateMachine()
    sm.handle(Event.ERROR_OCCURRED)
    assert sm.state == State.ERROR
    assert not sm.can_handle(Event.WAKE_WORD_DETECTED)
    assert sm.handle(Event.RESET) == State.IDLE


def test_reminder_cycle_from_idle():
    sm = StateMachine()
    assert sm.handle(Event.REMINDER_DUE) == State.REMINDER
    assert sm.handle(Event.PLAYBACK_FINISHED) == State.IDLE


def test_force_reset_from_any_state():
    sm = StateMachine()
    sm.handle(Event.WAKE_WORD_DETECTED)
    sm.handle(Event.RECORDING_STARTED)
    assert sm.state == State.LISTENING
    assert sm.reset() == State.IDLE


def test_on_transition_callback_invoked():
    calls = []
    sm = StateMachine(on_transition=lambda t: calls.append(t))
    sm.handle(Event.WAKE_WORD_DETECTED)
    assert len(calls) == 1
    assert calls[0].previous == State.IDLE
    assert calls[0].next == State.WAKE_DETECTED
    assert calls[0].event == Event.WAKE_WORD_DETECTED
