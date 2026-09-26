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

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from ..providers.base import LLMProvider, ProviderError
from . import nlu_rules
from .commands import Action, Interpretation, ProposedAction, interpretation_json_schema
from .models import DialogPhase, Language

log = logging.getLogger(__name__)

NLU_PROMPT_VERSION = "nlu-v1"

_SYSTEM = """You convert one caller utterance from a phone call into JSON actions.
You do NOT decide anything; you only report what the caller said.
Allowed actions: {actions}.
Rules:
- AFFIRM/DENY: caller says yes/no to the agent's last question.
- PROVIDE_DOB: caller states their date of birth -> dob as YYYY-MM-DD.
- WRONG_PERSON: caller says they are not the named person, or the person is absent.
- PROPOSE_PAYMENT: caller offers an amount and/or date. amount is an integer in yen.
  Use date (YYYY-MM-DD) for explicit dates, days_from_now for relative durations.
- REQUEST_HUMAN: caller wants a person/operator. STOP_CONTACT: caller wants no more calls.
- Never invent amounts or dates the caller did not say. If unsure use UNCLEAR.
Today is {today}. Conversation language: {language}. Current step: {phase}.
The agent's last question was: "{last_agent}".
Return only JSON matching the schema."""


@dataclass
class UnderstandingResult:
    interpretation: Interpretation
    llm_used: bool = False
    llm_error: str | None = None
    llm_latency_ms: float | None = None
    notes: list[str] = field(default_factory=list)


class Understanding:
    def __init__(self, llm: LLMProvider | None, timeout_s: float, monotonic: Any) -> None:
        self.llm = llm
        self.timeout_s = timeout_s
        self._mono = monotonic

    async def interpret(
        self, text: str, language: Language, today: date, phase: DialogPhase, last_agent: str
    ) -> UnderstandingResult:
        expecting_dob = phase == DialogPhase.IDENTITY_DOB
        rules = nlu_rules.interpret(text, language, today, expecting_dob=expecting_dob)
        if self.llm is None or nlu_rules.is_filler_only(text):
            return UnderstandingResult(rules)

        started = self._mono()
        system = _SYSTEM.format(
            actions=", ".join(a.value for a in Action),
            today=today.isoformat(),
            language="Japanese" if language == Language.JA else "English",
            phase=phase.value,
            last_agent=last_agent[:300].replace('"', "'"),
        )
        try:
            raw = await self.llm.complete_json(system, text[:500], interpretation_json_schema(), timeout=self.timeout_s)
            llm_interp = _validate(raw)
        except (ProviderError, ValidationError, ValueError, TypeError) as e:
            log.warning("llm_nlu_fallback", extra={"error": str(e)[:200]})
            return UnderstandingResult(
                rules,
                llm_used=False,
                llm_error=str(e)[:200],
                llm_latency_ms=(self._mono() - started) * 1000,
                notes=["llm_failed_rules_fallback"],
            )
        latency = (self._mono() - started) * 1000
        merged, notes = merge(llm_interp, rules)
        return UnderstandingResult(merged, llm_used=True, llm_latency_ms=latency, notes=notes)


def _validate(raw: Any) -> Interpretation:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError("llm output is not an object")
    actions = raw.get("actions", [])
    if not isinstance(actions, list):
        raise ValueError("actions must be a list")
    cleaned = []
    for a in actions[:3]:
        if not isinstance(a, dict):
            raise ValueError("action must be an object")
        cleaned.append({k: v for k, v in a.items() if v is not None and k in ProposedAction.model_fields})
    return Interpretation(actions=[ProposedAction(**a) for a in cleaned], source="llm")


def merge(llm: Interpretation, rules: Interpretation) -> tuple[Interpretation, list[str]]:
    notes: list[str] = []
    actions = list(llm.actions) or [ProposedAction(action=Action.UNCLEAR)]
    # caller-rights safety net
    for act in (Action.STOP_CONTACT, Action.REQUEST_HUMAN):
        if rules.has(act) and not any(a.action == act for a in actions):
            actions.insert(0, ProposedAction(action=act))
            notes.append(f"rules_added_{act.value.lower()}")
    # grounding: prefer amounts read verbatim from the transcript
    rp = rules.first(Action.PROPOSE_PAYMENT)
    for i, a in enumerate(actions):
        if a.action == Action.PROPOSE_PAYMENT and rp and rp.amount is not None and a.amount != rp.amount:
            actions[i] = a.model_copy(update={"amount": rp.amount})
            notes.append("amount_grounded_to_transcript")
    actions = [a for a in actions if not (a.action == Action.UNCLEAR and len(actions) > 1)]
    return Interpretation(actions=actions[:3], source="llm+rules" if notes else "llm"), notes
