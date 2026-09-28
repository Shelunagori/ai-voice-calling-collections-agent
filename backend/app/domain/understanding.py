"""Language understanding: transcript -> typed `Interpretation`.

Pipeline per caller turn:
  1. deterministic rules parser (always runs; cheap);
  2. hosted LLM with a JSON schema (only when an LLM provider is configured);
  3. merge: LLM output is schema-validated; invalid output falls back to rules;
     caller-rights intents from rules are always added (fail-safe toward the caller);
     an LLM amount that contradicts an amount the rules parser read verbatim from the
     transcript is replaced by the verbatim one.

The LLM is never shown the debtor's date of birth or account terms: it extracts what
the caller *said*, and application code compares it with the record.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from ..providers.base import LLMProvider, ProviderError
from . import nlu_rules
from .commands import Action, Interpretation, ProposedAction, interpretation_json_schema
from .models import DialogPhase, DobParts, Language
from .turn_context import TurnContext, constrain, context_for

log = logging.getLogger(__name__)

NLU_PROMPT_VERSION = "nlu-v2"

_SYSTEM = """You convert one caller utterance from a phone call into JSON actions.
You do NOT decide anything; you only report what the caller said.
Allowed actions for this step (anything else is discarded): {actions}.
Rules:
- AFFIRM/DENY: caller says yes/no to the agent's last question.
- PROVIDE_DOB: caller states their COMPLETE date of birth (year, month and day) -> dob as YYYY-MM-DD.
- PARTIAL_DOB: caller gives only part of their date of birth -> set only dob_year / dob_month / dob_day
  that the caller actually said. Never fill in a missing part (no default month or day).
- WRONG_PERSON: caller says they are not the named person, or the person is absent.
- PROPOSE_PAYMENT: caller offers an amount and/or date. amount is an integer in yen.
  Use date (YYYY-MM-DD) for explicit dates, days_from_now for relative durations.
- REQUEST_HUMAN: caller wants a person/operator. STOP_CONTACT: caller wants no more calls.
- Never invent amounts or dates the caller did not say. If unsure use UNCLEAR.
Today is {today}. Conversation language: {language}. Current step: {phase}. Expected answer: {slot}.
The agent's last question was: "{last_agent}".
Return only JSON matching the schema."""


def build_system_prompt(ctx: TurnContext, today: date, language: Language, last_agent: str) -> str:
    """The exact system prompt the runtime sends. The NLU benchmark and the post-training data
    pipeline call this too, so a model is always trained and evaluated on what production feeds it."""
    return _SYSTEM.format(
        actions=", ".join(sorted(a.value for a in ctx.allowed)),
        today=today.isoformat(),
        language="Japanese" if language == Language.JA else "English",
        phase=ctx.phase.value,
        slot=ctx.expected_slot.value,
        last_agent=last_agent[:300].replace('"', "'"),
    )


@dataclass
class UnderstandingResult:
    interpretation: Interpretation
    llm_used: bool = False
    llm_error: str | None = None
    llm_latency_ms: float | None = None
    notes: list[str] = field(default_factory=list)
    llm_validation_failed: bool = False  # some or all LLM actions failed schema validation


class Understanding:
    def __init__(self, llm: LLMProvider | None, timeout_s: float, monotonic: Any) -> None:
        self.llm = llm
        self.timeout_s = timeout_s
        self._mono = monotonic

    async def interpret(
        self,
        text: str,
        language: Language,
        today: date,
        phase: DialogPhase | TurnContext,
        last_agent: str,
    ) -> UnderstandingResult:
        ctx = phase if isinstance(phase, TurnContext) else context_for(phase)
        rules = nlu_rules.interpret(text, language, today, context=ctx)
        if self.llm is None or nlu_rules.is_filler_only(text):
            return _result(rules)

        started = self._mono()
        system = build_system_prompt(ctx, today, language, last_agent)
        vnotes: list[str] = []
        try:
            # One overall deadline for the whole call, retries included (latency budget).
            async with asyncio.timeout(self.timeout_s):
                raw = await self.llm.complete_json(
                    system, text[:500], interpretation_json_schema(ctx.allowed), timeout=self.timeout_s
                )
            llm_interp = _validate(raw, vnotes)
        except (ProviderError, ValidationError, ValueError, TypeError, TimeoutError) as e:
            log.warning("llm_nlu_fallback", extra={"error": str(e)[:200], "expected_slot": ctx.expected_slot.value})
            # The fallback is the *same* phase-constrained parser: an LLM failure can never
            # widen what the turn may mean (e.g. a DOB year read as a payment amount).
            invalid = isinstance(e, ValidationError | ValueError | TypeError)
            notes = [*vnotes, "llm_failed_rules_fallback", *(["llm_validation_failed"] if invalid else [])]
            return _result(
                rules,
                notes,
                llm_error=str(e)[:200],
                llm_latency_ms=(self._mono() - started) * 1000,
                llm_validation_failed=invalid,
            )
        latency = (self._mono() - started) * 1000
        merged, notes = merge(llm_interp, rules, ctx)
        failed = "llm_validation_failed" in vnotes
        return _result(merged, [*vnotes, *notes], llm_used=True, llm_latency_ms=latency, llm_validation_failed=failed)


def _result(interp: Interpretation, notes: list[str] | None = None, **kw: Any) -> UnderstandingResult:
    all_notes = list(dict.fromkeys([*interp.notes, *(notes or [])]))
    return UnderstandingResult(interp.model_copy(update={"notes": all_notes[:20]}), notes=all_notes, **kw)


_PARTIAL_ISO = re.compile(r"^\s*(\d{4})(?:-(\d{1,2}))?\s*$")


def _normalise_action(a: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    """A model that writes a partial date into `dob` ("1988-04", "1988") meant PARTIAL_DOB.
    Model it explicitly instead of letting pydantic reject the whole turn."""
    dob = a.get("dob")
    if a.get("action") in ("PROVIDE_DOB", "PARTIAL_DOB") and isinstance(dob, str):
        m = _PARTIAL_ISO.match(dob)
        if m:
            notes.append("llm_partial_dob_normalised")
            out = {k: v for k, v in a.items() if k != "dob"}
            out["action"] = "PARTIAL_DOB"
            out["dob_year"] = int(m[1])
            if m[2]:
                out["dob_month"] = int(m[2])
            return out
    return a


def _validate(raw: Any, notes: list[str] | None = None) -> Interpretation:
    """Validate each proposed action on its own. An invalid action is dropped (and noted);
    only when nothing valid is left does the whole output count as a failure."""
    notes = notes if notes is not None else []
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError("llm output is not an object")
    actions = raw.get("actions", [])
    if not isinstance(actions, list):
        raise ValueError("actions must be a list")
    valid: list[ProposedAction] = []
    errors: list[str] = []
    for a in actions[:3]:
        if not isinstance(a, dict):
            errors.append("action must be an object")
            continue
        cleaned = {k: v for k, v in a.items() if v is not None and k in ProposedAction.model_fields}
        try:
            valid.append(ProposedAction(**_normalise_action(cleaned, notes)))
        except ValidationError as e:
            errors.append(f"{cleaned.get('action')}: {e.errors()[0].get('msg', 'invalid')}"[:160])
    if errors:
        notes.append("llm_validation_failed")
        if not valid:
            raise ValueError("; ".join(errors)[:200])
    return Interpretation(actions=valid, source="llm")


def _ground_dob(llm_action: ProposedAction, rules: Interpretation, notes: list[str]) -> ProposedAction | None:
    """Keep only DOB parts the transcript supports (the rules parser is the evidence)."""
    r_act = rules.first(Action.PROVIDE_DOB) or rules.first(Action.PARTIAL_DOB)
    r = nlu_rules.dob_parts_of(r_act) if r_act else DobParts()
    llm = nlu_rules.dob_parts_of(llm_action)
    out: dict[str, int | None] = {}
    for k in ("year", "month", "day"):
        lv, rv = getattr(llm, k), getattr(r, k)
        if lv is not None and rv is not None and lv != rv:
            notes.append("dob_grounded_to_transcript")
            out[k] = rv
        elif lv is not None and rv is None and r.has_any:
            notes.append("llm_dob_field_not_in_transcript")
            out[k] = None
        elif lv is None and rv is not None:
            out[k] = rv
        else:
            out[k] = lv
    grounded = DobParts(out["year"], out["month"], out["day"])
    if not r.has_any and grounded.month == 1 and grounded.day == 1:
        # Unverifiable "YYYY-01-01" is the classic padding of a year-only answer.
        notes.append("llm_dob_field_not_in_transcript")
        grounded = DobParts(grounded.year, None, None)
    return nlu_rules.dob_action(grounded)


def merge(
    llm: Interpretation, rules: Interpretation, ctx: TurnContext | None = None
) -> tuple[Interpretation, list[str]]:
    notes: list[str] = []
    actions = list(llm.actions) or [ProposedAction(action=Action.UNCLEAR)]
    # caller-rights safety net
    for act in (Action.STOP_CONTACT, Action.REQUEST_HUMAN):
        if rules.has(act) and not any(a.action == act for a in actions):
            actions.insert(0, ProposedAction(action=act))
            notes.append(f"rules_added_{act.value.lower()}")
    # a model "yes" never overrides an explicit "no" the rules parser heard (consent gate)
    if rules.has(Action.DENY) and any(a.action == Action.AFFIRM for a in actions):
        actions = [ProposedAction(action=Action.DENY) if a.action == Action.AFFIRM else a for a in actions]
        notes.append("affirm_overridden_by_rules_deny")
    # grounding: prefer amounts read verbatim from the transcript
    rp = rules.first(Action.PROPOSE_PAYMENT)
    for i, a in enumerate(actions):
        if a.action == Action.PROPOSE_PAYMENT and rp and rp.amount is not None and a.amount != rp.amount:
            actions[i] = a.model_copy(update={"amount": rp.amount})
            notes.append("amount_grounded_to_transcript")
    # grounding: a date of birth never gains parts the caller did not say
    dob_kinds = (Action.PROVIDE_DOB, Action.PARTIAL_DOB)
    grounded: list[ProposedAction] = []
    for a in actions:
        if a.action in dob_kinds:
            g = _ground_dob(a, rules, notes)
            if g is not None:
                grounded.append(g)
        else:
            grounded.append(a)
    actions = grounded
    if ctx is not None and ctx.expects_dob and not any(a.action in dob_kinds for a in actions):
        r_dob = rules.first(Action.PROVIDE_DOB) or rules.first(Action.PARTIAL_DOB)
        if r_dob is not None:
            actions.append(r_dob)
            notes.append("rules_added_dob")
    actions = [a for a in actions if not (a.action == Action.UNCLEAR and len(actions) > 1)]
    actions = actions or [ProposedAction(action=Action.UNCLEAR)]
    merged = Interpretation(actions=actions[:3], source="llm+rules" if notes else "llm", notes=notes)
    if ctx is not None:
        merged = constrain(merged, ctx)
        notes = [n for n in merged.notes]
    return merged, list(dict.fromkeys(notes))


def validate_llm_output(raw: Any, notes: list[str] | None = None) -> Interpretation:
    """Public entry point for `_validate` (benchmark / offline evaluation)."""
    return _validate(raw, notes)
