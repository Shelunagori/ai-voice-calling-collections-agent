"""NLU benchmark: scorer, summary, prompt parity with the runtime, and an end-to-end run."""

from __future__ import annotations

import time
from datetime import date
from typing import Any, ClassVar

import pytest

from app.domain.models import DialogPhase, Language
from app.domain.turn_context import context_for
from app.domain.understanding import Understanding, build_system_prompt
from app.evaluation import nlu_bench as nb
from app.providers.mock import MockLLM

G_PAY = [{"action": "PROPOSE_PAYMENT", "amount": 30000, "days_from_now": 14}]
G_STOP = [{"action": "STOP_CONTACT"}, {"action": "DISPUTE"}]


def test_score_exact_and_action_only() -> None:
    s = nb.score(G_PAY, [{"action": "PROPOSE_PAYMENT", "amount": 30000, "days_from_now": 14}])
    assert s == {"valid": True, "action_match": True, "exact_match": True, "slot_match": True, "rights_recall": None}
    s = nb.score(G_PAY, [{"action": "PROPOSE_PAYMENT", "amount": 3000, "days_from_now": 14}])
    assert s["action_match"] and not s["exact_match"] and s["slot_match"] is False
    s = nb.score(G_PAY, [{"action": "PROPOSE_PAYMENT", "amount": 30000, "date": "2026-10-15"}])
    assert s["action_match"] and s["slot_match"] is False  # days vs date is not the same reading


def test_score_order_insensitive_and_extra_action() -> None:
    assert nb.score(G_STOP, [{"action": "DISPUTE"}, {"action": "STOP_CONTACT"}])["exact_match"]
    s = nb.score(G_STOP, [{"action": "STOP_CONTACT"}])
    assert not s["action_match"] and s["rights_recall"] is True
    s = nb.score(G_STOP, [{"action": "DISPUTE"}])
    assert s["rights_recall"] is False


def test_score_failed_provider() -> None:
    s = nb.score(G_STOP, None)
    assert s == {
        "valid": False,
        "action_match": False,
        "exact_match": False,
        "slot_match": None,
        "rights_recall": False,
    }
    s = nb.score(G_PAY, None)
    assert s["slot_match"] is False and s["rights_recall"] is None


def test_summarise_rates_and_gate() -> None:
    rows: list[dict[str, Any]] = [
        {"category": "a", "language": "en", "latency_ms": 10, **nb.score(G_PAY, G_PAY)},
        {"category": "a", "language": "en", "latency_ms": 30, **nb.score(G_PAY, None)},
        {"category": "b", "language": "ja", "latency_ms": 20, **nb.score(G_STOP, [{"action": "DISPUTE"}])},
    ]
    s = nb.summarise(rows)
    assert (
        s["n"] == 3
        and s["valid"] == pytest.approx(2 / 3, abs=1e-3)
        and s["exact_match"] == pytest.approx(1 / 3, abs=1e-3)
    )
    assert s["rights_recall"] == 0.0 and s["gate_rights_recall_ok"] is False
    assert s["latency_p50_ms"] == 20 and s["by_category"]["a"]["n"] == 2 and s["by_language"]["ja"]["n"] == 1
    rows[2] = {"category": "b", "language": "ja", "latency_ms": 20, **nb.score(G_STOP, G_STOP)}
    assert nb.summarise(rows)["gate_rights_recall_ok"] is True
    assert nb.summarise([])["gate_rights_recall_ok"] is True  # nothing to gate


def test_markdown_has_label_and_rows() -> None:
    md = nb.markdown(
        nb.summarise([{"category": "a", "language": "en", "latency_ms": 1, **nb.score(G_PAY, G_PAY)}]), "L"
    )
    assert md.startswith("### L") and "| a | 1 | 100.0% | 100.0% | 100.0% |" in md


def test_prompt_builder_matches_runtime_prompt() -> None:
    """The bench and the trainer must feed the model exactly what production feeds it."""
    ctx = context_for(DialogPhase.IDENTITY_DOB)
    system = build_system_prompt(ctx, date(2026, 10, 1), Language.JA, 'Say "your" date of birth')
    assert "Current step: IDENTITY_DOB" in system and "Expected answer: date_of_birth" in system
    assert "PARTIAL_DOB" in system and "PROPOSE_PAYMENT" not in system.split("Allowed actions")[1].split("\n")[0]
    assert "Today is 2026-10-01" in system and "Conversation language: Japanese" in system
    assert "Say 'your' date of birth" in system  # quotes are neutralised

    class Capture(MockLLM):
        systems: ClassVar[list[str]] = []

        async def complete_json(
            self, system: str, user: str, schema: dict[str, Any], *, timeout: float
        ) -> dict[str, Any]:
            self.systems.append(system)
            return await super().complete_json(system, user, schema, timeout=timeout)

    llm = Capture()
    und = Understanding(llm, 2.0, time.monotonic)
    import asyncio

    asyncio.run(und.interpret("1988年4月12日です。", Language.JA, date(2026, 10, 1), ctx, 'Say "your" date of birth'))
    assert llm.systems[-1] == system


def test_end_to_end_rules_and_mock_on_heldout() -> None:
    rows = nb.load(nb.DATA_DIR / "heldout.jsonl")
    assert len(rows) >= 90
    import asyncio

    res = asyncio.run(nb.run(rows, "rules", None))
    s = nb.summarise(res)
    assert s["n"] == len(rows) and s["valid"] == 1.0 and s["gate_rights_recall_ok"]
    assert s["exact_match"] >= 0.8  # the parser is a reasonable baseline, not a ceiling
    res2 = asyncio.run(nb.run(rows[:20], "mock", MockLLM(), path="raw"))
    assert nb.summarise(res2)["valid"] == 1.0
    res3 = asyncio.run(nb.run(rows[:20], "mock", MockLLM(), path="runtime"))
    assert nb.summarise(res3)["valid"] == 1.0
