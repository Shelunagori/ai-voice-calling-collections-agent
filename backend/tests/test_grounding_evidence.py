"""Merge grounding uses transcript evidence, not the parser's reading (the 'parser is the evidence'
rule discarded correct model readings on split years and spelled amounts)."""

from __future__ import annotations

from datetime import date

from app.domain import nlu_rules
from app.domain.commands import Action, Interpretation, ProposedAction
from app.domain.models import DialogPhase, Language
from app.domain.turn_context import context_for
from app.domain.understanding import merge

A = ProposedAction


def test_number_evidence_covers_every_surface_form() -> None:
    ev = nlu_rules.number_evidence
    assert {4, 12, 19, 88, 1988} <= ev("April 12 19 88")
    assert 60000 in ev("I can pay 60 thousand.")
    assert 30000 in ev("30k by Friday") and 30000 in ev("¥30,000") and 25000 in ev("twenty-five thousand yen")
    assert {1995, 1, 9} <= ev("January 9 nineteen ninety-five")
    assert 12 in ev("the twelfth of April") and 4 in ev("the twelfth of April")
    assert 30000 in ev("3万円なら払えます。") and 15000 in ev("一万五千円")
    assert (
        1971 in ev("昭和46年3月28日です。") and 3 in ev("昭和46年3月28日です。") and 28 in ev("昭和46年3月28日です。")
    )
    assert 1989 in ev("平成元年です。")
    assert ev("Yes, that's me.") == set()


def test_split_year_model_reading_survives_the_merge() -> None:
    """Real-call finding 84f807f3: parser reads '19 88' as day=19; the model's year is in the text."""
    ctx = context_for(DialogPhase.IDENTITY_DOB)
    text = "April 12 19 88"
    rules = nlu_rules.interpret(text, Language.EN, date(2026, 10, 1), context=ctx)
    r = rules.first(Action.PARTIAL_DOB)
    assert r is not None and r.dob_year is None  # the parser cannot read the split year at all
    llm = Interpretation(actions=[A(action=Action.PROVIDE_DOB, dob=date(1988, 4, 12))], source="llm")
    merged, notes = merge(llm, rules, ctx, text)
    assert merged.actions[0].action == Action.PROVIDE_DOB and merged.actions[0].dob == date(1988, 4, 12)
    assert "llm_dob_field_not_in_transcript" not in notes  # the year was in the text ("19 88")


def test_spelled_amount_model_reading_survives_the_merge() -> None:
    ctx = context_for(DialogPhase.NEGOTIATION)
    text = "I can pay 60 thousand."
    rules = nlu_rules.interpret(text, Language.EN, date(2026, 10, 1), context=ctx)
    llm = Interpretation(actions=[A(action=Action.PROPOSE_PAYMENT, amount=60000)], source="llm")
    merged, notes = merge(llm, rules, ctx, text)
    assert merged.first(Action.PROPOSE_PAYMENT).amount == 60000  # type: ignore[union-attr]
    assert "amount_kept_llm_over_rules" in notes


def test_numbers_not_in_the_transcript_are_still_never_invented() -> None:
    """Scope guard: evidence grounding only *keeps* what was said; it never adds."""
    ctx = context_for(DialogPhase.IDENTITY_DOB)
    # model hallucinates 1990 for a caller who said 1988 -> parser value wins
    text = "April 1988"
    rules = nlu_rules.interpret(text, Language.EN, date(2026, 10, 1), context=ctx)
    llm = Interpretation(actions=[A(action=Action.PARTIAL_DOB, dob_year=1990, dob_month=4)], source="llm")
    merged, notes = merge(llm, rules, ctx, text)
    a = merged.first(Action.PARTIAL_DOB)
    assert a is not None and a.dob_year == 1988 and a.dob_month == 4 and "dob_grounded_to_transcript" in notes
    # model pads a bare year to YYYY-01-01 -> the padding is dropped
    text = "1988"
    rules = nlu_rules.interpret(text, Language.EN, date(2026, 10, 1), context=ctx)
    llm = Interpretation(actions=[A(action=Action.PROVIDE_DOB, dob=date(1988, 1, 1))], source="llm")
    merged, notes = merge(llm, rules, ctx, text)
    a = merged.first(Action.PARTIAL_DOB)
    assert a is not None and a.dob_year == 1988 and a.dob_month is None and a.dob_day is None
    # model invents an amount the caller never said -> parser amount wins
    ctx = context_for(DialogPhase.NEGOTIATION)
    text = "I can pay 30,000 yen."
    rules = nlu_rules.interpret(text, Language.EN, date(2026, 10, 1), context=ctx)
    llm = Interpretation(actions=[A(action=Action.PROPOSE_PAYMENT, amount=3000)], source="llm")
    merged, notes = merge(llm, rules, ctx, text)
    assert merged.first(Action.PROPOSE_PAYMENT).amount == 30000 and "amount_grounded_to_transcript" in notes  # type: ignore[union-attr]


def test_without_transcript_the_parser_remains_the_only_evidence() -> None:
    """Offline callers that pass no text keep the old behaviour (parser wins on conflict)."""
    ctx = context_for(DialogPhase.IDENTITY_DOB)
    rules = Interpretation(actions=[A(action=Action.PARTIAL_DOB, dob_month=4, dob_day=19)], source="rules")
    llm = Interpretation(actions=[A(action=Action.PROVIDE_DOB, dob=date(1988, 4, 12))], source="llm")
    merged, notes = merge(llm, rules, ctx)
    a = merged.actions[0]
    assert a.action == Action.PARTIAL_DOB and a.dob_day == 19 and a.dob_year is None


def test_parser_day_that_is_really_the_split_year_token_is_not_added() -> None:
    """70B runtime after the first evidence pass: 'March 19 71' became 1971-03-19 because the
    parser's day=19 was appended to the model's (month, year). One token is not two facts."""
    ctx = context_for(DialogPhase.IDENTITY_DOB)
    text = "March 19 71"
    rules = nlu_rules.interpret(text, Language.EN, date(2026, 10, 1), context=ctx)
    llm = Interpretation(actions=[A(action=Action.PARTIAL_DOB, dob_month=3, dob_year=1971)], source="llm")
    merged, notes = merge(llm, rules, ctx, text)
    a = merged.actions[0]
    assert a.action == Action.PARTIAL_DOB and a.dob_year == 1971 and a.dob_month == 3 and a.dob_day is None
    assert "rules_day_is_split_year_token" in notes
    # but a real day next to a split year is still kept: "March 28 19 71" -> full date
    text = "March 28 19 71"
    rules = nlu_rules.interpret(text, Language.EN, date(2026, 10, 1), context=ctx)
    llm = Interpretation(actions=[A(action=Action.PROVIDE_DOB, dob=date(1971, 3, 28))], source="llm")
    merged, _ = merge(llm, rules, ctx, text)
    assert merged.actions[0].dob == date(1971, 3, 28)
