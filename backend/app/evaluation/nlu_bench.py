"""NLU benchmark: score any language-layer provider on the frozen held-out set.

    python -m app.evaluation.nlu_bench --provider rules                     # deterministic parser
    python -m app.evaluation.nlu_bench --provider cloudflare                # model from settings
    python -m app.evaluation.nlu_bench --provider cloudflare --model @cf/google/gemma-2b-it-lora --lora <id>
    python -m app.evaluation.nlu_bench --provider cloudflare --path runtime # full Understanding path (LLM + rules merge)
    ... --out report.json --md report.md

Two paths:
* `raw`     — the provider's own output, schema-validated and phase-constrained, nothing else.
              This measures the *model*.
* `runtime` — `Understanding.interpret()`: LLM + rules merge + DOB grounding + fallback.
              This measures what the agent ships.

Metrics per example (all deterministic):
* `action_match`  — the set of action names equals the gold set;
* `exact_match`   — actions *and* their slots (amount / date / days_from_now / dob parts) equal the gold,
                    order-insensitive;
* `slot_match`    — for gold actions that carry slots, the predicted action of the same name carries the
                    same slots (None when the gold has no slots);
* `valid`         — the provider returned a parseable, schema-valid interpretation (rules: always);
* `rights_recall` — when the gold contains STOP_CONTACT / REQUEST_HUMAN, the prediction contains it
                    (None otherwise). This is the hard gate: a model that drops a caller-rights intent
                    fails the benchmark whatever its other scores.
Latency is wall-clock per provider call (p50 / p95, ms).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..domain import nlu_rules
from ..domain.commands import Action, ProposedAction, interpretation_json_schema
from ..domain.models import DialogPhase, Language
from ..domain.turn_context import TurnContext, constrain, context_for
from ..domain.understanding import Understanding, build_system_prompt, validate_llm_output
from ..providers.base import LLMProvider

DATA_DIR = Path(__file__).resolve().parents[2] / "training" / "data"
RIGHTS = {Action.STOP_CONTACT.value, Action.REQUEST_HUMAN.value}
SLOT_KEYS = ("amount", "date", "days_from_now", "dob", "dob_year", "dob_month", "dob_day")


# ----------------------------------------------------------------------------- scoring
def compact(actions: list[ProposedAction]) -> list[dict[str, Any]]:
    out = []
    for a in actions:
        d = a.model_dump(mode="json", exclude_none=True)
        d.pop("note", None)
        out.append(d)
    return out


def _canon(actions: list[dict[str, Any]]) -> list[str]:
    return sorted(
        json.dumps({k: v for k, v in a.items() if k == "action" or k in SLOT_KEYS}, sort_keys=True) for a in actions
    )


def score(gold: list[dict[str, Any]], pred: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Score one prediction (a list of compact actions; None = provider failed)."""
    gold_names = {a["action"] for a in gold}
    if pred is None:
        pred_names: set[str] = set()
        rights = None if not (gold_names & RIGHTS) else False
        return {
            "valid": False,
            "action_match": False,
            "exact_match": False,
            "slot_match": None if not _has_slots(gold) else False,
            "rights_recall": rights,
        }
    pred_names = {a["action"] for a in pred}
    action_match = pred_names == gold_names
    exact = _canon(gold) == _canon(pred)
    slot_match: bool | None = None
    if _has_slots(gold):
        slot_match = True
        by_name = {a["action"]: a for a in pred}
        for g in gold:
            if not any(k in g for k in SLOT_KEYS):
                continue
            p = by_name.get(g["action"])
            if p is None or {k: g.get(k) for k in SLOT_KEYS} != {k: p.get(k) for k in SLOT_KEYS}:
                slot_match = False
    rights_recall = None if not (gold_names & RIGHTS) else bool((gold_names & RIGHTS) <= pred_names)
    return {
        "valid": True,
        "action_match": action_match,
        "exact_match": exact,
        "slot_match": slot_match,
        "rights_recall": rights_recall,
    }


def _has_slots(actions: list[dict[str, Any]]) -> bool:
    return any(k in a for a in actions for k in SLOT_KEYS)


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(sum(1 for v in vals if v) / len(vals), 4) if vals else None


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    lat = sorted(r["latency_ms"] for r in results if r.get("latency_ms") is not None)
    summary: dict[str, Any] = {
        "n": len(results),
        "valid": _rate(results, "valid"),
        "action_match": _rate(results, "action_match"),
        "exact_match": _rate(results, "exact_match"),
        "slot_match": _rate(results, "slot_match"),
        "rights_recall": _rate(results, "rights_recall"),
        "latency_p50_ms": round(statistics.median(lat), 1) if lat else None,
        "latency_p95_ms": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 1) if lat else None,
        "by_category": {},
        "by_language": {},
    }
    for group_key, name in (("category", "by_category"), ("language", "by_language")):
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in results:
            groups[r[group_key]].append(r)
        summary[name] = {
            g: {
                "n": len(rows),
                "action_match": _rate(rows, "action_match"),
                "exact_match": _rate(rows, "exact_match"),
                "slot_match": _rate(rows, "slot_match"),
            }
            for g, rows in sorted(groups.items())
        }
    summary["gate_rights_recall_ok"] = summary["rights_recall"] in (None, 1.0)
    return summary


def markdown(summary: dict[str, Any], label: str) -> str:
    def pct(v: float | None) -> str:
        return "—" if v is None else f"{100 * v:.1f}%"

    lines = [
        f"### {label}",
        "",
        f"n={summary['n']} · valid {pct(summary['valid'])} · action match **{pct(summary['action_match'])}** · exact match **{pct(summary['exact_match'])}** · slot match {pct(summary['slot_match'])} · caller-rights recall {pct(summary['rights_recall'])} ({'gate OK' if summary['gate_rights_recall_ok'] else 'GATE FAILED'}) · latency p50 {summary['latency_p50_ms']} ms / p95 {summary['latency_p95_ms']} ms",
        "",
        "| category | n | action | exact | slots |",
        "|---|---:|---:|---:|---:|",
    ]
    for cat, s in summary["by_category"].items():
        lines.append(
            f"| {cat} | {s['n']} | {pct(s['action_match'])} | {pct(s['exact_match'])} | {pct(s['slot_match'])} |"
        )
    lines.append("")
    lines.append("| language | n | action | exact | slots |")
    lines.append("|---|---:|---:|---:|---:|")
    for lang, s in summary["by_language"].items():
        lines.append(
            f"| {lang} | {s['n']} | {pct(s['action_match'])} | {pct(s['exact_match'])} | {pct(s['slot_match'])} |"
        )
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- running
@dataclass
class Row:
    rec: dict[str, Any]

    @property
    def ctx(self) -> TurnContext:
        return context_for(DialogPhase(self.rec["phase"]))

    @property
    def language(self) -> Language:
        return Language(self.rec["language"])

    @property
    def today(self) -> date:
        return date.fromisoformat(self.rec["today"])


def load(path: Path) -> list[Row]:
    return [Row(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def predict_rules(row: Row) -> list[dict[str, Any]]:
    interp = nlu_rules.interpret(row.rec["utterance"], row.language, row.today, context=row.ctx)
    return compact(interp.actions)


async def predict_raw(llm: LLMProvider, row: Row, timeout: float) -> tuple[list[dict[str, Any]] | None, str | None]:
    system = build_system_prompt(row.ctx, row.today, row.language, row.rec["last_agent"])
    try:
        raw = await llm.complete_json(
            system, row.rec["utterance"], interpretation_json_schema(row.ctx.allowed), timeout=timeout
        )
        interp = constrain(validate_llm_output(raw), row.ctx)
        return compact(interp.actions), None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:160]}"


async def predict_runtime(und: Understanding, row: Row) -> tuple[list[dict[str, Any]] | None, str | None]:
    res = await und.interpret(row.rec["utterance"], row.language, row.today, row.ctx, row.rec["last_agent"])
    return compact(res.interpretation.actions), res.llm_error


async def run(
    rows: list[Row],
    provider: str,
    llm: LLMProvider | None,
    path: str = "raw",
    timeout: float = 10.0,
    concurrency: int = 4,
) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)
    und = Understanding(llm, timeout, time.monotonic) if path == "runtime" else None

    async def one(row: Row) -> dict[str, Any]:
        gold = row.rec["gold"]["actions"]
        async with sem:
            t0 = time.monotonic()
            err: str | None = None
            if provider == "rules":
                pred: list[dict[str, Any]] | None = predict_rules(row)
            elif und is not None:
                pred, err = await predict_runtime(und, row)
            else:
                assert llm is not None
                pred, err = await predict_raw(llm, row, timeout)
            latency = (time.monotonic() - t0) * 1000
        return {
            "id": row.rec["id"],
            "category": row.rec["category"],
            "language": row.rec["language"],
            "phase": row.rec["phase"],
            "utterance": row.rec["utterance"],
            "gold": gold,
            "pred": pred,
            "error": err,
            "latency_ms": round(latency, 1),
            **score(gold, pred),
        }

    return await asyncio.gather(*(one(r) for r in rows))


def build_llm(provider: str, model: str | None, lora: str | None) -> LLMProvider | None:
    if provider == "rules":
        return None
    from ..config import Settings

    s = Settings()
    if provider == "cloudflare":
        if not s.cloudflare_configured:
            raise SystemExit("cloudflare credentials not configured (CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN)")
        from ..providers.cloudflare_llm import CloudflareLLM

        kw: dict[str, Any] = {"lora": lora} if lora else {}
        return CloudflareLLM(
            s.cloudflare_account_id, s.cloudflare_api_token, model or s.cloudflare_ai_model, s.llm_max_retries, **kw
        )
    if provider == "mock":
        from ..providers.mock import MockLLM

        return MockLLM()
    raise SystemExit(f"unknown provider {provider}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="rules", choices=["rules", "mock", "cloudflare"])
    ap.add_argument("--model", default=None, help="override the model id (cloudflare)")
    ap.add_argument("--lora", default=None, help="Cloudflare finetune id/name to apply (BYO LoRA)")
    ap.add_argument("--path", default="raw", choices=["raw", "runtime"])
    ap.add_argument("--data", default=str(DATA_DIR / "heldout.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", default=None, help="write the full JSON report here")
    ap.add_argument("--md", default=None, help="append a markdown summary here")
    ap.add_argument("--label", default=None)
    args = ap.parse_args(argv)

    rows = load(Path(args.data))
    if args.limit:
        rows = rows[: args.limit]
    llm = build_llm(args.provider, args.model, args.lora)
    label = args.label or (
        f"{args.provider}"
        + (f":{llm.model}" if llm else "")
        + (f" +lora {args.lora}" if args.lora else "")
        + f" [{args.path}]"
    )
    results = asyncio.run(run(rows, args.provider, llm, args.path, args.timeout, args.concurrency))
    summary = summarise(results)
    report = {
        "label": label,
        "provider": args.provider,
        "model": getattr(llm, "model", None),
        "lora": args.lora,
        "path": args.path,
        "data": str(args.data),
        "summary": summary,
        "results": results,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = markdown(summary, label)
    if args.md:
        with Path(args.md).open("a", encoding="utf-8") as f:
            f.write(md + "\n")
    print(md)
    errors = [r for r in results if r["error"]]
    if errors:
        print(f"{len(errors)} provider errors; first: {errors[0]['error']}", file=sys.stderr)
    return 0 if summary["gate_rights_recall_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
