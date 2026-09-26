"""Persist and read evaluation runs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from ..persistence import tables as t
from ..persistence.repository import Repository, _json_row
from .cases import CASES_BY_KEY


async def save_report(repo: Repository, report: dict[str, Any]) -> uuid.UUID:
    run_id = uuid.UUID(report["run_id"])
    now = datetime.now(UTC)
    j = report["judge"]
    async with repo.engine.begin() as conn:
        for r in report["results"]:
            case = CASES_BY_KEY.get(r["key"])
            exists = (
                await conn.execute(sa.select(t.evaluation_cases.c.key).where(t.evaluation_cases.c.key == r["key"]))
            ).first()
            values = {
                "title": r["title"],
                "category": r["category"],
                "language": r["language"],
                "definition": case.definition() if case else {},
            }
            if exists:
                await conn.execute(
                    t.evaluation_cases.update().where(t.evaluation_cases.c.key == r["key"]).values(**values)
                )
            else:
                await conn.execute(t.evaluation_cases.insert().values(key=r["key"], **values))
        await conn.execute(
            t.evaluation_runs.insert().values(
                id=run_id,
                started_at=now,
                finished_at=now,
                llm_provider="mock",
                llm_model="mock-rules-v1",
                judge_provider=j["provider"],
                judge_model=j["model"],
                judge_prompt_version=j["prompt_version"],
                code_version=report["code_version"][:64],
                total=report["total"],
                passed=report["passed"],
                summary={"by_category": report["by_category"], "clock": report["clock"], "judge_note": j["note"]},
            )
        )
        for r in report["results"]:
            await conn.execute(
                t.evaluation_results.insert().values(
                    id=uuid.uuid4(),
                    run_id=run_id,
                    case_key=r["key"],
                    passed=r["passed"],
                    invariants=r["invariants"],
                    judge=r.get("judge"),
                    transcript=r["transcript"],
                    latency={"turns": r["latency"], "barge_ins": r["barge_ins"], "lifecycle": r["lifecycle_path"]},
                    created_at=now,
                )
            )
    return run_id


async def list_runs(repo: Repository, limit: int = 10) -> list[dict[str, Any]]:
    q = sa.select(t.evaluation_runs).order_by(t.evaluation_runs.c.started_at.desc()).limit(min(limit, 50))
    async with repo.engine.connect() as conn:
        return [_json_row(r) for r in (await conn.execute(q)).mappings()]


async def get_run(repo: Repository, run_id: uuid.UUID) -> dict[str, Any] | None:
    async with repo.engine.connect() as conn:
        run = (
            (await conn.execute(sa.select(t.evaluation_runs).where(t.evaluation_runs.c.id == run_id)))
            .mappings()
            .first()
        )
        if not run:
            return None
        rows = (
            await conn.execute(
                sa.select(
                    t.evaluation_results,
                    t.evaluation_cases.c.title,
                    t.evaluation_cases.c.category,
                    t.evaluation_cases.c.language,
                )
                .join(t.evaluation_cases, t.evaluation_cases.c.key == t.evaluation_results.c.case_key)
                .where(t.evaluation_results.c.run_id == run_id)
            )
        ).mappings()
        return {"run": _json_row(run), "results": [_json_row(r) for r in rows]}
