"""Deterministic demo policy engine.

These rules are *simulated demo policy* inspired by regulated collections workflows.
They are NOT a statement of Japanese law (e.g. the Money Lending Business Act or
Servicer Act) and have not been reviewed by counsel. Every evaluation returns a
`PolicyDecision`, and every decision is written to the audit trail.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from .models import AccountTerms, CollectionState, IdentityStatus


class Rule(StrEnum):
    CONTACT_WINDOW = "DEMO_CALLING_HOURS_WINDOW"
    MAX_CONTACT_ATTEMPTS = "MAX_CONTACT_ATTEMPTS"
    STOP_CONTACT_ACTIVE = "STOP_CONTACT_BLOCKS_CONTACT"
    IDENTITY_BEFORE_DISCLOSURE = "IDENTITY_REQUIRED_BEFORE_DISCLOSURE"
    IDENTITY_ATTEMPT_LIMIT = "IDENTITY_ATTEMPT_LIMIT"
    WRONG_PARTY_NO_DISCLOSURE = "WRONG_PARTY_NO_DISCLOSURE"
    PAYMENT_MIN_AMOUNT = "PAYMENT_MIN_AMOUNT"
    PAYMENT_MAX_AMOUNT = "PAYMENT_NOT_ABOVE_BALANCE"
    PAYMENT_DATE_WINDOW = "PAYMENT_DATE_WITHIN_MAX_EXTENSION"
    NO_DISCOUNT_AUTHORITY = "NO_DISCOUNT_AUTHORITY"
    PROMISE_EXPLICIT_CONFIRMATION = "PROMISE_REQUIRES_EXPLICIT_CONFIRMATION"
    PROMISE_SINGLE = "ONE_CONFIRMED_PROMISE_PER_SESSION"
    STOP_CONTACT_REQUEST = "STOP_CONTACT_HONOURED"
    HUMAN_TRANSFER = "HUMAN_TRANSFER_ON_REQUEST"
    RESPONSE_GUARD = "RESPONSE_DISCLOSURE_GUARD"


CALLING_WINDOW_COUNTRIES = {"JP"}


class Decision(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class PolicyDecision:
    rule: Rule
    decision: Decision
    reason: str
    session_id: uuid.UUID | None
    at: datetime
    details: dict[str, Any] = field(default_factory=dict)
    decision_id: uuid.UUID = field(default_factory=uuid.uuid4)

    @property
    def allowed(self) -> bool:
        return self.decision != Decision.BLOCK

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": str(self.decision_id),
            "rule": self.rule.value,
            "decision": self.decision.value,
            "reason": self.reason,
            "session_id": str(self.session_id) if self.session_id else None,
            "timestamp": self.at.isoformat(),
            "details": self.details,
        }


@dataclass(frozen=True)
class PolicyConfig:
    # Country whose simulated calling-hours window applies to outbound demo contact.
    # Only countries in CALLING_WINDOW_COUNTRIES have a window; anything else (including
    # empty) makes the window rule NOT_APPLICABLE. Attempt and stop-contact rules always run.
    country: str = "JP"
    timezone: str = "Asia/Tokyo"
    calling_start_hour: int = 8
    calling_end_hour: int = 21  # exclusive
    max_contact_attempts: int = 3
    max_identity_attempts: int = 2


@dataclass(frozen=True)
class PaymentEvaluation:
    decisions: list[PolicyDecision]
    earliest_date: date
    latest_date: date

    @property
    def allowed(self) -> bool:
        return all(d.allowed for d in self.decisions)

    @property
    def violations(self) -> list[PolicyDecision]:
        return [d for d in self.decisions if not d.allowed]


class PolicyEngine:
    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()

    def _d(
        self, rule: Rule, decision: Decision, reason: str, session_id: uuid.UUID | None, now: datetime, **details: Any
    ) -> PolicyDecision:
        return PolicyDecision(rule, decision, reason, session_id, now, details)

    # ---- contact eligibility (outbound initiation) --------------------------------
    @property
    def calling_window_applies(self) -> bool:
        return self.config.country.strip().upper() in CALLING_WINDOW_COUNTRIES

    def evaluate_contact(
        self,
        account: AccountTerms,
        now: datetime,
        session_id: uuid.UUID | None = None,
        *,
        debtor_stop_contact: bool = False,
        contact_point_stop_contact: bool = False,
    ) -> list[PolicyDecision]:
        """Outbound-contact gate. Stop-contact blocks if the account, its debtor, or the
        destination contact point has an active request (owner ruling, 2026-09-26)."""
        out = []
        if self.calling_window_applies:
            local = now.astimezone(ZoneInfo(self.config.timezone))
            in_window = self.config.calling_start_hour <= local.hour < self.config.calling_end_hour
            out.append(
                self._d(
                    Rule.CONTACT_WINDOW,
                    Decision.ALLOW if in_window else Decision.BLOCK,
                    f"local time {local:%H:%M} {self.config.timezone}; simulated demo window "
                    f"{self.config.calling_start_hour:02d}:00-{self.config.calling_end_hour:02d}:00 "
                    f"(POLICY_COUNTRY={self.config.country.upper()})",
                    session_id,
                    now,
                    local_time=local.isoformat(),
                    country=self.config.country.upper(),
                )
            )
        else:
            out.append(
                self._d(
                    Rule.CONTACT_WINDOW,
                    Decision.NOT_APPLICABLE,
                    f"no simulated calling-hours window configured for POLICY_COUNTRY="
                    f"{self.config.country.upper() or '(empty)'}",
                    session_id,
                    now,
                    country=self.config.country.upper(),
                )
            )
        under = account.contact_attempts < self.config.max_contact_attempts
        out.append(
            self._d(
                Rule.MAX_CONTACT_ATTEMPTS,
                Decision.ALLOW if under else Decision.BLOCK,
                f"{account.contact_attempts} prior attempts; limit {self.config.max_contact_attempts}",
                session_id,
                now,
                attempts=account.contact_attempts,
                limit=self.config.max_contact_attempts,
            )
        )
        scopes = [
            name
            for name, flagged in (
                ("account", account.stop_contact),
                ("debtor", debtor_stop_contact),
                ("contact_point", contact_point_stop_contact),
            )
            if flagged
        ]
        out.append(
            self._d(
                Rule.STOP_CONTACT_ACTIVE,
                Decision.BLOCK if scopes else Decision.ALLOW,
                f"debtor/contact has an active stop-contact request (scope: {', '.join(scopes)})"
                if scopes
                else "no stop-contact request for this account, debtor or contact point",
                session_id,
                now,
                scopes=scopes,
            )
        )
        return out

    def reviewer_initiated_contact(self, session_id: uuid.UUID, now: datetime) -> PolicyDecision:
        """Browser demo sessions are started by the reviewer, not dialled to a debtor."""
        return self._d(
            Rule.CONTACT_WINDOW,
            Decision.NOT_APPLICABLE,
            "reviewer-initiated browser session; outbound contact rules apply to telephony only",
            session_id,
            now,
        )

    # ---- disclosure -------------------------------------------------------------
    def evaluate_disclosure(self, state: CollectionState, now: datetime) -> PolicyDecision:
        if state.identity_status == IdentityStatus.VERIFIED:
            return self._d(Rule.IDENTITY_BEFORE_DISCLOSURE, Decision.ALLOW, "identity verified", state.session_id, now)
        rule = (
            Rule.WRONG_PARTY_NO_DISCLOSURE
            if state.identity_status == IdentityStatus.WRONG_PARTY
            else Rule.IDENTITY_BEFORE_DISCLOSURE
        )
        return self._d(
            rule,
            Decision.BLOCK,
            f"identity status is {state.identity_status.value}; account details may not be disclosed",
            state.session_id,
            now,
            identity_status=state.identity_status.value,
        )

    def evaluate_identity_attempts(self, state: CollectionState, now: datetime) -> PolicyDecision:
        exceeded = state.identity_attempts >= self.config.max_identity_attempts
        return self._d(
            Rule.IDENTITY_ATTEMPT_LIMIT,
            Decision.BLOCK if exceeded else Decision.ALLOW,
            f"{state.identity_attempts} failed verification attempts; limit {self.config.max_identity_attempts}",
            state.session_id,
            now,
            attempts=state.identity_attempts,
        )

    # ---- negotiation ------------------------------------------------------------
    def payment_window(self, state: CollectionState, today: date) -> tuple[date, date]:
        return today, today + timedelta(days=state.max_extension_days)

    def evaluate_payment(
        self, state: CollectionState, amount: int, due: date, today: date, now: datetime
    ) -> PaymentEvaluation:
        sid = state.session_id
        earliest, latest = self.payment_window(state, today)
        ds = [self.evaluate_disclosure(state, now)]
        ds.append(
            self._d(
                Rule.PAYMENT_MIN_AMOUNT,
                Decision.ALLOW
                if amount >= min(state.allowed_min_payment, state.outstanding_balance)
                else Decision.BLOCK,
                f"amount {amount} vs approved minimum {state.allowed_min_payment}",
                sid,
                now,
                amount=amount,
                minimum=state.allowed_min_payment,
            )
        )
        ds.append(
            self._d(
                Rule.PAYMENT_MAX_AMOUNT,
                Decision.ALLOW if amount <= state.outstanding_balance else Decision.BLOCK,
                f"amount {amount} vs outstanding balance {state.outstanding_balance}",
                sid,
                now,
                amount=amount,
                outstanding=state.outstanding_balance,
            )
        )
        in_window = earliest <= due <= latest
        ds.append(
            self._d(
                Rule.PAYMENT_DATE_WINDOW,
                Decision.ALLOW if in_window else Decision.BLOCK,
                f"due {due.isoformat()} vs allowed window {earliest.isoformat()}..{latest.isoformat()} "
                f"(max extension {state.max_extension_days} days)",
                sid,
                now,
                due=due.isoformat(),
                earliest=earliest.isoformat(),
                latest=latest.isoformat(),
            )
        )
        return PaymentEvaluation(ds, earliest, latest)

    def evaluate_discount_request(self, state: CollectionState, terms_discount: bool, now: datetime) -> PolicyDecision:
        return self._d(
            Rule.NO_DISCOUNT_AUTHORITY,
            Decision.ALLOW if terms_discount else Decision.BLOCK,
            "agent has no authority to reduce or waive balances" if not terms_discount else "discount authority",
            state.session_id,
            now,
        )

    def evaluate_promise_confirmation(
        self, state: CollectionState, explicit_affirm: bool, today: date, now: datetime
    ) -> list[PolicyDecision]:
        """Final gate before a promise-to-pay becomes CONFIRMED."""
        out: list[PolicyDecision] = []
        if state.promise is not None:
            out.append(
                self._d(Rule.PROMISE_SINGLE, Decision.BLOCK, "a promise is already confirmed", state.session_id, now)
            )
            return out
        out.append(
            self._d(
                Rule.PROMISE_EXPLICIT_CONFIRMATION,
                Decision.ALLOW if explicit_affirm else Decision.BLOCK,
                "caller explicitly confirmed the read-back" if explicit_affirm else "no explicit confirmation",
                state.session_id,
                now,
            )
        )
        if state.proposed_amount is None or state.proposed_date is None:
            out.append(
                self._d(
                    Rule.PROMISE_EXPLICIT_CONFIRMATION,
                    Decision.BLOCK,
                    "no complete proposal to confirm",
                    state.session_id,
                    now,
                )
            )
            return out
        out.extend(self.evaluate_payment(state, state.proposed_amount, state.proposed_date, today, now).decisions)
        return out

    def readback_not_delivered(self, state: CollectionState, now: datetime) -> PolicyDecision:
        return self._d(
            Rule.PROMISE_EXPLICIT_CONFIRMATION,
            Decision.BLOCK,
            "affirmation received before the confirmation read-back was fully played; re-asking",
            state.session_id,
            now,
        )

    def stop_contact(self, state: CollectionState, now: datetime) -> PolicyDecision:
        return self._d(
            Rule.STOP_CONTACT_REQUEST,
            Decision.ALLOW,
            "caller requested no further contact; future contact disabled",
            state.session_id,
            now,
        )

    def human_transfer(self, state: CollectionState, now: datetime, reason: str) -> PolicyDecision:
        return self._d(
            Rule.HUMAN_TRANSFER,
            Decision.ALLOW,
            f"transfer requested: {reason}",
            state.session_id,
            now,
            transfer_reason=reason,
        )
