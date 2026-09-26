"""Conversation controller: the single owner of authoritative collection state.

`apply()` is synchronous and side-effect free apart from mutating `self.state` and
appending audit events. It takes a typed `Interpretation` (from rules or an LLM),
runs every relevant policy check and returns a `TurnOutcome` describing what the
agent is allowed to say and which effects (end call, transfer, notification) the
runtime must execute. Nothing the LLM produces can change state without passing
through this function.
"""

from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from .audit import AuditLog, AuditType
from .clock import Clock
from .commands import Action, Interpretation, ProposedAction
from .models import (
    AccountTerms,
    CallStatus,
    Channel,
    CollectionState,
    DebtorProfile,
    DialogPhase,
    DobParts,
    IdentityStatus,
    Language,
    PaymentPromise,
    PromiseStatus,
    TransferStatus,
)
from .policy import PolicyDecision, PolicyEngine, Rule
from .responses import REJECT_REASON, Act, ResponsePlan, fmt_date, fmt_money
from .scenarios import ORG_NAME
from .turn_context import TurnContext, constrain, context_for


class Effect(StrEnum):
    END_CALL = "END_CALL"
    TRANSFER = "TRANSFER"
    SEND_PTP_CONFIRMATION = "SEND_PTP_CONFIRMATION"
    DISABLE_FUTURE_CONTACT = "DISABLE_FUTURE_CONTACT"


@dataclass
class TurnOutcome:
    plan: ResponsePlan
    decisions: list[PolicyDecision] = field(default_factory=list)
    effects: list[Effect] = field(default_factory=list)
    state_changes: list[str] = field(default_factory=list)


MAX_UNCLEAR_BEFORE_TRANSFER = 3
MAX_SILENCE_REPROMPTS = 2
READBACK_MIN_PLAYED = 0.9


class ConversationController:
    def __init__(
        self,
        *,
        session_id: uuid.UUID,
        debtor: DebtorProfile,
        account: AccountTerms,
        language: Language,
        channel: Channel,
        policy: PolicyEngine,
        clock: Clock,
        audit: AuditLog,
    ) -> None:
        self.debtor = debtor
        self.account = account
        self.policy = policy
        self.clock = clock
        self.audit = audit
        self.state = CollectionState(
            session_id=session_id,
            debtor_id=debtor.debtor_id,
            account_id=account.account_id,
            language=language,
            channel=channel,
            outstanding_balance=account.outstanding_balance,
            currency=account.currency,
            allowed_min_payment=account.allowed_min_payment,
            max_extension_days=account.max_extension_days,
        )
        self.turn_index = 0
        self.last_agent_text = ""

    # ------------------------------------------------------------------ helpers
    @property
    def lang(self) -> Language:
        return self.state.language

    def today(self) -> date:
        return self.clock.today_in(self.debtor.timezone)

    def _record(self, d: PolicyDecision, out: TurnOutcome) -> PolicyDecision:
        out.decisions.append(d)
        self.audit.record(AuditType.POLICY_DECISION, self.turn_index, **d.to_dict())
        return d

    def _name(self) -> str:
        return self.debtor.display_name_ja if self.lang == Language.JA else self.debtor.full_name

    def _base_slots(self) -> dict[str, str]:
        s = self.state
        _, latest = self.policy.payment_window(s, self.today())
        slots = {
            "org": ORG_NAME[self.lang],
            "name": self._name(),
            "first_name": self.debtor.full_name.split()[0],
            "family_name": (self.debtor.display_name_ja.split() or [""])[0],
        }
        if s.partial_dob is not None:
            slots["dob_question"] = self._dob_question(s.partial_dob)
        if s.disclosure_allowed:
            slots.update(
                balance=fmt_money(s.outstanding_balance, s.currency, self.lang),
                min=fmt_money(s.allowed_min_payment, s.currency, self.lang),
                latest=fmt_date(latest, self.lang),
                max_days=str(s.max_extension_days),
            )
            if s.proposed_amount is not None:
                slots["amount"] = fmt_money(s.proposed_amount, s.currency, self.lang)
            if s.proposed_date is not None:
                slots["date"] = fmt_date(s.proposed_date, self.lang)
        return slots

    def _plan(self, acts: list[Act], out: TurnOutcome, **extra: str) -> ResponsePlan:
        s = self.state
        _, latest = self.policy.payment_window(s, self.today())
        approved = set()
        dates = set()
        if s.disclosure_allowed:
            approved = {s.outstanding_balance, s.allowed_min_payment}
            dates = {latest}
            if s.proposed_amount is not None:
                approved.add(s.proposed_amount)
            if s.proposed_date is not None:
                dates.add(s.proposed_date)
        slots = self._base_slots() | extra
        plan = ResponsePlan(
            acts=acts,
            language=self.lang,
            slots=slots,
            disclosure_allowed=s.disclosure_allowed,
            approved_amounts=approved,
            approved_dates=dates,
            end_call=Effect.END_CALL in out.effects,
            transfer=Effect.TRANSFER in out.effects,
        )
        out.plan = plan
        return plan

    def _end(self, out: TurnOutcome, reason: str, status: CallStatus = CallStatus.COMPLETED) -> None:
        self.state.call_status = status
        self.state.ended_reason = reason
        self.state.phase = DialogPhase.ENDED
        if Effect.END_CALL not in out.effects:
            out.effects.append(Effect.END_CALL)
        out.state_changes.append(f"call_status={status.value}")

    def _new_outcome(self) -> TurnOutcome:
        return TurnOutcome(plan=ResponsePlan(acts=[Act.CLARIFY], language=self.lang))

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> TurnOutcome:
        out = self._new_outcome()
        self.state.call_status = CallStatus.IN_PROGRESS
        self.state.phase = DialogPhase.IDENTITY_NAME
        self.audit.record(AuditType.IDENTITY_CHALLENGE, 0, factor="name_confirmation")
        self._record(self.policy.evaluate_disclosure(self.state, self.clock.now()), out)
        self._plan([Act.GREETING], out)
        return out

    def on_silence(self) -> TurnOutcome:
        out = self._new_outcome()
        if self.state.ended:
            self._plan([Act.CLOSING], out)
            return out
        self.state.silence_reprompts += 1
        if self.state.silence_reprompts > MAX_SILENCE_REPROMPTS:
            self._end(out, "silence_timeout", CallStatus.DISCONNECTED)
            self._plan([Act.SILENCE_END], out)
        else:
            self._plan([Act.REPROMPT_SILENCE, *self._current_question()], out)
        return out

    def on_disconnect(self, reason: str = "caller_hangup") -> None:
        # A completed/handed-off call keeps its final status (e.g. TRANSFER_REQUESTED).
        if not self.state.ended:
            self.state.call_status = CallStatus.DISCONNECTED
            self.state.ended_reason = reason
            self.state.phase = DialogPhase.ENDED
            # A pending (unconfirmed) proposal never becomes a promise on hang-up.
            if self.state.promise_status == PromiseStatus.PENDING_CONFIRMATION:
                self.state.promise_status = PromiseStatus.NONE
            self.audit.record(AuditType.CALL_DISCONNECTED, self.turn_index, reason=reason)

    def _current_question(self) -> list[Act]:
        s = self.state
        match s.phase:
            case DialogPhase.IDENTITY_NAME | DialogPhase.GREETING:
                return [Act.ASK_NAME_AGAIN]
            case DialogPhase.IDENTITY_DOB:
                return [Act.ASK_DOB_PART] if s.partial_dob else [Act.ASK_DOB]
            case DialogPhase.CONFIRMATION:
                return [Act.CONFIRM_PROPOSAL]
            case DialogPhase.NEGOTIATION:
                if s.proposed_amount is not None and s.proposed_date is None:
                    return [Act.ASK_DATE]
                if s.proposed_date is not None and s.proposed_amount is None:
                    return [Act.ASK_AMOUNT]
                return [Act.ASK_PLAN]
            case _:
                return []

    # ------------------------------------------------------------------ main entry
    def turn_context(self) -> TurnContext:
        """The allowed-action contract for the caller's next turn (see turn_context.py)."""
        return context_for(self.state.phase)

    def apply(self, interp: Interpretation, caller_text: str = "") -> TurnOutcome:
        # Defence in depth: whatever produced `interp` (LLM, rules, a test), actions the
        # current phase does not accept are dropped here and audited, never acted on.
        ctx = self.turn_context()
        interp = constrain(interp, ctx)
        dropped = [n.split(":", 1)[1] for n in interp.notes if n.startswith("out_of_phase:")]
        if dropped:
            self.audit.record(
                AuditType.NLU_OUT_OF_PHASE,
                self.turn_index + 1,
                phase=ctx.phase.value,
                expected_slot=ctx.expected_slot.value,
                dropped=dropped,
                source=interp.source,
            )
        out = self._apply(interp, caller_text)
        s = self.state
        # Consent only ever answers the read-back that was *just* asked: any other reply
        # while confirmation is pending (no-discount, balance info, ...) voids it.
        if s.phase == DialogPhase.CONFIRMATION and out.plan.primary != Act.CONFIRM_PROPOSAL:
            s.readback_delivered = False
        return out

    def _apply(self, interp: Interpretation, caller_text: str = "") -> TurnOutcome:
        self.turn_index += 1
        out = self._new_outcome()
        s = self.state
        now = self.clock.now()
        if s.ended:
            self._plan([Act.CLOSING], out)
            return out
        s.silence_reprompts = 0

        # 1. caller-rights intents win regardless of phase or identity.
        if interp.has(Action.STOP_CONTACT):
            return self._stop_contact(out, now)
        if interp.has(Action.REQUEST_HUMAN):
            return self._transfer(out, now, "caller_requested_human", Act.TRANSFER)
        if interp.has(Action.DISPUTE) and s.identity_status == IdentityStatus.VERIFIED:
            return self._transfer(out, now, "caller_disputes_debt", Act.DISPUTE_TRANSFER)

        # 2. identity gate
        if s.identity_status in (IdentityStatus.UNVERIFIED, IdentityStatus.NAME_CONFIRMED):
            return self._identity(interp, out, now)

        # 3. verified caller
        if interp.has(Action.DISPUTE):
            return self._transfer(out, now, "caller_disputes_debt", Act.DISPUTE_TRANSFER)
        if s.phase == DialogPhase.CLOSING:
            return self._closing(interp, out, now)
        if s.phase == DialogPhase.CONFIRMATION:
            return self._confirmation(interp, out, now)
        return self._negotiation(interp, out, now)

    # ------------------------------------------------------------------ branches
    def _stop_contact(self, out: TurnOutcome, now: object) -> TurnOutcome:
        s = self.state
        s.stop_contact = True
        s.future_contact_eligible = False
        if s.promise_status == PromiseStatus.PENDING_CONFIRMATION:
            s.promise_status = PromiseStatus.NONE
        self._record(self.policy.stop_contact(s, self.clock.now()), out)
        self.audit.record(AuditType.STOP_CONTACT_REQUESTED, self.turn_index, future_contact_eligible=False)
        out.effects.append(Effect.DISABLE_FUTURE_CONTACT)
        out.state_changes += ["stop_contact=true", "future_contact_eligible=false"]
        self._end(out, "stop_contact_requested")
        self._plan([Act.STOP_CONTACT_ACK], out)
        return out

    def _transfer(self, out: TurnOutcome, now: object, reason: str, act: Act) -> TurnOutcome:
        s = self.state
        s.human_transfer_requested = True
        s.transfer_reason = reason
        s.transfer_status = TransferStatus.REQUESTED
        s.call_status = CallStatus.TRANSFER_REQUESTED
        if s.promise_status == PromiseStatus.PENDING_CONFIRMATION:
            s.promise_status = PromiseStatus.NONE
        self._record(self.policy.human_transfer(s, self.clock.now(), reason), out)
        self.audit.record(AuditType.HUMAN_TRANSFER_REQUESTED, self.turn_index, reason=reason)
        out.effects.append(Effect.TRANSFER)
        out.state_changes += ["human_transfer_requested=true", f"transfer_reason={reason}"]
        s.phase = DialogPhase.ENDED
        s.ended_reason = "transferred_to_human"
        self._plan([act], out)
        return out

    def _identity(self, interp: Interpretation, out: TurnOutcome, now: object) -> TurnOutcome:
        s = self.state
        dob_action = interp.first(Action.PROVIDE_DOB) or interp.first(Action.PARTIAL_DOB)
        wants_details = interp.has(Action.ASK_BALANCE) or interp.has(Action.PROPOSE_PAYMENT)

        if interp.has(Action.WRONG_PERSON) or (
            s.identity_status == IdentityStatus.UNVERIFIED and interp.has(Action.DENY)
        ):
            s.identity_status = IdentityStatus.WRONG_PARTY
            self.audit.record(AuditType.IDENTITY_WRONG_PARTY, self.turn_index)
            d = self._record(self.policy.evaluate_disclosure(s, self.clock.now()), out)
            self.audit.record(AuditType.DISCLOSURE_BLOCKED, self.turn_index, rule=d.rule.value)
            out.state_changes.append("identity_status=WRONG_PARTY")
            self._end(out, "wrong_party")
            self._plan([Act.WRONG_PARTY], out)
            return out

        if s.identity_status == IdentityStatus.UNVERIFIED:
            if interp.has(Action.AFFIRM) or dob_action:
                s.identity_status = IdentityStatus.NAME_CONFIRMED
                s.phase = DialogPhase.IDENTITY_DOB
                self.audit.record(AuditType.IDENTITY_NAME_CONFIRMED, self.turn_index)
                self.audit.record(AuditType.IDENTITY_CHALLENGE, self.turn_index, factor="date_of_birth")
                out.state_changes.append("identity_status=NAME_CONFIRMED")
                if not dob_action:
                    self._plan([Act.ASK_DOB], out)
                    return out
            else:
                return self._pre_verification(interp, out, wants_details, [Act.ASK_NAME_AGAIN])

        # NAME_CONFIRMED: need the knowledge factor
        if dob_action:
            return self._dob(dob_action, interp, out)
        return self._pre_verification(interp, out, wants_details, self._current_question())

    def _dob(self, action: ProposedAction, interp: Interpretation, out: TurnOutcome) -> TurnOutcome:
        """Verify only a complete, real date the caller actually said (possibly across turns)."""
        s = self.state
        given = (
            DobParts(action.dob.year, action.dob.month, action.dob.day)
            if action.dob is not None
            else DobParts(action.dob_year, action.dob_month, action.dob_day)
        )
        previous = s.partial_dob
        parts = given.merged_over(previous)
        trace = {"source": interp.source, "llm_validation_failed": "llm_validation_failed" in interp.notes}
        if not parts.complete:
            s.partial_dob = parts
            self.audit.record(
                AuditType.IDENTITY_PARTIAL_DOB,
                self.turn_index,
                year=parts.year,
                month=parts.month,
                day=parts.day,
                missing=parts.missing,
                merged_with_previous=previous is not None,
                **trace,
            )
            out.state_changes.append(f"partial_dob_missing={','.join(parts.missing)}")
            if parts == previous:  # no progress: count it like an unclear turn
                return self._unclear(out, [Act.ASK_DOB_PART], dob_question=self._dob_question(parts))
            self._plan([Act.ASK_DOB_PART], out, dob_question=self._dob_question(parts))
            return out

        s.partial_dob = None
        dob = parts.as_date()
        if dob is None:  # e.g. April 31: never coerced, and not a failed attempt
            self.audit.record(
                AuditType.IDENTITY_INVALID_DOB,
                self.turn_index,
                year=parts.year,
                month=parts.month,
                day=parts.day,
                reason="not a calendar date",
                **trace,
            )
            self._plan([Act.DOB_INVALID], out)
            return out
        if dob == self.debtor.date_of_birth:
            s.identity_status = IdentityStatus.VERIFIED
            s.phase = DialogPhase.NEGOTIATION
            s.unclear_count = 0
            self.audit.record(AuditType.IDENTITY_VERIFIED, self.turn_index, factor="date_of_birth", **trace)
            d = self._record(self.policy.evaluate_disclosure(s, self.clock.now()), out)
            self.audit.record(AuditType.DISCLOSURE_ALLOWED, self.turn_index, rule=d.rule.value)
            s.debt_disclosed = True
            out.state_changes += ["identity_status=VERIFIED", "debt_disclosed=true"]
            self._plan([Act.DISCLOSE], out)
            return out
        s.identity_attempts += 1
        self.audit.record(AuditType.IDENTITY_FAILED, self.turn_index, attempts=s.identity_attempts, **trace)
        d = self._record(self.policy.evaluate_identity_attempts(s, self.clock.now()), out)
        if not d.allowed:
            s.identity_status = IdentityStatus.FAILED
            out.state_changes.append("identity_status=FAILED")
            self._end(out, "identity_verification_failed")
            self._plan([Act.IDENTITY_FAILED], out)
        else:
            self._plan([Act.DOB_RETRY], out)
        return out

    def _dob_question(self, parts: DobParts) -> str:
        """Ask only for what is missing. Month names are fine; digits are never spoken
        before verification (output guard) and the caller's year is not echoed back."""
        missing = frozenset(parts.missing)
        if self.lang == Language.JA:
            m = f"{parts.month}月" if parts.month else ""
            ja = {
                frozenset({"day"}): f"ありがとうございます。{m}の何日でしょうか。",
                frozenset({"month", "day"}): "ありがとうございます。何月何日でしょうか。",
                frozenset({"year"}): "ありがとうございます。何年のお生まれでしょうか。",
                frozenset({"year", "day"}): f"ありがとうございます。{m}の何日、何年のお生まれでしょうか。",
                frozenset({"month"}): "ありがとうございます。何月でしょうか。",
                frozenset({"year", "month"}): "ありがとうございます。何年何月でしょうか。",
            }
            return ja.get(missing, "ご本人確認のため、生年月日をお教えいただけますか。")
        month = calendar.month_name[parts.month] if parts.month else ""
        en = {
            frozenset({"day"}): f"Thank you. And what day in {month}?",
            frozenset({"month", "day"}): "Thank you. Could you tell me the month and day as well?",
            frozenset({"year"}): "Thank you. And what year were you born?",
            frozenset({"year", "day"}): f"Thank you. And which day in {month}, and what year?",
            frozenset({"month"}): "Thank you. And which month?",
            frozenset({"year", "month"}): "Thank you. Could you tell me the month and year as well?",
        }
        return en.get(missing, "Could you please tell me your full date of birth?")

    def _pre_verification(
        self, interp: Interpretation, out: TurnOutcome, wants_details: bool, question: list[Act]
    ) -> TurnOutcome:
        s = self.state
        if wants_details:
            d = self._record(self.policy.evaluate_disclosure(s, self.clock.now()), out)
            self.audit.record(AuditType.DISCLOSURE_BLOCKED, self.turn_index, rule=d.rule.value, requested="details")
            acts = [Act.PRE_VERIFICATION, *question]
            if interp.has(Action.ASK_PURPOSE):
                acts = [Act.PURPOSE, *question]
            self._plan(acts, out)
            return out
        if interp.has(Action.ASK_PURPOSE):
            self._plan([Act.PURPOSE, *question], out)
            return out
        if interp.has(Action.GOODBYE):
            self._end(out, "caller_ended_before_verification")
            self._plan([Act.CLOSING], out)
            return out
        return self._unclear(out, question)

    def _unclear(self, out: TurnOutcome, question: list[Act], **slots: str) -> TurnOutcome:
        s = self.state
        s.unclear_count += 1
        if s.unclear_count >= MAX_UNCLEAR_BEFORE_TRANSFER:
            return self._transfer(out, self.clock.now(), "repeated_misunderstanding", Act.TRANSFER)
        self._plan([Act.CLARIFY, *question], out, **slots)
        return out

    # -- negotiation ---------------------------------------------------------------
    def _resolve_date(self, a: ProposedAction) -> date | None:
        if a.date is not None:
            return a.date
        if a.days_from_now is not None:
            return self.today() + timedelta(days=a.days_from_now)
        return None

    def _negotiation(self, interp: Interpretation, out: TurnOutcome, now: object) -> TurnOutcome:
        s = self.state
        prop = interp.first(Action.PROPOSE_PAYMENT)
        if prop:
            return self._proposal(prop, interp, out)
        if interp.has(Action.REQUEST_DISCOUNT):
            self._record(
                self.policy.evaluate_discount_request(s, self.account.discount_authority, self.clock.now()), out
            )
            self._plan([Act.NO_DISCOUNT], out)
            return out
        if interp.has(Action.CANNOT_PAY) or interp.has(Action.DENY):
            self._plan([Act.OFFER_TERMS], out)
            return out
        if interp.has(Action.ASK_BALANCE):
            self._plan([Act.BALANCE_INFO, *self._current_question()], out)
            return out
        if interp.has(Action.AFFIRM):
            self._plan(self._current_question(), out)
            return out
        if interp.has(Action.GOODBYE):
            self._end(out, "caller_ended_without_promise")
            self._plan([Act.CLOSING], out)
            return out
        return self._unclear(out, self._current_question())

    def _proposal(self, prop: ProposedAction, interp: Interpretation, out: TurnOutcome) -> TurnOutcome:
        s = self.state
        amount = prop.amount if prop.amount is not None else s.proposed_amount
        due = self._resolve_date(prop) or s.proposed_date
        self.audit.record(
            AuditType.PAYMENT_PROPOSED,
            self.turn_index,
            amount=amount,
            due_date=due.isoformat() if due else None,
            source=interp.source,
            correction=s.promise_status == PromiseStatus.PENDING_CONFIRMATION,
        )
        today = self.today()
        # Evaluate whatever is known; a missing half is evaluated against a neutral value
        # so that e.g. an invalid date is rejected before we even ask for an amount.
        probe_amount = amount if amount is not None else max(s.allowed_min_payment, 0)
        probe_due = due if due is not None else today
        ev = self.policy.evaluate_payment(s, probe_amount, probe_due, today, self.clock.now())
        relevant = [
            d
            for d in ev.decisions
            if (d.rule in (Rule.PAYMENT_MIN_AMOUNT, Rule.PAYMENT_MAX_AMOUNT) and amount is not None)
            or (d.rule == Rule.PAYMENT_DATE_WINDOW and due is not None)
            or d.rule in (Rule.IDENTITY_BEFORE_DISCLOSURE, Rule.WRONG_PARTY_NO_DISCLOSURE)
        ]
        for d in relevant:
            self._record(d, out)
        violations = [d for d in relevant if not d.allowed]
        if violations:
            s.rejected_proposals += 1
            s.promise_status = PromiseStatus.REJECTED
            self.audit.record(
                AuditType.PAYMENT_PROPOSAL_REJECTED,
                self.turn_index,
                amount=amount,
                due_date=due.isoformat() if due else None,
                violated_rules=[d.rule.value for d in violations],
            )
            # Keep only the parts of the proposal that were valid.
            bad = {d.rule for d in violations}
            s.proposed_amount = None if bad & {Rule.PAYMENT_MIN_AMOUNT, Rule.PAYMENT_MAX_AMOUNT} else amount
            s.proposed_date = None if Rule.PAYMENT_DATE_WINDOW in bad else due
            s.phase = DialogPhase.NEGOTIATION
            out.state_changes.append("promise_status=REJECTED")
            slots = self._base_slots()
            lang = self.lang
            reasons = (" " if lang == Language.EN else "").join(
                REJECT_REASON[d.rule.value][lang].format_map(slots) for d in violations if d.rule.value in REJECT_REASON
            )
            self._plan([Act.REJECT_PROPOSAL], out, reasons=reasons)
            return out

        s.proposed_amount = amount
        s.proposed_date = due
        if amount is None:
            s.phase = DialogPhase.NEGOTIATION
            self._plan([Act.ASK_AMOUNT], out)
            return out
        if due is None:
            s.phase = DialogPhase.NEGOTIATION
            self._plan([Act.ASK_DATE], out)
            return out
        s.promise_status = PromiseStatus.PENDING_CONFIRMATION
        s.phase = DialogPhase.CONFIRMATION
        s.unclear_count = 0
        self.audit.record(AuditType.PAYMENT_PROPOSAL_ALLOWED, self.turn_index, amount=amount, due_date=due.isoformat())
        out.state_changes += [
            f"proposed_amount={amount}",
            f"proposed_date={due.isoformat()}",
            "promise_status=PENDING_CONFIRMATION",
        ]
        s.readback_delivered = False
        self._plan([Act.CONFIRM_PROPOSAL], out)
        return out

    def _confirmation(self, interp: Interpretation, out: TurnOutcome, now: object) -> TurnOutcome:
        s = self.state
        prop = interp.first(Action.PROPOSE_PAYMENT)
        if prop:  # correction while a read-back is pending
            return self._proposal(prop, interp, out)
        if interp.has(Action.AFFIRM) and not interp.has(Action.DENY):
            if not s.readback_delivered:
                # The caller said "yes" before hearing the full read-back (e.g. they
                # interrupted it). A "yes" to an unheard question is not consent.
                self._record(self.policy.readback_not_delivered(s, self.clock.now()), out)
                s.unclear_count += 1
                if s.unclear_count >= MAX_UNCLEAR_BEFORE_TRANSFER:
                    return self._transfer(out, self.clock.now(), "confirmation_not_completed", Act.TRANSFER)
                self._plan([Act.CONFIRM_PROPOSAL], out)
                return out
            decisions = self.policy.evaluate_promise_confirmation(s, True, self.today(), self.clock.now())
            for d in decisions:
                self._record(d, out)
            if all(d.allowed for d in decisions):
                assert s.proposed_amount is not None and s.proposed_date is not None
                s.promise = PaymentPromise(
                    promise_id=uuid.uuid4(),
                    amount=s.proposed_amount,
                    currency=s.currency,
                    due_date=s.proposed_date,
                    confirmation_turn=self.turn_index,
                    confirmed_at=self.clock.now(),
                    policy_decision_ids=[d.decision_id for d in decisions],
                )
                s.promise_status = PromiseStatus.CONFIRMED
                s.phase = DialogPhase.CLOSING
                self.audit.record(
                    AuditType.PROMISE_CONFIRMED,
                    self.turn_index,
                    promise_id=str(s.promise.promise_id),
                    amount=s.promise.amount,
                    currency=s.currency,
                    due_date=s.promise.due_date.isoformat(),
                    policy_decision_ids=[str(i) for i in s.promise.policy_decision_ids],
                )
                out.effects.append(Effect.SEND_PTP_CONFIRMATION)
                out.state_changes.append("promise_status=CONFIRMED")
                self._plan([Act.PTP_CONFIRMED], out)
                return out
            # Should be unreachable because proposals are pre-validated; fail closed anyway.
            s.promise_status = PromiseStatus.REJECTED
            s.phase = DialogPhase.NEGOTIATION
            self._plan([Act.PROPOSAL_DECLINED], out)
            return out
        if interp.has(Action.DENY) or interp.has(Action.CANNOT_PAY):
            s.proposed_amount = None
            s.proposed_date = None
            s.promise_status = PromiseStatus.NONE
            s.phase = DialogPhase.NEGOTIATION
            out.state_changes.append("promise_status=NONE")
            self._plan([Act.PROPOSAL_DECLINED] if interp.has(Action.DENY) else [Act.OFFER_TERMS], out)
            return out
        if interp.has(Action.REQUEST_DISCOUNT):
            self._record(
                self.policy.evaluate_discount_request(s, self.account.discount_authority, self.clock.now()), out
            )
            self._plan([Act.NO_DISCOUNT], out)
            return out
        return self._unclear(out, [Act.CONFIRM_PROPOSAL])

    def _closing(self, interp: Interpretation, out: TurnOutcome, now: object) -> TurnOutcome:
        s = self.state
        if interp.has(Action.PROPOSE_PAYMENT) and s.promise:
            d = self.policy.evaluate_promise_confirmation(s, True, self.today(), self.clock.now())
            for x in d:
                self._record(x, out)
            slots = {
                "amount": fmt_money(s.promise.amount, s.currency, self.lang),
                "date": fmt_date(s.promise.due_date, self.lang),
            }
            self._plan([Act.ALREADY_CONFIRMED], out, **slots)
            out.plan.approved_amounts.add(s.promise.amount)
            out.plan.approved_dates.add(s.promise.due_date)
            return out
        self._end(out, "completed_with_promise" if s.promise else "completed")
        self._plan([Act.CLOSING], out)
        return out

    def mark_delivered(self, plan: ResponsePlan, played_fraction: float) -> None:
        """Called by the runtime once an utterance has been played (or cut off)."""
        if self.state.phase != DialogPhase.CONFIRMATION:
            return
        ends_with_readback = bool(plan.acts) and plan.acts[-1] == Act.CONFIRM_PROPOSAL
        self.state.readback_delivered = ends_with_readback and played_fraction >= READBACK_MIN_PLAYED

    def mark_transfer_status(self, status: TransferStatus, detail: str = "") -> None:
        self.state.transfer_status = status
        self.audit.record(AuditType.HUMAN_TRANSFER_STATUS, self.turn_index, status=status.value, detail=detail)
