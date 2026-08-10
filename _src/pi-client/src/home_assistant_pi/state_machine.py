"""Explicit half-duplex voice state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    IDLE_WAKEWORD = "idle_wakeword"
    ACTIVATED = "activated"
    STREAMING_COMMAND = "streaming_command"
    WAITING_FOR_RESPONSE = "waiting_for_response"
    PLAYING_RESPONSE = "playing_response"
    COOLDOWN = "cooldown"


_ALLOWED = {
    State.IDLE_WAKEWORD: {State.ACTIVATED},
    State.ACTIVATED: {State.STREAMING_COMMAND, State.COOLDOWN},
    State.STREAMING_COMMAND: {State.WAITING_FOR_RESPONSE, State.COOLDOWN},
    State.WAITING_FOR_RESPONSE: {State.PLAYING_RESPONSE, State.COOLDOWN},
    State.PLAYING_RESPONSE: {State.COOLDOWN},
    State.COOLDOWN: {State.IDLE_WAKEWORD},
}


class InvalidTransitionError(RuntimeError):
    pass


@dataclass
class StateMachine:
    state: State = State.IDLE_WAKEWORD
    history: list[tuple[State, State]] = field(default_factory=list)

    def transition(self, next_state: State) -> None:
        if next_state not in _ALLOWED[self.state]:
            raise InvalidTransitionError(
                f"Cannot transition from {self.state.value} to {next_state.value}."
            )
        previous = self.state
        self.state = next_state
        self.history.append((previous, next_state))

    def reset(self) -> None:
        self.state = State.IDLE_WAKEWORD
