"""Audit events: every important action must be reconstructable after the call."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class AuditType(StrEnum):
    SESSION_CREATED = "session.created"
    CALL_STARTED = "call.started"
    CALL_STATUS = "call.status"
    CALL_DISCONNECTED = "call.disconnected"
    SESSION_ENDED = "session.ended"
    IDENTITY_CHALLENGE = "identity.challenge"
    IDENTITY_NAME_CONFIRMED = "identity.name_confirmed"
    IDENTITY_VERIFIED = "identity.verified"
    IDENTITY_FAILED = "identity.failed"
    IDENTITY_WRONG_PARTY = "identity.wrong_party"
    DISCLOSURE_ALLOWED = "disclosure.allowed"
    DISCLOSURE_BLOCKED = "disclosure.blocked"
    PAYMENT_PROPOSED = "payment.proposed"
    PAYMENT_PROPOSAL_ALLOWED = "payment.proposal_allowed"
    PAYMENT_PROPOSAL_REJECTED = "payment.proposal_rejected"
    PROMISE_CONFIRMED = "promise.confirmed"
    STOP_CONTACT_REQUESTED = "stop_contact.requested"
    HUMAN_TRANSFER_REQUESTED = "transfer.requested"
    HUMAN_TRANSFER_STATUS = "transfer.status"
    POLICY_DECISION = "policy.decision"
    BARGE_IN = "voice.barge_in"
    PROVIDER_FAILURE = "provider.failure"
    RESPONSE_GUARD_BLOCKED = "response.guard_blocked"
    LLM_PROPOSAL_REJECTED = "llm.proposal_rejected"


@dataclass(frozen=True)
class AuditEvent:
    event_id: uuid.UUID
    session_id: uuid.UUID
    type: AuditType
    at: datetime
    data: dict[str, Any] = field(default_factory=dict)
    turn_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": str(self.event_id),
            "session_id": str(self.session_id),
            "type": self.type.value,
            "at": self.at.isoformat(),
            "turn_index": self.turn_index,
            "data": self.data,
        }


AuditListener = Callable[[AuditEvent], None]


class AuditLog:
    """In-memory ordered audit log for one session with synchronous listeners.

    Listeners (persistence queue, websocket fan-out) must not raise; failures are
    isolated so auditing never breaks the call path.
    """

    def __init__(self, session_id: uuid.UUID, now: Callable[[], datetime]) -> None:
        self.session_id = session_id
        self._now = now
        self.events: list[AuditEvent] = []
        self._listeners: list[AuditListener] = []

    def subscribe(self, fn: AuditListener) -> None:
        self._listeners.append(fn)

    def record(self, type_: AuditType, turn_index: int | None = None, /, **data: Any) -> AuditEvent:
        ev = AuditEvent(uuid.uuid4(), self.session_id, type_, self._now(), dict(data), turn_index)
        self.events.append(ev)
        for fn in list(self._listeners):
            try:
                fn(ev)
            except Exception:  # noqa: S110 - listener isolation is intentional
                pass
        return ev

    def of_type(self, type_: AuditType) -> list[AuditEvent]:
        return [e for e in self.events if e.type == type_]
