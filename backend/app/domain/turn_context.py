"""Allowed-action contract: which caller actions are valid in the current phase.

The controller owns the phase, so it owns this contract. The same `TurnContext` is
handed to every layer that reads a caller turn:

* the LLM (restricted JSON-schema enum + expected slot in the prompt),
* the deterministic parser (which extractors run at all — while a date of birth is
  expected, digits are only ever DOB candidates, never money),
* the validation layer in `understanding` and, again, `ConversationController.apply()`
  (defence in depth: an out-of-phase action is dropped and audited, never acted on).

Prompt wording alone is never relied on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .commands import Action, Interpretation, ProposedAction
from .models import DialogPhase


class ExpectedSlot(StrEnum):
    NAME_CONFIRMATION = "name_confirmation"
    DATE_OF_BIRTH = "date_of_birth"
    PAYMENT_PLAN = "payment_plan"
    PROMISE_CONFIRMATION = "promise_confirmation"
    CLOSING = "closing"
    NONE = "none"


# Caller rights (stop contact, human) and conversational basics are valid everywhere.
GLOBAL_ACTIONS = frozenset({Action.STOP_CONTACT, Action.REQUEST_HUMAN, Action.GOODBYE, Action.UNCLEAR})
_IDENTITY_COMMON = frozenset({Action.WRONG_PERSON, Action.ASK_PURPOSE, Action.ASK_BALANCE, Action.DISPUTE})
_DOB = frozenset({Action.PROVIDE_DOB, Action.PARTIAL_DOB})
_PAYMENT = frozenset(
    {
        Action.AFFIRM,
        Action.DENY,
        Action.PROPOSE_PAYMENT,
        Action.CANNOT_PAY,
        Action.REQUEST_DISCOUNT,
        Action.ASK_BALANCE,
        Action.ASK_PURPOSE,
        Action.DISPUTE,
    }
)

_CONTRACT: dict[DialogPhase, tuple[ExpectedSlot, frozenset[Action]]] = {
    # A caller may give their DOB straight away instead of "yes" (implies the name).
    DialogPhase.GREETING: (ExpectedSlot.NAME_CONFIRMATION, _IDENTITY_COMMON | _DOB | {Action.AFFIRM, Action.DENY}),
    DialogPhase.IDENTITY_NAME: (
        ExpectedSlot.NAME_CONFIRMATION,
        _IDENTITY_COMMON | _DOB | {Action.AFFIRM, Action.DENY},
    ),
    # Waiting for the knowledge factor: no payment amounts, dates or consent.
    DialogPhase.IDENTITY_DOB: (ExpectedSlot.DATE_OF_BIRTH, _IDENTITY_COMMON | _DOB),
    DialogPhase.NEGOTIATION: (ExpectedSlot.PAYMENT_PLAN, _PAYMENT),
    DialogPhase.CONFIRMATION: (ExpectedSlot.PROMISE_CONFIRMATION, _PAYMENT),
    DialogPhase.CLOSING: (ExpectedSlot.CLOSING, _PAYMENT),
    DialogPhase.ENDED: (ExpectedSlot.NONE, frozenset()),
}


@dataclass(frozen=True)
class TurnContext:
    phase: DialogPhase
    expected_slot: ExpectedSlot
    allowed: frozenset[Action]

    def allows(self, action: Action) -> bool:
        return action in self.allowed

    @property
    def expects_dob(self) -> bool:
        return self.expected_slot == ExpectedSlot.DATE_OF_BIRTH

    @property
    def identity_phase(self) -> bool:
        return self.expected_slot in (ExpectedSlot.NAME_CONFIRMATION, ExpectedSlot.DATE_OF_BIRTH)

    def describe(self) -> dict[str, object]:
        return {
            "phase": self.phase.value,
            "expected_slot": self.expected_slot.value,
            "allowed_actions": sorted(a.value for a in self.allowed),
        }


def context_for(phase: DialogPhase) -> TurnContext:
    slot, allowed = _CONTRACT[phase]
    return TurnContext(phase, slot, allowed | GLOBAL_ACTIONS)


def constrain(interp: Interpretation, ctx: TurnContext) -> Interpretation:
    """Drop actions the phase does not accept. Never adds meaning: an empty result is UNCLEAR."""
    kept = [a for a in interp.actions if ctx.allows(a.action)]
    dropped = [a.action.value for a in interp.actions if not ctx.allows(a.action)]
    if not dropped:
        return interp
    if not kept:
        kept = [ProposedAction(action=Action.UNCLEAR)]
    notes = [*interp.notes, *(f"out_of_phase:{d}" for d in dropped if f"out_of_phase:{d}" not in interp.notes)]
    return Interpretation(actions=kept, source=interp.source, notes=notes[:20])
