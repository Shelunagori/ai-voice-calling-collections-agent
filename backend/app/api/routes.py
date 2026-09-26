"""REST API: health, capabilities, scenarios, sessions/audit, metrics, operator tools."""

from __future__ import annotations

import hmac
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .. import __version__
from ..domain.scenarios import SCENARIOS
from ..observability import metrics
from ..state import AppState

router = APIRouter()

DISCLAIMER = (
    "Portfolio proof-of-concept. Synthetic identities and accounts only. Policy rules are simulated demo "
    "rules inspired by regulated collections workflows; they are not legal advice, not certified, and this "
    "system must not be used to contact real debtors."
)


def get_state(request: Request) -> AppState:
    return request.app.state.app_state


def forwarded_client(xff: str, fallback: str) -> str:
    """Right-most X-Forwarded-For entry: the address appended by the platform proxy
    (Railway/Vercel). Entries to its left are client-controlled and spoofable."""
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    return parts[-1] if parts else fallback


def client_ip(request: Request) -> str:
    return forwarded_client(
        request.headers.get("x-forwarded-for", ""), request.client.host if request.client else "unknown"
    )


def require_operator(request: Request, state: AppState = Depends(get_state)) -> None:
    token = state.settings.operator_token
    if not token:
        raise HTTPException(403, "operator endpoints disabled: OPERATOR_TOKEN is not configured")
    got = request.headers.get("authorization", "")
    if not got.startswith("Bearer ") or not hmac.compare_digest(got[7:].encode(), token.encode()):
        metrics.inc("operator_auth_failures")
        raise HTTPException(401, "invalid operator token")


@router.get("/health")
async def health(state: AppState = Depends(get_state)) -> dict[str, Any]:
    """Liveness: the process is up. Dependency health is reported by /ready."""
    return {"status": "ok", "version": __version__, "uptime_s": round(time.monotonic() - state.started_at, 1)}


@router.get("/ready")
async def ready(state: AppState = Depends(get_state)) -> Any:
    deps: dict[str, Any] = {}
    ok = True
    try:
        await state.repo.ping()
        deps["database"] = {"status": "ok", "mode": state.db_mode}
    except Exception as e:
        ok = False
        deps["database"] = {"status": "error", "error": type(e).__name__}
    deps["providers"] = state.providers.labels
    deps["telephony"] = "active" if state.settings.telephony_active else "disabled"
    body = {
        "status": "ready" if ok and not state.draining else "not_ready",
        "draining": state.draining,
        "dependencies": deps,
    }
    if not ok or state.draining:
        from fastapi.responses import JSONResponse

        return JSONResponse(body, status_code=503)
    return body


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics() -> str:
    return metrics.render()


@router.get("/api/capabilities")
async def capabilities(state: AppState = Depends(get_state)) -> dict[str, Any]:
    s = state.settings
    labels = state.providers.labels
    return {
        "version": __version__,
        "disclaimer": DISCLAIMER,
        "providers": labels,
        "mock_mode": {k: (v.startswith("mock") or v == "fake") for k, v in labels.items()},
        "browser_voice_available": labels.get("stt", "mock") != "mock",
        "real_tts": labels.get("tts", "mock") != "mock",
        "response_mode": s.response_mode if labels.get("llm", "").startswith("cloudflare") else "template",
        "telephony": {
            "enabled_flag": s.telephony_enabled,
            "configured": s.twilio_configured,
            "active": s.telephony_active,
            "operator_endpoints": bool(s.operator_token),
            "allowed_numbers_configured": len(s.allowed_call_numbers),
            "transfer_number_configured": bool(s.twilio_transfer_number),
        },
        "languages": ["en", "ja"],
        "latency_budget_ms": 1500,
        "policy": {
            "timezone": s.policy_timezone,
            "calling_hours": f"{s.policy_calling_start_hour:02d}:00-{s.policy_calling_end_hour:02d}:00",
            "max_contact_attempts": s.policy_max_contact_attempts,
            "max_identity_attempts": s.policy_max_identity_attempts,
            "label": "simulated demo policy (not legal requirements)",
        },
        "transcript_retention_days": s.transcript_retention_days,
        "raw_audio_persisted": False,
    }


@router.get("/api/scenarios")
async def scenarios() -> list[dict[str, Any]]:
    return [sc.to_public() for sc in SCENARIOS.values()]


@router.get("/api/sessions")
async def list_sessions(limit: int = 20, state: AppState = Depends(get_state)) -> list[dict[str, Any]]:
    return await state.repo.list_sessions(limit)


@router.get("/api/sessions/{session_id}")
async def session_detail(
    session_id: uuid.UUID, request: Request, state: AppState = Depends(get_state)
) -> dict[str, Any]:
    d = await state.repo.get_session_detail(session_id)
    if not d:
        raise HTTPException(404, "session not found")
    if d["session"].get("channel") == "phone":
        # Phone sessions involve a real person's voice/number: operator only.
        require_operator(request, state)
    return d


@router.get("/api/metrics/latency")
async def latency(state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {
        "budget_ms": 1500,
        "note": "Measured from real session events. Groups are keyed by provider mode; mock-provider groups "
        "measure pipeline overhead only and are not real speech-provider latency.",
        "groups": await state.repo.latency_summary(),
    }


@router.get("/api/accounts")
async def accounts(state: AppState = Depends(get_state)) -> list[dict[str, Any]]:
    """Contact eligibility per synthetic account (stop-contact, attempts)."""
    return await state.repo.list_accounts()


@router.post("/api/operator/reset-demo", dependencies=[Depends(require_operator)])
async def reset_demo(state: AppState = Depends(get_state)) -> dict[str, Any]:
    await state.repo.seed_demo_data(reset=True)
    return {"status": "reset", "accounts": await state.repo.list_accounts()}
