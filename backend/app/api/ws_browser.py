"""Browser voice/text session over a WebSocket.

Client -> server
  binary:  PCM16 mono little-endian at AUDIO_SAMPLE_RATE (voice mode), <= MAX_WS_MESSAGE_BYTES
  JSON:    {"type":"text","text":"..."} | {"type":"interrupt"} | {"type":"end"} | {"type":"ping"}
Server -> client
  binary:  4-byte big-endian playback generation + PCM16 audio
  JSON:    {"type":"audio.clear","generation":n} and session events (state, audit, latency...)

The client never sends authoritative state; it only sends audio, text and intents to
interrupt or end. Everything it displays is derived from server events.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import struct
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..domain.models import Channel, Language
from ..domain.scenarios import SCENARIOS
from ..observability import metrics, session_id_var
from ..providers.factory import voice_capable
from ..state import AppState
from .session_service import close_session, open_session

log = logging.getLogger(__name__)
router = APIRouter()


class BrowserTransport:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self._lock = asyncio.Lock()
        self.closed = False

    async def _send(self, fn: Any, data: Any) -> None:
        if self.closed:
            return
        async with self._lock:
            try:
                await fn(data)
            except (WebSocketDisconnect, RuntimeError):
                self.closed = True

    async def send_audio(self, pcm16: bytes, generation: int) -> None:
        await self._send(self.ws.send_bytes, struct.pack(">I", generation) + pcm16)

    async def clear_audio(self, generation: int) -> None:
        await self._send(self.ws.send_text, json.dumps({"type": "audio.clear", "generation": generation}))

    async def send_event(self, event: dict[str, Any]) -> None:
        await self._send(self.ws.send_text, json.dumps(event, default=str, ensure_ascii=False))


def _client_ip(ws: WebSocket) -> str:
    fwd = ws.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (ws.client.host if ws.client else "unknown")


@router.websocket("/ws/session")
async def browser_session(ws: WebSocket) -> None:
    state: AppState = ws.app.state.app_state
    s = state.settings
    origin = ws.headers.get("origin")
    if s.app_env in ("production", "staging") and origin and origin.rstrip("/") not in s.cors_origins:
        await ws.close(code=1008, reason="origin not allowed")
        return
    scenario = (ws.query_params.get("scenario") or "A").upper()
    lang_q = ws.query_params.get("lang", "en")
    mode = ws.query_params.get("mode", "text")
    if scenario not in SCENARIOS or lang_q not in ("en", "ja") or mode not in ("text", "voice"):
        await ws.close(code=1008, reason="invalid parameters")
        return
    if state.draining or len(state.sessions) >= s.max_concurrent_sessions:
        await ws.close(code=1013, reason="busy, try again shortly")
        return
    if not state.limiter.allow("session", _client_ip(ws), s.rate_limit_sessions_per_minute, 60):
        metrics.inc("rate_limited", {"bucket": "session"})
        await ws.close(code=1008, reason="rate limited")
        return
    if mode == "voice" and not voice_capable(state.providers):
        mode = "text"  # degrade: no speech recognition provider configured

    await ws.accept()
    transport = BrowserTransport(ws)
    sess, rec = await open_session(
        state,
        scenario_key=scenario,
        language=Language(lang_q),
        channel=Channel.BROWSER,
        input_mode=mode,
        transport=transport,
    )
    sid = str(sess.c.state.session_id)
    session_id_var.set(sid)
    await transport.send_event(
        {
            "type": "session.created",
            "session_id": sid,
            "input_mode": mode,
            "providers": state.providers.labels,
            "scenario": SCENARIOS[scenario].to_public(),
            "sample_rate": s.audio_sample_rate,
        }
    )
    reason = "client_disconnected"
    ended: asyncio.Task[Any] | None = None
    try:
        await sess.start()
        ended = asyncio.create_task(sess.ended.wait())
        while not sess.lifecycle.terminal:
            recv = asyncio.create_task(ws.receive())
            done, _ = await asyncio.wait(
                {recv, ended}, timeout=s.session_idle_timeout_s, return_when=asyncio.FIRST_COMPLETED
            )
            if recv not in done:
                recv.cancel()
                with contextlib.suppress(BaseException):
                    await recv
                if ended not in done:
                    reason = "idle_timeout"
                break
            msg = recv.result()
            if msg["type"] == "websocket.disconnect":
                break
            if (data := msg.get("bytes")) is not None:
                if len(data) > s.max_ws_message_bytes or len(data) % 2:
                    continue  # drop oversize / malformed frames
                if sess.input_mode == "voice":
                    await sess.on_audio(data)
                continue
            text = msg.get("text")
            if text is None or len(text) > s.max_ws_message_bytes:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            t = obj.get("type")
            if t == "text" and isinstance(obj.get("text"), str):
                await sess.on_text(obj["text"][: s.max_text_turn_chars])
            elif t == "interrupt":
                await sess.interrupt()
            elif t == "end":
                reason = "caller_ended"
                break
            elif t == "ping":
                await transport.send_event({"type": "pong"})
        if sess.lifecycle.terminal:
            reason = sess.c.state.ended_reason or "completed"
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("browser_session_failed")
        reason = "server_error"
    finally:
        if ended is not None:
            ended.cancel()
        # Shielded: persisting the end of the session must survive handler cancellation.
        await asyncio.shield(close_session(state, sess, rec, reason))
        with contextlib.suppress(Exception):
            await ws.close()
