from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.domain import nlu_rules
from app.domain.audit import AuditType
from app.domain.clock import FakeClock
from app.domain.commands import Action, Interpretation, ProposedAction
from app.domain.controller import Effect
from app.domain.models import CallStatus, IdentityStatus, Language, PromiseStatus
from app.domain.policy import Decision, PolicyEngine, Rule
from app.domain.responses import guard
from app.domain.scenarios import SCENARIOS, get_scenario
from tests.conftest import make_controller, say, verify


def rules_of(out, decision=None):
    return [d.rule for d in out.decisions if decision is None or d.decision == decision]


# ------------------------------------------------------------------ identity / disclosure
def test_disclosure_blocked_before_verification_even_if_llm_asks():
    c, _ = make_controller("A")
    c.start()
    # a hostile/buggy model proposes a payment before identity is verified
    out = c.apply(Interpretation(actions=[ProposedAction(action=Action.ASK_BALANCE)]), "how much do I owe")
    assert Rule.IDENTITY_BEFORE_DISCLOSURE in rules_of(out, Decision.BLOCK)
    text = out.plan.render()
    assert "80,000" not in text and "¥" not in text
    assert guard(text, out.plan).ok
    assert not c.state.debt_disclosed


def test_llm_cannot_set_identity_verified_by_claiming_it():
    c, _ = make_controller("A")
    c.start()
    say(c, "yes")
    wrong = ProposedAction(action=Action.PROVIDE_DOB, dob=date(1990, 1, 1))
    out = c.apply(Interpretation(actions=[wrong], source="llm"), "born 1990")
    assert c.state.identity_status == IdentityStatus.NAME_CONFIRMED
    assert out.plan.primary.value == "DOB_RETRY"


def test_identity_attempt_limit_fails_closed():
    c, _ = make_controller("A")
    c.start()
    say(c, "yes")
    say(c, "January 1, 1990")
    out = say(c, "February 2, 1991")
    assert c.state.identity_status == IdentityStatus.FAILED
    assert Effect.END_CALL in out.effects
    assert not c.state.debt_disclosed
    assert "¥" not in out.plan.render()


def test_wrong_party_ends_without_disclosure():
    c, _ = make_controller("D")
    c.start()
    out = say(c, "No, I'm her husband.")
    assert c.state.identity_status == IdentityStatus.WRONG_PARTY
    assert Rule.WRONG_PARTY_NO_DISCLOSURE in rules_of(out, Decision.BLOCK)
    assert c.state.call_status == CallStatus.COMPLETED
    assert "45,000" not in out.plan.render()
    assert any(e.type == AuditType.DISCLOSURE_BLOCKED for e in c.audit.events)


# ------------------------------------------------------------------ negotiation
def test_extension_beyond_policy_is_rejected_and_never_confirmed():
    c, clk = make_controller("C")
    verify(c)
    out = say(c, "Can I pay 15,000 yen in 60 days?")
    assert Rule.PAYMENT_DATE_WINDOW in rules_of(out, Decision.BLOCK)
    assert c.state.promise_status == PromiseStatus.REJECTED
    assert c.state.proposed_date is None
    out = say(c, "yes")  # "yes" to the counter-offer is not a promise
    assert c.state.promise is None
    assert c.state.promise_status != PromiseStatus.CONFIRMED


def test_amount_below_minimum_rejected():
    c, _ = make_controller("A")
    verify(c)
    out = say(c, "I can pay 5,000 yen tomorrow")
    assert Rule.PAYMENT_MIN_AMOUNT in rules_of(out, Decision.BLOCK)
    assert c.state.proposed_amount is None
    assert c.state.proposed_date is not None  # valid half kept


def test_amount_above_balance_rejected():
    c, _ = make_controller("A")
    verify(c)
    out = say(c, "I will pay 900,000 yen tomorrow")
    assert Rule.PAYMENT_MAX_AMOUNT in rules_of(out, Decision.BLOCK)


def test_past_date_rejected():
    c, _ = make_controller("A")
    verify(c)
    out = c.apply(
        Interpretation(actions=[ProposedAction(action=Action.PROPOSE_PAYMENT, amount=30000, date=date(2020, 1, 1))]),
        "30,000 on Jan 1 2020",
    )
    assert Rule.PAYMENT_DATE_WINDOW in rules_of(out, Decision.BLOCK)


def test_promise_requires_explicit_confirmation():
    c, _ = make_controller("A")
    verify(c)
    say(c, "30,000 yen in two weeks")
    assert c.state.promise_status == PromiseStatus.PENDING_CONFIRMATION
    say(c, "hmm")  # unclear is not confirmation
    assert c.state.promise is None
    out = say(c, "yes that's right")
    assert c.state.promise_status == PromiseStatus.CONFIRMED
    assert Effect.SEND_PTP_CONFIRMATION in out.effects
    p = c.state.promise
    assert p and p.amount == 30000 and p.currency == "JPY" and p.confirmation_turn == c.turn_index
    assert p.policy_decision_ids


def test_no_duplicate_promise():
    c, _ = make_controller("A")
    verify(c)
    say(c, "30,000 yen in two weeks")
    say(c, "yes")
    first = c.state.promise
    out = say(c, "actually 40,000 yen tomorrow")
    assert c.state.promise is first
    assert Rule.PROMISE_SINGLE in rules_of(out, Decision.BLOCK)
    assert len([e for e in c.audit.events if e.type == AuditType.PROMISE_CONFIRMED]) == 1


def test_amount_correction_keeps_date():
    c, _ = make_controller("A")
    verify(c)
    say(c, "30,000 yen in two weeks")
    d = c.state.proposed_date
    say(c, "actually make it 25,000 yen")
    assert c.state.proposed_amount == 25000 and c.state.proposed_date == d
    assert c.state.promise_status == PromiseStatus.PENDING_CONFIRMATION
    say(c, "yes")
    assert c.state.promise and c.state.promise.amount == 25000


def test_date_correction_keeps_amount():
    c, _ = make_controller("A")
    verify(c)
    say(c, "30,000 yen in two weeks")
    say(c, "no wait, tomorrow")
    assert c.state.proposed_amount == 30000
    assert c.state.proposed_date == c.today() + timedelta(days=1)


def test_discount_request_blocked():
    c, _ = make_controller("B")
    verify(c)
    out = say(c, "Can you waive part of it or give me a discount?")
    assert Rule.NO_DISCOUNT_AUTHORITY in rules_of(out, Decision.BLOCK)
    assert guard(out.plan.render(), out.plan).ok


def test_deny_during_confirmation_clears_proposal():
    c, _ = make_controller("A")
    verify(c)
    say(c, "30,000 yen in two weeks")
    say(c, "no")
    assert c.state.proposed_amount is None and c.state.promise_status == PromiseStatus.NONE


def test_hangup_with_pending_proposal_never_creates_promise():
    c, _ = make_controller("A")
    verify(c)
    say(c, "30,000 yen in two weeks")
    c.on_disconnect("caller_hangup")
    assert c.state.promise is None and c.state.promise_status == PromiseStatus.NONE
    assert c.state.call_status == CallStatus.DISCONNECTED
    say(c, "yes")  # late event after hangup
    assert c.state.promise is None


# ------------------------------------------------------------------ caller rights
@pytest.mark.parametrize("lang,text", [(Language.EN, "stop calling me"), (Language.JA, "もう電話しないでください")])
def test_stop_contact_before_or_after_verification(lang, text):
    for verified in (False, True):
        c, _ = make_controller("E", lang)
        if verified:
            verify(c)
        else:
            c.start()
        out = c.apply(nlu_rules.interpret(text, lang, c.today()), text)
        assert c.state.stop_contact and not c.state.future_contact_eligible
        assert Effect.DISABLE_FUTURE_CONTACT in out.effects and Effect.END_CALL in out.effects


def test_human_transfer_is_deterministic_state():
    c, _ = make_controller("G")
    c.start()
    out = say(c, "let me talk to a human")
    assert c.state.human_transfer_requested
    assert c.state.transfer_reason == "caller_requested_human"
    assert c.state.call_status == CallStatus.TRANSFER_REQUESTED
    assert Effect.TRANSFER in out.effects


def test_repeated_misunderstanding_escalates_to_human():
    c, _ = make_controller("A")
    verify(c)
    for _ in range(3):
        out = say(c, "blue banana")
    assert c.state.human_transfer_requested and c.state.transfer_reason == "repeated_misunderstanding"
    assert Effect.TRANSFER in out.effects


def test_dispute_after_verification_transfers():
    c, _ = make_controller("A")
    verify(c)
    say(c, "this is not my debt")
    assert c.state.transfer_reason == "caller_disputes_debt"


# ------------------------------------------------------------------ contact policy
def test_calling_hours_and_attempts_and_stop_contact():
    pe = PolicyEngine()
    acc = get_scenario("A").account
    at_10_jst = datetime(2026, 10, 1, 1, 0, tzinfo=UTC)
    at_23_jst = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)
    assert all(d.allowed for d in pe.evaluate_contact(acc, at_10_jst))
    late = {d.rule: d.decision for d in pe.evaluate_contact(acc, at_23_jst)}
    assert late[Rule.CONTACT_WINDOW] == Decision.BLOCK
    from dataclasses import replace

    many = {d.rule: d.decision for d in pe.evaluate_contact(replace(acc, contact_attempts=3), at_10_jst)}
    assert many[Rule.MAX_CONTACT_ATTEMPTS] == Decision.BLOCK
    stop = {d.rule: d.decision for d in pe.evaluate_contact(replace(acc, stop_contact=True), at_10_jst)}
    assert stop[Rule.STOP_CONTACT_ACTIVE] == Decision.BLOCK


def test_every_policy_decision_is_audited():
    c, _ = make_controller("A")
    verify(c)
    out = say(c, "30,000 yen in two weeks")
    audited = {e.data["decision_id"] for e in c.audit.events if e.type == AuditType.POLICY_DECISION}
    assert {str(d.decision_id) for d in out.decisions} <= audited


# ------------------------------------------------------------------ templates & guard
@pytest.mark.parametrize("key", list(SCENARIOS))
@pytest.mark.parametrize("lang", [Language.EN, Language.JA])
def test_scripted_scenarios_never_violate_guard(key, lang):
    c, _ = make_controller(key, lang)
    out = c.start()
    assert guard(out.plan.render(), out.plan).ok
    for line in get_scenario(key).script(lang):
        if c.state.ended:
            break
        out = say(c, line.replace("[interrupt] ", ""))
        text = out.plan.render()
        g = guard(text, out.plan)
        assert g.ok, (key, lang, text, g.violations)


def test_guard_blocks_unapproved_amount_and_threats():
    c, _ = make_controller("A")
    verify(c)
    out = say(c, "30,000 yen in two weeks")
    assert not guard("You owe ¥99,999 now.", out.plan).ok
    assert not guard("We will take legal action.", out.plan).ok
    assert not guard("我々は訴訟を起こします", out.plan).ok
    assert not guard("Pay ¥30,000 on October 29.", out.plan).ok  # date not approved
    assert guard(out.plan.render(), out.plan).ok


def test_calendar_is_debtor_timezone():
    # 23:30 UTC on Sep 30 is already Oct 1 in Tokyo
    clk = FakeClock(datetime(2026, 9, 30, 23, 30, tzinfo=UTC))
    c, _ = make_controller("A", clock=clk)
    assert c.today() == date(2026, 10, 1)
