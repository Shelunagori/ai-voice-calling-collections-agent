"""Evaluation API. Anyone may trigger the deterministic mock-provider suite (rate-limited,
~seconds of CPU); the LLM judge requires the operator token because it costs money."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..evaluation.judge import LLMJudge
from ..evaluation.runner import run_suite
from ..evaluation.store import get_run, list_runs, save_report
from ..providers.factory import build_judge_llm
from ..state import AppState
from .routes import client_ip, get_state, require_operator

router = APIRouter()
_run_lock = asyncio.Lock()


async def _run(state: AppState, judge: Any = None) -> dict[str, Any]:
    if _run_lock.locked():
        raise HTTPException(409, "an evaluation run is already in progress")
    async with _run_lock:
        # Run on a worker thread with its own event loop so live voice sessions on the
        # main loop are not delayed by the (CPU-bound) simulation.
        report = await asyncio.to_thread(asyncio.run, run_suite(judge=judge))
        await save_report(state.repo, report)
    return {"run_id": report["run_id"], "passed": report["passed"], "total": report["total"], "judge": report["judge"]}


@router.post("/api/eval/run")
async def run_eval(request: Request, state: AppState = Depends(get_state)) -> dict[str, Any]:
    if not state.limiter.allow("eval", client_ip(request), 3, 600):
        raise HTTPException(429, "evaluation rate limit reached")
    return await _run(state)


@router.post("/api/eval/run-llm-judge", dependencies=[Depends(require_operator)])
async def run_eval_llm(state: AppState = Depends(get_state)) -> dict[str, Any]:
    llm = build_judge_llm(state.settings.model_copy(update={"judge_provider": "cloudflare"}))
    if llm is None:
        raise HTTPException(409, "LLM judge needs CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN")
    return await _run(state, LLMJudge(llm))


@router.get("/api/eval/runs")
async def runs(state: AppState = Depends(get_state)) -> list[dict[str, Any]]:
    return await list_runs(state.repo)


@router.get("/api/eval/runs/{run_id}")
async def run_detail(run_id: uuid.UUID, state: AppState = Depends(get_state)) -> dict[str, Any]:
    d = await get_run(state.repo, run_id)
    if not d:
        raise HTTPException(404, "run not found")
    return d
