"""Explicit voice-session lifecycle state machine.

Every transition is validated against `ALLOWED` and recorded with a cause and a
monotonic timestamp, so tests (and the operator UI) can assert exact sequences.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class VoiceState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    USER_SPEAKING = "USER_SPEAKING"
    PROCESSING = "PROCESSING"
    AGENT_SPEAKING = "AGENT_SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    TRANSFER_REQUESTED = "TRANSFER_REQUESTED"
    ENDED = "ENDED"
    ERROR = "ERROR"


S = VoiceState
ALLOWED: dict[VoiceState, set[VoiceState]] = {
    S.IDLE: {S.PROCESSING, S.LISTENING},
    S.LISTENING: {S.USER_SPEAKING, S.PROCESSING},
    S.USER_SPEAKING: {S.LISTENING, S.PROCESSING},
    # PROCESSING -> USER_SPEAKING: caller resumed talking before the reply started.
    S.PROCESSING: {S.AGENT_SPEAKING, S.USER_SPEAKING, S.LISTENING, S.TRANSFER_REQUESTED},
    S.AGENT_SPEAKING: {S.LISTENING, S.INTERRUPTED, S.TRANSFER_REQUESTED},
    S.INTERRUPTED: {S.USER_SPEAKING, S.PROCESSING, S.LISTENING},
    S.TRANSFER_REQUESTED: set(),
    S.ENDED: set(),
    S.ERROR: set(),
}
# Any non-terminal state may go to ENDED (hang-up) or ERROR (fatal provider failure).
for _s in list(ALLOWED):
    if _s not in (S.ENDED,):
        ALLOWED[_s] |= {S.ENDED}
    if _s not in (S.ENDED, S.ERROR):
        ALLOWED[_s] |= {S.ERROR}


class IllegalTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class Transition:
    from_state: VoiceState
    to_state: VoiceState
    cause: str
    at: float


class Lifecycle:
    def __init__(self, monotonic: Callable[[], float], on_change: Callable[[Transition], None] | None = None) -> None:
        self.state = VoiceState.IDLE
        self.history: list[Transition] = []
        self._mono = monotonic
        self._on_change = on_change

    def can(self, to: VoiceState) -> bool:
        return to in ALLOWED[self.state]

    def to(self, to: VoiceState, cause: str) -> Transition | None:
        if to == self.state:
            return None
        if not self.can(to):
            raise IllegalTransition(f"{self.state.value} -> {to.value} ({cause})")
        t = Transition(self.state, to, cause, self._mono())
        self.state = to
        self.history.append(t)
        if self._on_change:
            self._on_change(t)
        return t

    @property
    def terminal(self) -> bool:
        return self.state in (VoiceState.ENDED, VoiceState.ERROR)

    def path(self) -> list[str]:
        return [VoiceState.IDLE.value] + [t.to_state.value for t in self.history]
