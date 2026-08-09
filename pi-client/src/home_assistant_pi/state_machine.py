"""Conversation state machine for the pi-client voice assistant.

The client cycles through a small, explicit set of states while it waits
for a wake word, records the user's speech, waits for a backend response,
speaks the reply, and handles reminders/errors. Keeping this logic in a
dedicated, dependency-free module makes it straightforward to unit test
without any real audio hardware or network access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class State(str, Enum):
    """All states the assistant can be in."""

    IDLE = "idle"
    WAKE_DETECTED = "wake_detected"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    REMINDER = "reminder"
    OFFLINE = "offline"
    ERROR = "error"


class Event(str, Enum):
    """All events that can trigger a state transition."""

    WAKE_WORD_DETECTED = "wake_word_detected"
    RECORDING_STARTED = "recording_started"
    SPEECH_ENDED = "speech_ended"
    RESPONSE_READY = "response_ready"
    PLAYBACK_FINISHED = "playback_finished"
    REMINDER_DUE = "reminder_due"
    ERROR_OCCURRED = "error_occurred"
    CONNECTIVITY_LOST = "connectivity_lost"
    CONNECTIVITY_RESTORED = "connectivity_restored"
    RESET = "reset"
    CANCELLED = "cancelled"


class InvalidTransitionError(RuntimeError):
    """Raised when an event is not permitted in the current state."""


# Mapping of (current_state, event) -> next_state. Any (state, event) pair
# not present here is considered invalid.
_TRANSITIONS: dict[tuple[State, Event], State] = {
    (State.IDLE, Event.WAKE_WORD_DETECTED): State.WAKE_DETECTED,
    (State.IDLE, Event.REMINDER_DUE): State.REMINDER,
    (State.IDLE, Event.CONNECTIVITY_LOST): State.OFFLINE,
    (State.IDLE, Event.ERROR_OCCURRED): State.ERROR,
    (State.WAKE_DETECTED, Event.RECORDING_STARTED): State.LISTENING,
    (State.WAKE_DETECTED, Event.CANCELLED): State.IDLE,
    (State.WAKE_DETECTED, Event.ERROR_OCCURRED): State.ERROR,
    (State.LISTENING, Event.SPEECH_ENDED): State.PROCESSING,
    (State.LISTENING, Event.CANCELLED): State.IDLE,
    (State.LISTENING, Event.ERROR_OCCURRED): State.ERROR,
    (State.PROCESSING, Event.RESPONSE_READY): State.SPEAKING,
    (State.PROCESSING, Event.CANCELLED): State.IDLE,
    (State.PROCESSING, Event.ERROR_OCCURRED): State.ERROR,
    (State.PROCESSING, Event.CONNECTIVITY_LOST): State.OFFLINE,
    (State.SPEAKING, Event.PLAYBACK_FINISHED): State.IDLE,
    (State.SPEAKING, Event.ERROR_OCCURRED): State.ERROR,
    (State.REMINDER, Event.PLAYBACK_FINISHED): State.IDLE,
    (State.REMINDER, Event.ERROR_OCCURRED): State.ERROR,
    (State.OFFLINE, Event.CONNECTIVITY_RESTORED): State.IDLE,
    (State.OFFLINE, Event.RESET): State.IDLE,
    (State.ERROR, Event.RESET): State.IDLE,
}


@dataclass
class Transition:
    """A single recorded state transition."""

    previous: State
    event: Event
    next: State


@dataclass
class StateMachine:
    """A small, explicit finite state machine for the voice assistant."""

    state: State = State.IDLE
    history: list = field(default_factory=list)
    on_transition: Optional[Callable[[Transition], None]] = None

    def can_handle(self, event: Event) -> bool:
        """Return True if ``event`` is valid from the current state."""
        return (self.state, event) in _TRANSITIONS

    def handle(self, event: Event) -> State:
        """Apply ``event`` to the current state.

        Returns:
            The new state.

        Raises:
            InvalidTransitionError: if ``event`` is not valid from the
                current state.
        """
        key = (self.state, event)
        if key not in _TRANSITIONS:
            raise InvalidTransitionError(
                f"Event {event.value!r} is not valid while in state "
                f"{self.state.value!r}"
            )
        previous = self.state
        self.state = _TRANSITIONS[key]
        transition = Transition(previous=previous, event=event, next=self.state)
        self.history.append(transition)
        logger.debug(
            "state transition: %s -> %s (event=%s)",
            previous.value,
            self.state.value,
            event.value,
        )
        if self.on_transition is not None:
            self.on_transition(transition)
        return self.state

    def reset(self) -> State:
        """Force the machine back to :data:`State.IDLE` unconditionally."""
        previous = self.state
        self.state = State.IDLE
        transition = Transition(previous=previous, event=Event.RESET, next=self.state)
        self.history.append(transition)
        if self.on_transition is not None:
            self.on_transition(transition)
        return self.state

    def is_busy(self) -> bool:
        """Return True when the assistant is mid-conversation (not idle/offline)."""
        return self.state not in (State.IDLE, State.OFFLINE, State.ERROR)
