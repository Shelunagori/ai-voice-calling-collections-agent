"""Structured voice-session observability and provider-failure signalling."""

from __future__ import annotations

import asyncio
import base64
import json
import logging

import pytest
import websockets
from fastapi.testclient import TestClient

from app.config import settings_for_tests
from app.main import create_app
from app.providers.base import ErrorKind, ProviderError
from app.providers.cartesia import CartesiaTTS
from app.voice import audio
from tests.conftest import drain, make_session


def voice_events(caplog):
    return [r for r in caplog.records if r.name == "app.voice.session" and getattr(r, "voice_event", None)]


async def test_lifecycle_milestones_are_logged_without_audio_or_text(caplog):
    caplog.set_level(logging.INFO, logger="app.voice.session")
    sess, tr, clk, _ = make_session("A", input_mode="voice", stt_script=["yes this is haruto"])
    await sess.start(run_background=False)
    await drain(sess, clk)
    for fr in audio.frames(audio.synth_speechlike(0.8, 16000) + audio.silence(1.5, 16000), 16000):
        await sess.on_audio(fr)
        await clk.advance_async(0.02)
        await sess.tick()
    await drain(sess, clk)
    await sess.end("caller_ended")
    names = [r.voice_event for r in voice_events(caplog)]
    for expected in ("session_started", "stt_stream_ready", "first_audio_frame", "first_final_transcript",
                     "tts_started", "first_tts_audio", "session_ended"):  # fmt: skip
        assert expected in names, (expected, names)
    assert names.count("first_audio_frame") == 1
    ended = next(r for r in voice_events(caplog) if r.voice_event == "session_ended")
    assert ended.reason == "caller_ended" and ended.session_id
    for r in voice_events(caplog):  # no transcript text, no raw audio in logs
        assert "haruto" not in r.getMessage().lower() and "haruto" not in json.dumps(r.__dict__, default=str).lower()


class BrokenSTT:
    name = "cartesia"
    model = "ink-whisper"
    provides_endpointing = True

    async def open_stream(self, language, sample_rate):
        raise ProviderError("cartesia_stt", ErrorKind.AUTH, "connect failed: InvalidStatus")


async def test_stt_init_failure_is_sent_to_browser_and_degrades(caplog):
    caplog.set_level(logging.INFO, logger="app.voice.session")
    sess, tr, clk, prov = make_session("A", input_mode="voice")
    sess.d.stt = BrokenSTT()
    await sess.start(run_background=False)
    await drain(sess, clk)
    errs = tr.of("error")
    assert errs and errs[0]["provider"] == "stt" and errs[0]["degraded"] is True
    assert tr.of("input_mode") and tr.of("input_mode")[0]["input_mode"] == "text"
    assert sess.input_mode == "text"
    assert any(r.voice_event == "provider_error" for r in voice_events(caplog))
    await sess.end("done")


async def test_cartesia_tts_realigns_odd_length_chunks():
    """Cartesia does not document sample-aligned chunks; the adapter must never emit odd bytes."""
    parts = [b"\x01\x02\x03", b"\x04\x05", b"\x06\x07\x08\x09\x0a"]

    async def handler(ws):
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("cancel"):
                continue
            for p in parts:
                await ws.send(json.dumps({"type": "chunk", "data": base64.b64encode(p).decode(),
                                          "done": False, "context_id": msg["context_id"]}))  # fmt: skip
            await ws.send(json.dumps({"type": "done", "done": True, "context_id": msg["context_id"]}))

    server = await websockets.serve(handler, "127.0.0.1", 0)
    url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        tts = CartesiaTTS("k", "sonic-3", "2026-08-14", "v", "", 16000, url=url)
        out = [c async for c in tts.synthesize("hi", "en", context_id="c")]
        assert all(len(c) % 2 == 0 for c in out)
        assert b"".join(out) == b"".join(parts)[:10]  # 10 bytes = 5 whole samples; nothing else lost
        await tts.aclose()
    finally:
        server.close()
        await asyncio.sleep(0)


@pytest.mark.parametrize(
    "origin,allowed",
    [("https://ai-voice-calling-collections-agent.vercel.app", True), ("https://evil.example", False)],
)
def test_cors_allows_exactly_the_frontend_origin(tmp_path, origin, allowed):
    s = settings_for_tests(
        frontend_url="https://ai-voice-calling-collections-agent.vercel.app/",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/c.db",
    )
    with TestClient(create_app(s)) as c:
        r = c.options("/api/capabilities", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
        got = r.headers.get("access-control-allow-origin")
        assert (got == origin) is allowed and got != "*"


def test_ws_origin_enforced_in_production(tmp_path):
    from starlette.websockets import WebSocketDisconnect

    s = settings_for_tests(app_env="production", frontend_url="https://ai-voice-calling-collections-agent.vercel.app",
                           database_url=f"sqlite+aiosqlite:///{tmp_path}/o.db",
                           allow_ephemeral_database=True)  # fmt: skip  # production-on-SQLite now needs the escape hatch
    with TestClient(create_app(s)) as c:
        with (
            pytest.raises(WebSocketDisconnect),
            c.websocket_connect("/ws/session?scenario=A", headers={"origin": "https://evil.example"}) as ws,
        ):
            ws.receive_text()
        with c.websocket_connect(
            "/ws/session?scenario=A", headers={"origin": "https://ai-voice-calling-collections-agent.vercel.app"}
        ) as ws:
            assert json.loads(ws.receive_text())["type"] == "session.created"
            ws.send_text(json.dumps({"type": "end"}))
