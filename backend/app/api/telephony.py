"""Twilio Voice webhooks, Media Streams WebSocket and the protected outbound-call endpoint.

Flow for an operator-initiated demo call:
  POST /api/operator/calls  (Bearer OPERATOR_TOKEN, allow-listed number, contact policy)
    -> Twilio dials -> POST /telephony/twilio/voice (signed) -> TwiML <Connect><Stream>
    -> WS /telephony/twilio/media (per-call HMAC token) -> VoiceSession(channel=PHONE)
  POST /telephony/twilio/status (signed, idempotent) -> call lifecycle / disconnect handling
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from typing import Any
from xml.sax.saxutils import quoteattr

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..domain.audit import AuditType
from ..domain.models import Channel, Language
from ..domain.scenarios import SCENARIOS
from ..observability import metrics
from ..providers.base import ProviderError
from ..state import AppState
from ..voice.audio import downsample_16k_to_8k, pcm16_to_ulaw, ulaw_to_pcm16, upsample_8k_to_16k
from .routes import client_ip, get_state, require_operator
from .session_service import close_session, open_session

log = logging.getLogger(__name__)
router = APIRouter()
E164 = re.compile(r"^\+[1-9]\d{7,14}$")
TERMINAL_STATUSES = {"completed", "busy", "failed", "no-answer", "canceled"}


PENDING_TTL_S = 600
PENDING_MAX = 100


def stream_token(auth_token: str, session_id: str) -> str:
    if not auth_token:
        raise ValueError("refusing to derive a media-stream token from an empty key")
    return hmac.new(auth_token.encode(), f"media:{session_id}".encode(), hashlib.sha256).hexdigest()


def require_telephony(state: AppState = Depends(get_state)) -> None:
    """Telephony endpoints do not exist unless telephony is enabled *and* configured."""
    if not state.settings.telephony_active:
        raise HTTPException(404, "not found")


def _remember_pending(state: AppState, sid: str, ctx: dict[str, Any]) -> None:
    now = time.monotonic()
    for k in [k for k, v in state.pending_calls.items() if now - v.get("created", now) > PENDING_TTL_S]:
        state.pending_calls.pop(k, None)
    while len(state.pending_calls) >= PENDING_MAX:
        state.pending_calls.pop(next(iter(state.pending_calls)))
    state.pending_calls[sid] = {**ctx, "created": now}


def _public_url(state: AppState, request: Request) -> str:
    base = state.settings.twilio_webhook_base_url.rstrip("/")
    q = f"?{request.url.query}" if request.url.query else ""
    return f"{base}{request.url.path}{q}"


async def _verified_form(request: Request, state: AppState) -> dict[str, str]:
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not state.settings.validate_twilio_signatures:
        return form
    sig = request.headers.get("x-twilio-signature", "")
    if not state.providers.telephony.validate_signature(_public_url(state, request), form, sig):
        metrics.inc("webhook_signature_failures")
        raise HTTPException(403, "invalid Twilio signature")
    return form


class CallRequest(BaseModel):
    to: str = Field(max_length=16)
    scenario: str = Field(default="A", max_length=2)
    language: Language = Language.JA


@router.post("/api/operator/calls", dependencies=[Depends(require_operator)])
async def start_call(body: CallRequest, request: Request, state: AppState = Depends(get_state)) -> dict[str, Any]:
    s = state.settings
    if not s.telephony_active:
        raise HTTPException(409, "telephony disabled: set TELEPHONY_ENABLED=true and Twilio credentials")
    if not state.limiter.allow("call", client_ip(request), s.rate_limit_calls_per_hour, 3600):
        raise HTTPException(429, "call rate limit reached")
    if not E164.match(body.to) or body.to not in s.allowed_call_numbers:
        raise HTTPException(403, "destination is not on DEMO_CALL_ALLOWED_NUMBERS")
    key = body.scenario.upper()
    if key not in SCENARIOS:
        raise HTTPException(404, "unknown scenario")
    loaded = await state.repo.get_account(key)
    if not loaded:
        raise HTTPException(500, "demo data not seeded")
    _, account = loaded
    sid = uuid.uuid4()
    from ..runtime import policy_from_settings

    decisions = policy_from_settings(s).evaluate_contact(account, state.policy_clock.now(), sid)
    if not all(d.allowed for d in decisions):
        return {"status": "blocked_by_policy", "decisions": [d.to_dict() for d in decisions]}
    try:
        handle = await state.providers.telephony.place_call(body.to, str(sid))
    except ProviderError as e:
        raise HTTPException(502, f"telephony provider error: {e.kind.value}") from e
    await state.repo.increment_contact_attempts(account.account_id)
    _remember_pending(
        state,
        str(sid),
        {
            "scenario": key,
            "language": body.language.value,
            "call_id": handle.call_id,
            "decisions": [d.to_dict() for d in decisions],
        },
    )
    return {
        "status": "dialing",
        "session_id": str(sid),
        "call_id": handle.call_id,
        "decisions": [d.to_dict() for d in decisions],
    }


@router.post("/telephony/twilio/voice", dependencies=[Depends(require_telephony)])
async def twilio_voice(request: Request, state: AppState = Depends(get_state)) -> Response:
    form = await _verified_form(request, state)
    sid = request.query_params.get("session_id") or ""
    if sid not in state.pending_calls:
        # Inbound (or unknown) call: start a fresh verification-first session.
        sid = str(uuid.uuid4())
        _remember_pending(
            state,
            sid,
            {"scenario": "A", "language": "ja", "call_id": form.get("CallSid", ""), "decisions": [], "inbound": True},
        )
    ws_base = (
        state.settings.twilio_webhook_base_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
    )
    token = stream_token(state.settings.twilio_auth_token, sid)
    twiml = (
        "<?xml version='1.0' encoding='UTF-8'?><Response><Connect>"
        f"<Stream url={quoteattr(ws_base + '/telephony/twilio/media')}>"
        f"<Parameter name='session_id' value={quoteattr(sid)}/>"
        f"<Parameter name='token' value={quoteattr(token)}/>"
        "</Stream></Connect></Response>"
    )
    return Response(twiml, media_type="application/xml")


@router.post("/telephony/twilio/status", dependencies=[Depends(require_telephony)])
async def twilio_status(request: Request, state: AppState = Depends(get_state)) -> dict[str, str]:
    form = await _verified_form(request, state)
    call_sid = form.get("CallSid", "")
    status = form.get("CallStatus", "")
    key = f"{call_sid}:{status}:{form.get('SequenceNumber', '')}"
    first = await state.repo.record_webhook_once(key, call_sid, "status", form)
    if not first:
        return {"status": "duplicate_ignored"}
    metrics.inc("call_status", {"status": status})
    for sess in list(state.sessions.values()):
        if sess.call_id == call_sid:
            sess.c.audit.record(
                AuditType.CALL_STATUS, sess.c.turn_index, status=status, duration=form.get("CallDuration")
            )
            if status in TERMINAL_STATUSES:
                await sess.end(f"call_{status}")
    return {"status": "ok"}


class TwilioTransport:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.stream_sid = ""
        self._lock = asyncio.Lock()
        self.closed = False

    async def _send(self, obj: dict[str, Any]) -> None:
        if self.closed or not self.stream_sid:
            return
        async with self._lock:
            try:
                await self.ws.send_text(json.dumps(obj))
            except (WebSocketDisconnect, RuntimeError):
                self.closed = True

    async def send_audio(self, pcm16: bytes, generation: int) -> None:
        payload = base64.b64encode(pcm16_to_ulaw(downsample_16k_to_8k(pcm16))).decode()
        await self._send({"event": "media", "streamSid": self.stream_sid, "media": {"payload": payload}})

    async def clear_audio(self, generation: int) -> None:
        await self._send({"event": "clear", "streamSid": self.stream_sid})

    async def send_event(self, event: dict[str, Any]) -> None:
        return None  # phone callers only get audio; operators watch via the audit trail


@router.websocket("/telephony/twilio/media")
async def twilio_media(ws: WebSocket) -> None:
    state: AppState = ws.app.state.app_state
    if not state.settings.telephony_active:
        await ws.close(code=1008)
        return
    await ws.accept()
    transport = TwilioTransport(ws)
    sess = rec = None
    reason = "media_stream_closed"
    try:
        while True:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=state.settings.session_idle_timeout_s)
            if len(raw) > state.settings.max_ws_message_bytes:
                continue
            msg = json.loads(raw)
            ev = msg.get("event")
            if ev == "start" and sess is None:
                start = msg.get("start", {})
                params = start.get("customParameters", {}) or {}
                sid = str(params.get("session_id", ""))
                expected = stream_token(state.settings.twilio_auth_token, sid)
                # Verify before consuming the pending entry, so a forged attempt cannot burn it.
                if sid not in state.pending_calls or not hmac.compare_digest(expected, str(params.get("token", ""))):
                    metrics.inc("media_stream_auth_failures")
                    await ws.close(code=1008)
                    return
                if state.draining or len(state.sessions) + state.opening >= state.settings.max_concurrent_sessions:
                    await ws.close(code=1013)  # entry kept: Twilio may retry the stream
                    return
                ctx = state.pending_calls.pop(sid)
                transport.stream_sid = start.get("streamSid", "")
                call_sid = start.get("callSid") or ctx.get("call_id")
                sess, rec = await open_session(
                    state,
                    scenario_key=ctx["scenario"],
                    language=Language(ctx["language"]),
                    channel=Channel.PHONE,
                    input_mode="voice",
                    transport=transport,
                    call_id=call_sid,
                    session_id=uuid.UUID(sid),
                )
                for d in ctx.get("decisions", []):
                    sess.c.audit.record(AuditType.POLICY_DECISION, 0, **d)
                await sess.start()
            elif ev == "media" and sess is not None:
                payload = msg.get("media", {}).get("payload", "")
                with contextlib.suppress(ValueError):
                    await sess.on_audio(upsample_8k_to_16k(ulaw_to_pcm16(base64.b64decode(payload))))
                if sess.lifecycle.terminal:
                    break
            elif ev == "stop":
                reason = "caller_hangup"
                break
    except (WebSocketDisconnect, TimeoutError):
        reason = "media_stream_disconnected"
    except Exception:
        log.exception("twilio_media_failed")
        reason = "server_error"
    finally:
        if sess is not None and rec is not None:
            if sess.lifecycle.terminal:
                reason = sess.c.state.ended_reason or reason
            await asyncio.shield(close_session(state, sess, rec, reason))
            tel = state.providers.telephony
            if sess.call_id and tel.live and reason not in ("caller_hangup", "transferred_to_human"):
                with contextlib.suppress(Exception):
                    await tel.hangup(sess.call_id)
