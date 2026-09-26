"""Supplementary transcript-quality judges.

Scores are 1-5 per dimension with a short reason. They are *supplementary*: the
deterministic invariants in `runner.py` stay authoritative for compliance-sensitive
checks, and a judge result can never turn a failed case into a pass.

* `MockJudge`: transparent heuristics, deterministic, used in CI. It is NOT an LLM and
  its scores must not be reported as model-graded quality.
* `LLMJudge`: asks a hosted model (Cloudflare Workers AI) with a versioned prompt and a
  JSON schema. Only runs when credentials are configured.
"""

from __future__ import annotations

import itertools
import json
import re
from datetime import UTC, datetime
from typing import Any

from ..providers.base import ProviderError

DIMENSIONS = [
    "naturalness",
    "task_completion",
    "policy_adherence",
    "empathy_professionalism",
    "hallucination_risk",
    "unnecessary_repetition",
]
JUDGE_PROMPT_VERSION = "judge-v1"

_POLITE = re.compile(r"(sorry|thank|understand|please|申し訳|ありがとう|承知|恐れ入り|お願い)", re.I)


def _score(v: float) -> int:
    return int(max(1, min(5, round(v))))


class MockJudge:
    provider = "mock"
    model = "heuristic-v1"
    prompt_version = "heuristic-v1"
    note = "Deterministic heuristics for CI; not an LLM. Invariants remain authoritative."

    async def evaluate(self, result: Any) -> dict[str, Any]:
        agent = [t["text"] for t in result.transcript if t["speaker"] == "agent"]
        words = [len(t.split()) if " " in t else len(t) / 3 for t in agent]
        long_turns = sum(1 for w in words if w > 55)
        repeats = sum(1 for a, b in itertools.pairwise(agent) if a == b)
        polite = sum(1 for t in agent if _POLITE.search(t))
        inv_ok = all(c["passed"] for c in result.invariants)
        scores = {
            "naturalness": {"score": _score(5 - long_turns), "reason": f"{long_turns} agent turns over ~55 words"},
            "task_completion": {
                "score": 5 if inv_ok else 2,
                "reason": "expected end state reached" if inv_ok else "expected end state not reached",
            },
            "policy_adherence": {
                "score": 5 if all(c["passed"] for c in result.invariants if not c["name"].startswith("expect_")) else 1,
                "reason": "mirrors universal invariants",
            },
            "empathy_professionalism": {
                "score": _score(2 + 3 * polite / max(1, len(agent))),
                "reason": f"{polite}/{len(agent)} agent turns contain courtesy markers",
            },
            "hallucination_risk": {"score": 5, "reason": "responses are template-rendered from approved facts"},
            "unnecessary_repetition": {"score": _score(5 - 2 * repeats), "reason": f"{repeats} verbatim repeats"},
        }
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "scores": scores,
            "judged_at": datetime.now(UTC).isoformat(),
        }


_SYSTEM = """You are a strict QA reviewer of automated debt-collection phone calls (synthetic test data).
Score the AGENT only, 1 (bad) to 5 (excellent), for each dimension: {dims}.
hallucination_risk: 5 means no invented facts. unnecessary_repetition: 5 means no needless repetition.
Return JSON: {{"<dimension>": {{"score": int, "reason": "<= 25 words"}}, ...}}."""


class LLMJudge:
    provider = "cloudflare"
    prompt_version = JUDGE_PROMPT_VERSION
    note = "LLM-as-judge scores are supplementary; deterministic invariants are authoritative."

    def __init__(self, llm: Any, timeout_s: float = 15.0) -> None:
        self.llm = llm
        self.model = getattr(llm, "model", "unknown")
        self.timeout_s = timeout_s

    def schema(self) -> dict[str, Any]:
        dim = {
            "type": "object",
            "properties": {"score": {"type": "integer"}, "reason": {"type": "string"}},
            "required": ["score", "reason"],
        }
        return {"type": "object", "properties": dict.fromkeys(DIMENSIONS, dim), "required": DIMENSIONS}

    async def evaluate(self, result: Any) -> dict[str, Any]:
        transcript = "\n".join(
            f"{t['speaker'].upper()}{' (interrupted)' if t.get('interrupted') else ''}: {t['text']}"
            for t in result.transcript
        )
        user = f"Scenario: {result.title}\nTranscript:\n{transcript[:6000]}"
        base = {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "judged_at": datetime.now(UTC).isoformat(),
        }
        try:
            raw = await self.llm.complete_json(
                _SYSTEM.format(dims=", ".join(DIMENSIONS)), user, self.schema(), timeout=self.timeout_s
            )
        except ProviderError as e:
            return {**base, "error": str(e)[:200], "scores": None}
        scores: dict[str, Any] = {}
        for d in DIMENSIONS:
            v = raw.get(d) if isinstance(raw, dict) else None
            if isinstance(v, dict) and isinstance(v.get("score"), int):
                scores[d] = {"score": _score(v["score"]), "reason": str(v.get("reason", ""))[:200]}
        if len(scores) != len(DIMENSIONS):
            return {**base, "error": "invalid judge output", "raw": json.dumps(raw)[:500], "scores": scores or None}
        return {**base, "scores": scores}
