"""Open/close conversation sessions for any transport (browser, phone)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from ..domain.audit import AuditType
from ..domain.clock import SystemClock
from ..domain.models import Channel, Language
from ..domain.scenarios import get_scenario
from ..observability import metrics
from ..persistence.repository import DbRecorder
from ..runtime import build_session
from ..state import AppState
from ..voice.session import Transport, VoiceSession

log = logging.getLogger(__name__)


class MetricsRecorder:
    """Updates Prometheus metrics from session events, then forwards to the DB recorder."""

    def __init__(self, inner: DbRecorder, labels: dict[str, str]) -> None:
        self.inner = inner
        self.mode = "mock" if all(v.startswith("mock") or v == "fake" for v in labels.values()) else "live"

    async def record(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "latency":
            for stage, ms in (payload.get("stages") or {}).items():
                metrics.observe(
                    "conversation_stage_latency_ms",
                    float(ms),
                    {"stage": stage, "provider_mode": self.mode, "input": payload.get("input_mode", "?")},
                )
        elif kind == "barge_in":
            metrics.inc("barge_ins", {"source": str(payload.get("source"))})
            metrics.observe(
                "barge_in_cancel_latency_ms", float(payload.get("cancel_latency_ms", 0)), {"provider_mode": self.mode}
            )
        elif kind == "error":
            metrics.inc("session_errors", {"provider": str(payload.get("provider"))})
        elif kind == "session.ended":
            c = (payload.get("summary") or {}).get("collection", {})
            metrics.inc(
                "sessions_ended", {"call_status": str(c.get("call_status")), "reason": str(payload.get("reason"))}
            )
            if c.get("promise_status") == "CONFIRMED":
                metrics.inc("promises_confirmed")
        await self.inner.record(kind, payload)


async def open_session(
    state: AppState,
    *,
    scenario_key: str,
    language: Language,
    channel: Channel,
    input_mode: str,
    transport: Transport,
    call_id: str | None = None,
    session_id: uuid.UUID | None = None,
) -> tuple[VoiceSession, DbRecorder]:
    sc = get_scenario(scenario_key)
    loaded = await state.repo.get_account(sc.key)
    debtor, account = loaded if loaded else (sc.debtor, sc.account)
    if channel == Channel.BROWSER:
        # Reviewer sessions always start from the scenario's pristine terms so the demo
        # is reproducible; account-level flags (stop-contact) gate *outbound* contact only.
        account = sc.account
    clock = SystemClock()
    sid = session_id or uuid.uuid4()
    rec = DbRecorder(state.repo, sid, account.account_id, clock.now)
    sess = build_session(
        settings=state.settings,
        providers=state.providers,
        debtor=debtor,
        account=account,
        language=language,
        channel=channel,
        transport=transport,
        clock=clock,
        recorder=MetricsRecorder(rec, state.providers.labels),
        input_mode=input_mode,
        session_id=sid,
        call_id=call_id,
    )
    if channel == Channel.BROWSER:
        d = sess.c.policy.reviewer_initiated_contact(sid, clock.now())
        sess.c.audit.record(AuditType.POLICY_DECISION, 0, **d.to_dict())
    await state.repo.create_session(
        session_id=sid,
        account=account,
        scenario_key=sc.key,
        channel=channel.value,
        language=language.value,
        input_mode=input_mode,
        providers=state.providers.labels,
        state=sess.c.state.snapshot(),
        started_at=clock.now(),
        retention_days=state.settings.transcript_retention_days,
        call_id=call_id,
    )
    rec.start()
    sess.c.audit.record(
        AuditType.SESSION_CREATED,
        0,
        scenario=sc.key,
        language=language.value,
        channel=channel.value,
        synthetic_data=True,
    )
    state.sessions[sid] = sess
    metrics.inc("sessions_started", {"channel": channel.value, "input": input_mode})
    metrics.set("active_sessions", len(state.sessions))
    return sess, rec


async def close_session(state: AppState, sess: VoiceSession, rec: DbRecorder, reason: str) -> None:
    task = asyncio.current_task()
    state.closing.add(task)
    try:
        await sess.end(reason)
    finally:
        await rec.close()
        state.sessions.pop(sess.c.state.session_id, None)
        state.closing.discard(task)
        metrics.set("active_sessions", len(state.sessions))
