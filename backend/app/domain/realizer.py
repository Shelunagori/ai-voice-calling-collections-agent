"""Turn a `ResponsePlan` into speakable text.

* `TemplateRealizer` (default): deterministic bilingual templates. Zero LLM latency.
* `LLMRealizer`: asks the LLM to rephrase the *approved* template text naturally,
  then runs the disclosure guard. Any guard violation, timeout or provider error
  falls back to the template, and the fallback is audited.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from ..providers.base import LLMProvider, ProviderError
from .models import Language
from .responses import ResponsePlan, guard, numbers_match_template

REALIZER_PROMPT_VERSION = "realize-v1"

_SYSTEM = (
    "You are the voice of a polite automated assistant on a phone call. Rephrase the message inside "
    "<message> so it sounds natural when spoken, in {language}. Keep every number, amount and date exactly "
    "as written. Do not add facts, offers, threats, discounts or questions that are not in the message. "
    "Keep it short. Output only the rephrased message."
)


@dataclass
class Realization:
    text: str
    source: str  # "template" | "llm" | "template_fallback"
    first_token_at: float | None
    guard_violations: list[str]
    error: str | None = None


class TemplateRealizer:
    def __init__(self, monotonic: Callable[[], float]) -> None:
        self._mono = monotonic

    async def realize(self, plan: ResponsePlan) -> Realization:
        text = plan.render()
        g = guard(text, plan)
        if not g.ok:  # a template bug must never leak; tests assert this never happens
            raise AssertionError(f"template failed guard: {g.violations}: {text}")
        return Realization(text, "template", self._mono(), [])


class LLMRealizer:
    def __init__(self, llm: LLMProvider, monotonic: Callable[[], float], timeout_s: float) -> None:
        self.llm = llm
        self._mono = monotonic
        self.timeout_s = timeout_s

    async def realize(self, plan: ResponsePlan) -> Realization:
        template = plan.render()
        system = _SYSTEM.format(language="Japanese" if plan.language == Language.JA else "English")
        first: float | None = None
        parts: list[str] = []
        try:
            async with asyncio.timeout(self.timeout_s):
                async for tok in self.llm.stream_text(system, f"<message>{template}</message>", timeout=self.timeout_s):
                    if first is None:
                        first = self._mono()
                    parts.append(tok)
        except (ProviderError, TimeoutError) as e:
            return Realization(template, "template_fallback", self._mono(), [], error=str(e)[:200])
        text = "".join(parts).strip()
        g = guard(text, plan)
        violations = list(g.violations)
        if text and not numbers_match_template(text, template):
            violations.append("number_not_in_approved_template")
        if not text or violations:
            return Realization(template, "template_fallback", first, violations or ["empty"])
        return Realization(text, "llm", first, [])
