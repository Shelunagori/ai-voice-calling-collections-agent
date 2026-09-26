"""Regression tests for the real PSTN call 2fa211b9 (2026-09-26).

What happened: while the controller was waiting for a date of birth, the caller said
"Whatever. April. 1988". The LLM returned dob="1988-04", pydantic rejected the whole
output, the unconstrained rules fallback read "1988" as a ¥1,988 payment proposal, and
the agent repeated the generic DOB question. On "you 1988." the LLM invented
dob=1988-01-01, which cost the caller a verification attempt.

Invariants tested here:
* the allowed-action contract comes from the controller phase and is enforced in code
  (LLM schema, rules parser, validation layer, controller) — not only in the prompt;
* partial DOBs are modelled explicitly (year / month / day), never padded to a date;
* the agent asks only for the missing parts;
* payment parsing is unaffected once the caller is verified.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.domain.audit import AuditType
from app.domain.commands import Action, Interpretation, ProposedAction, interpretation_json_schema
from app.domain.models import DialogPhase, IdentityStatus, Language
from app.domain.nlu_rules import interpret, parse_dob
from app.domain.responses import Act, guard
from app.domain.turn_context import ExpectedSlot, context_for
from app.domain.understanding import Understanding
from tests.conftest import make_controller, say, verify

T = date(2026, 10, 1)
DOB_CTX = context_for(DialogPhase.IDENTITY_DOB)
PAY_CTX = context_for(DialogPhase.NEGOTIATION)


class ScriptedLLM:
    """Returns canned JSON, records the schema and prompt it was given."""

    name = "scripted"
    model = "scripted"

    def __init__(self, *outputs: Any) -> None:
        self.outputs = list(outputs)
        self.schemas: list[dict[str, Any]] = []
        self.systems: list[str] = []

    async def complete_json(self, system: str, user: str, schema: dict[str, Any], *, timeout: float) -> Any:
        self.schemas.append(schema)
        self.systems.append(system)
        return self.outputs.pop(0)

    async def stream_text(self, system: str, user: str, *, timeout: float):  # pragma: no cover
        yield ""


def dob_phase_controller():
    c, clk = make_controller("A")
    c.start()
    say(c, "yes")
    assert c.state.phase == DialogPhase.IDENTITY_DOB
    return c


def audit_types(c) -> list[str]:
    return [e.type.value for e in c.audit.events]


# ------------------------------------------------------------------ allowed-action contract
def test_contract_by_phase():
    assert DOB_CTX.expected_slot == ExpectedSlot.DATE_OF_BIRTH
    for a in (Action.PROVIDE_DOB, Action.PARTIAL_DOB, Action.UNCLEAR, Action.STOP_CONTACT, Action.REQUEST_HUMAN):
        assert DOB_CTX.allows(a)
    for a in (Action.PROPOSE_PAYMENT, Action.AFFIRM, Action.REQUEST_DISCOUNT):
        assert not DOB_CTX.allows(a)
    assert PAY_CTX.allows(Action.PROPOSE_PAYMENT)
    assert not PAY_CTX.allows(Action.PROVIDE_DOB) and not PAY_CTX.allows(Action.PARTIAL_DOB)
    # caller rights are valid in every phase
    for phase in DialogPhase:
        ctx = context_for(phase)
        assert ctx.allows(Action.STOP_CONTACT) and ctx.allows(Action.REQUEST_HUMAN)


def test_llm_schema_enum_is_restricted_to_the_phase():
    enum = interpretation_json_schema(DOB_CTX.allowed)["properties"]["actions"]["items"]["properties"]["action"]["enum"]
    assert "PROPOSE_PAYMENT" not in enum and "PARTIAL_DOB" in enum


# ------------------------------------------------------------------ rules parser in the DOB phase
@pytest.mark.parametrize(
    "text,year,month,day",
    [
        ("1988", 1988, None, None),
        ("you 1988.", 1988, None, None),
        ("April 1988", 1988, 4, None),
        ("- Whatever. April. 1988", 1988, 4, None),
        ("April", None, 4, None),
        ("the 12th", None, None, 12),
        ("April 12", None, 4, 12),
        ("nineteen eighty-eight", 1988, None, None),
        ("1988年", 1988, None, None),
        ("1988年4月", 1988, 4, None),
        ("昭和63年4月", 1988, 4, None),
    ],
)
def test_partial_dob_is_parsed_without_inventing_fields(text, year, month, day):
    lang = Language.JA if any(ord(ch) > 0x3000 for ch in text) else Language.EN
    interp = interpret(text, lang, T, context=DOB_CTX)
    assert not interp.has(Action.PROPOSE_PAYMENT), interp
    assert not interp.has(Action.PROVIDE_DOB), interp  # never padded to e.g. 1988-01-01
    p = interp.first(Action.PARTIAL_DOB)
    assert p is not None, interp
    assert (p.dob_year, p.dob_month, p.dob_day) == (year, month, day)


@pytest.mark.parametrize(
    "text,lang",
    [
        ("April 12 1988", Language.EN),
        ("April 12, 1988", Language.EN),
        ("12th April 1988", Language.EN),
        ("the twelfth of April 1988", Language.EN),
        ("1988-04-12", Language.EN),
        ("1988年4月12日", Language.JA),
        ("昭和63年4月12日です", Language.JA),
    ],
)
def test_complete_dob(text, lang):
    interp = interpret(text, lang, T, context=DOB_CTX)
    assert interp.first(Action.PROVIDE_DOB).dob == date(1988, 4, 12)
    assert not interp.has(Action.PROPOSE_PAYMENT)


def test_money_words_in_dob_phase_are_not_payment():
    for text in ("1988", "I can pay 20,000 yen", "20000"):
        assert not interpret(text, Language.EN, T, context=DOB_CTX).has(Action.PROPOSE_PAYMENT)


def test_caller_rights_still_detected_in_dob_phase():
    assert interpret("stop calling me, 1988", Language.EN, T, context=DOB_CTX).has(Action.STOP_CONTACT)
    assert interpret("let me talk to a real person", Language.EN, T, context=DOB_CTX).has(Action.REQUEST_HUMAN)


def test_parse_dob_invalid_day_is_reported_not_coerced():
    p = parse_dob("April 31 1988", Language.EN, T)
    assert (p.year, p.month, p.day) == (1988, 4, 31) and p.complete and p.as_date() is None


# ------------------------------------------------------------------ payment phase unaffected
@pytest.mark.parametrize(
    "text,amount,due",
    [
        ("I can pay 20,000 yen", 20000, None),
        ("I can pay 50,000", 50000, None),
        ("20,000 yen", 20000, None),
        ("1988 yen", 1988, None),
        ("October 27", None, date(2026, 10, 27)),
    ],
)
def test_payment_phase_parsing(text, amount, due):
    p = interpret(text, Language.EN, T, context=PAY_CTX).first(Action.PROPOSE_PAYMENT)
    assert p is not None and (p.amount, p.date) == (amount, due)


def test_payment_phase_never_produces_dob_actions():
    interp = interpret("April 12, 1988", Language.EN, T, context=PAY_CTX)
    assert not interp.has(Action.PROVIDE_DOB) and not interp.has(Action.PARTIAL_DOB)


# ------------------------------------------------------------------ LLM validation + grounding
async def test_malformed_llm_partial_dob_is_normalised_not_payment():
    llm = ScriptedLLM({"actions": [{"action": "PROVIDE_DOB", "dob": "1988-04"}]})
    u = Understanding(llm, 1.0, lambda: 0.0)
    r = await u.interpret("- Whatever. April. 1988", Language.EN, T, DialogPhase.IDENTITY_DOB, "")
    assert not r.interpretation.has(Action.PROPOSE_PAYMENT)
    p = r.interpretation.first(Action.PARTIAL_DOB)
    assert (p.dob_year, p.dob_month, p.dob_day) == (1988, 4, None)
    assert "llm_partial_dob_normalised" in r.notes
    assert "PROPOSE_PAYMENT" not in str(llm.schemas[0]) and "date_of_birth" in llm.systems[0]


async def test_invalid_llm_output_rules_fallback_is_still_constrained():
    llm = ScriptedLLM({"actions": [{"action": "PROVIDE_DOB", "dob": "sometime in 88"}]})
    u = Understanding(llm, 1.0, lambda: 0.0)
    r = await u.interpret("April 1988.", Language.EN, T, DialogPhase.IDENTITY_DOB, "")
    assert r.llm_validation_failed
    assert not r.interpretation.has(Action.PROPOSE_PAYMENT)
    assert r.interpretation.first(Action.PARTIAL_DOB).dob_month == 4


async def test_llm_payment_proposal_in_dob_phase_is_dropped():
    llm = ScriptedLLM({"actions": [{"action": "PROPOSE_PAYMENT", "amount": 1988}]})
    u = Understanding(llm, 1.0, lambda: 0.0)
    r = await u.interpret("1988", Language.EN, T, DialogPhase.IDENTITY_DOB, "")
    assert not r.interpretation.has(Action.PROPOSE_PAYMENT)
    assert r.interpretation.first(Action.PARTIAL_DOB).dob_year == 1988
    assert any(n.startswith("out_of_phase:PROPOSE_PAYMENT") for n in r.notes)


async def test_llm_cannot_invent_missing_dob_fields():
    # real call: "you 1988." -> LLM dob=1988-01-01
    llm = ScriptedLLM({"actions": [{"action": "PROVIDE_DOB", "dob": "1988-01-01"}]})
    u = Understanding(llm, 1.0, lambda: 0.0)
    r = await u.interpret("you 1988.", Language.EN, T, DialogPhase.IDENTITY_DOB, "")
    assert not r.interpretation.has(Action.PROVIDE_DOB)
    p = r.interpretation.first(Action.PARTIAL_DOB)
    assert (p.dob_year, p.dob_month, p.dob_day) == (1988, None, None)
    assert "llm_dob_field_not_in_transcript" in r.notes


async def test_llm_complete_dob_matching_transcript_is_kept():
    llm = ScriptedLLM({"actions": [{"action": "PROVIDE_DOB", "dob": "1988-04-12"}]})
    u = Understanding(llm, 1.0, lambda: 0.0)
    r = await u.interpret("12th April 1988", Language.EN, T, DialogPhase.IDENTITY_DOB, "")
    assert r.interpretation.first(Action.PROVIDE_DOB).dob == date(1988, 4, 12)


# ------------------------------------------------------------------ controller
def test_partial_dob_asks_only_for_missing_day_and_never_proposes_payment():
    c = dob_phase_controller()
    out = say(c, "April 1988")
    assert out.plan.primary == Act.ASK_DOB_PART
    text = out.plan.render()
    assert "April" in text and "day" in text.lower() and "date of birth" not in text.lower()
    assert guard(text, out.plan).ok  # no digits spoken before verification
    assert c.state.identity_status == IdentityStatus.NAME_CONFIRMED and c.state.identity_attempts == 0
    ev = [e for e in c.audit.events if e.type == AuditType.IDENTITY_PARTIAL_DOB][-1]
    assert ev.data["year"] == 1988 and ev.data["month"] == 4 and ev.data["missing"] == ["day"]
    assert ev.data["source"] == "rules" and ev.data["llm_validation_failed"] is False
    assert "payment.proposed" not in audit_types(c)


def test_year_only_asks_for_month_and_day():
    c = dob_phase_controller()
    out = say(c, "1988")
    assert out.plan.primary == Act.ASK_DOB_PART
    assert "month and day" in out.plan.render()


def test_partial_dob_is_completed_by_the_follow_up_answer():
    c = dob_phase_controller()
    say(c, "April 1988")
    out = say(c, "the 12th")
    assert c.state.identity_status == IdentityStatus.VERIFIED
    assert out.plan.primary == Act.DISCLOSE


def test_wrong_complete_dob_fails_and_counts_attempt():
    c = dob_phase_controller()
    say(c, "March 3 1991")
    assert c.state.identity_attempts == 1
    assert "identity.failed" in audit_types(c) and "identity.verified" not in audit_types(c)


def test_invalid_complete_dob_is_not_an_attempt():
    c = dob_phase_controller()
    out = say(c, "April 31 1988")
    assert out.plan.primary == Act.DOB_INVALID
    assert c.state.identity_attempts == 0 and "identity.invalid_dob" in audit_types(c)


def test_correct_dob_verifies_and_allows_disclosure():
    c = dob_phase_controller()
    say(c, "12th April 1988")
    t = audit_types(c)
    assert "identity.verified" in t and "disclosure.allowed" in t


def test_controller_rejects_out_of_phase_action_even_without_understanding_layer():
    c = dob_phase_controller()
    out = c.apply(Interpretation(actions=[ProposedAction(action=Action.PROPOSE_PAYMENT, amount=1988)]), "1988")
    assert "payment.proposed" not in audit_types(c)
    assert "nlu.action_out_of_phase" in audit_types(c)
    assert out.plan.acts == [Act.CLARIFY, Act.ASK_DOB]  # re-asks for the DOB, never discloses
    assert not c.state.debt_disclosed


def test_payment_still_works_after_verification():
    c, _ = make_controller("A")
    verify(c)
    out = say(c, "I can pay 30,000 yen on October 15")
    assert c.state.proposed_amount == 30000 and out.plan.primary == Act.CONFIRM_PROPOSAL


# ------------------------------------------------------------------ real-call scenario
def test_real_pstn_call_script():
    c, _ = make_controller("A")
    c.start()
    say(c, "Yes")
    assert c.state.identity_status == IdentityStatus.NAME_CONFIRMED
    out = say(c, "April 1988")
    assert out.plan.primary == Act.ASK_DOB_PART and "April" in out.plan.render()
    out = say(c, "12th April 1988")
    assert c.state.identity_status == IdentityStatus.VERIFIED and out.plan.primary == Act.DISCLOSE
    t = audit_types(c)
    assert t.index("identity.name_confirmed") < t.index("identity.partial_dob") < t.index("identity.verified")
    assert t.index("identity.verified") < t.index("disclosure.allowed")
    assert "payment.proposed" not in t and c.state.identity_attempts == 0


# ------------------------------------------------------------------ full runtime (eval harness)
@pytest.mark.parametrize("key", ["pstn_partial_dob", "pstn_partial_dob_llm_malformed"])
async def test_real_call_regression_through_voice_runtime(key):
    from app.evaluation.cases import CASES_BY_KEY
    from app.evaluation.runner import run_case

    result, sess = await run_case(CASES_BY_KEY[key])
    assert result.passed, result
    types = [e.type.value for e in sess.c.audit.events]
    assert "payment.proposed" not in types
    lc = next(e for e in sess.c.audit.events if e.type == AuditType.VOICE_LIFECYCLE)
    assert lc.data["transitions"][0]["t_ms"] == 0 and lc.data["transitions"][-1]["to"] == "ENDED"
    partial = next(e for e in sess.c.audit.events if e.type == AuditType.IDENTITY_PARTIAL_DOB)
    assert partial.data["missing"] == ["day"]
    if key.endswith("malformed"):
        assert partial.data["source"] == "llm" and "llm_partial_dob_normalised" in partial.data["nlu_notes"]
