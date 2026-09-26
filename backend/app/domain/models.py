"""Core domain types.

`CollectionState` is the *authoritative* structured state of a conversation. It is
mutated only by `ConversationController.apply()` after the policy engine has
approved a typed proposal. Transcript text is never treated as authoritative.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class Language(StrEnum):
    EN = "en"
    JA = "ja"


class Channel(StrEnum):
    BROWSER = "browser"
    PHONE = "phone"
    EVAL = "eval"


class IdentityStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"  # nothing confirmed yet
    NAME_CONFIRMED = "NAME_CONFIRMED"  # caller says they are the account holder; not yet verified
    VERIFIED = "VERIFIED"  # knowledge factor (date of birth) matched
    FAILED = "FAILED"  # knowledge factor failed too many times
    WRONG_PARTY = "WRONG_PARTY"  # caller is not the account holder


class PromiseStatus(StrEnum):
    NONE = "NONE"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class TransferStatus(StrEnum):
    NONE = "NONE"
    REQUESTED = "REQUESTED"
    SIMULATED = "SIMULATED"  # no telephony: transfer recorded at the domain level only
    DIALING = "DIALING"
    CONNECTED = "CONNECTED"
    FAILED = "FAILED"


class CallStatus(StrEnum):
    CREATED = "CREATED"
    IN_PROGRESS = "IN_PROGRESS"
    TRANSFER_REQUESTED = "TRANSFER_REQUESTED"
    COMPLETED = "COMPLETED"
    DISCONNECTED = "DISCONNECTED"
    FAILED = "FAILED"


class DialogPhase(StrEnum):
    GREETING = "GREETING"
    IDENTITY_NAME = "IDENTITY_NAME"
    IDENTITY_DOB = "IDENTITY_DOB"
    NEGOTIATION = "NEGOTIATION"
    CONFIRMATION = "CONFIRMATION"
    CLOSING = "CLOSING"
    ENDED = "ENDED"


TERMINAL_CALL_STATUSES = {CallStatus.COMPLETED, CallStatus.DISCONNECTED, CallStatus.FAILED}


@dataclass(frozen=True)
class DebtorProfile:
    """Synthetic demo identity. Never real personal data."""

    debtor_id: uuid.UUID
    full_name: str
    name_aliases: tuple[str, ...]  # accepted spoken variants (e.g. kanji + romaji)
    date_of_birth: date
    phone_e164: str  # synthetic, never dialled unless on the operator allow-list
    preferred_language: Language
    timezone: str = "Asia/Tokyo"
    display_name_ja: str = ""


@dataclass(frozen=True)
class AccountTerms:
    """Approved negotiation envelope for one synthetic collection account."""

    account_id: uuid.UUID
    debtor_id: uuid.UUID
    creditor_name: str
    outstanding_balance: int  # integer minor units; JPY has no minor unit
    currency: str
    allowed_min_payment: int
    max_extension_days: int
    discount_authority: bool = False
    contact_attempts: int = 0
    stop_contact: bool = False


@dataclass(frozen=True)
class DobParts:
    """Date-of-birth parts the caller explicitly said. Missing parts stay None and are
    never filled in; identity is only verified from a complete, real calendar date."""

    year: int | None = None
    month: int | None = None
    day: int | None = None

    @property
    def has_any(self) -> bool:
        return any(v is not None for v in (self.year, self.month, self.day))

    @property
    def complete(self) -> bool:
        return all(v is not None for v in (self.year, self.month, self.day))

    @property
    def missing(self) -> list[str]:
        return [k for k in ("year", "month", "day") if getattr(self, k) is None]

    def as_date(self) -> date | None:
        if self.year is None or self.month is None or self.day is None:
            return None
        try:
            return date(self.year, self.month, self.day)
        except ValueError:
            return None

    def merged_over(self, earlier: DobParts | None) -> DobParts:
        """Parts from this turn replace (correct) earlier parts; missing ones are kept."""
        if earlier is None:
            return self
        return DobParts(
            self.year if self.year is not None else earlier.year,
            self.month if self.month is not None else earlier.month,
            self.day if self.day is not None else earlier.day,
        )


@dataclass
class PaymentPromise:
    promise_id: uuid.UUID
    amount: int
    currency: str
    due_date: date
    confirmation_turn: int
    confirmed_at: datetime
    policy_decision_ids: list[uuid.UUID]


@dataclass
class CollectionState:
    session_id: uuid.UUID
    debtor_id: uuid.UUID
    account_id: uuid.UUID
    language: Language
    channel: Channel

    outstanding_balance: int
    currency: str
    allowed_min_payment: int
    max_extension_days: int

    identity_status: IdentityStatus = IdentityStatus.UNVERIFIED
    identity_attempts: int = 0
    debt_disclosed: bool = False
    partial_dob: DobParts | None = None  # parts collected so far while the DOB is incomplete

    proposed_amount: int | None = None
    proposed_date: date | None = None
    promise_status: PromiseStatus = PromiseStatus.NONE
    readback_delivered: bool = False  # the confirmation read-back was actually played to the caller
    promise: PaymentPromise | None = None
    rejected_proposals: int = 0

    stop_contact: bool = False
    future_contact_eligible: bool = True

    human_transfer_requested: bool = False
    transfer_reason: str | None = None
    transfer_status: TransferStatus = TransferStatus.NONE

    call_status: CallStatus = CallStatus.CREATED
    ended_reason: str | None = None
    phase: DialogPhase = DialogPhase.GREETING
    silence_reprompts: int = 0
    unclear_count: int = 0

    @property
    def disclosure_allowed(self) -> bool:
        return self.identity_status == IdentityStatus.VERIFIED

    @property
    def ended(self) -> bool:
        """No further caller turn may change state (call over, or handed to a human)."""
        return self.call_status in TERMINAL_CALL_STATUSES or self.phase == DialogPhase.ENDED

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe snapshot. Balance fields are included because this is the
        operator view; the *caller-facing* disclosure guard lives in the response layer."""
        d = asdict(self)
        d["disclosure_allowed"] = self.disclosure_allowed
        return _jsonable(d)


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, StrEnum):
        return v.value
    return v


@dataclass
class Turn:
    index: int
    speaker: str  # "caller" | "agent"
    text: str
    started_at: datetime
    interrupted: bool = False
    spoken_text: str | None = None  # for interrupted agent turns: approx. what was actually played
    intent: str | None = None
    latency: dict[str, float] = field(default_factory=dict)


def new_id() -> uuid.UUID:
    return uuid.uuid4()
