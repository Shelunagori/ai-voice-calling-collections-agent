"""Provider contract tests. They run against local fakes (httpx MockTransport, a local
WebSocket server) so CI never needs paid APIs. Live checks are opt-in:
RUN_LIVE_PROVIDER_TESTS=1 plus real credentials."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time

import httpx
import pytest
import websockets

from app.domain.models import Language
from app.domain.responses import Act, ResponsePlan
from app.providers.base import ErrorKind, ProviderError, STTEventType
from app.providers.cartesia import CartesiaSTT, CartesiaTTS
from app.providers.cloudflare_llm import CloudflareLLM
from app.providers.twilio import TwilioTelephony
from app.telephony.signature import compute_twilio_signature, validate_twilio_signature

# ---------------------------------------------------------------- Cloudflare


def cf(handler, retries=1) -> CloudflareLLM:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return CloudflareLLM("acct", "tok", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", retries, client=client)


async def test_cloudflare_json_mode_request_and_parse():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"success": True, "result": {"response": {"actions": [{"action": "AFFIRM"}]}}})

    out = await cf(handler).complete_json("sys", "yes", {"type": "object"}, timeout=2)
    assert out == {"actions": [{"action": "AFFIRM"}]}
    assert seen["url"].endswith("/accounts/acct/ai/run/@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    assert seen["auth"] == "Bearer tok"
    assert seen["body"]["response_format"]["type"] == "json_schema"


async def test_cloudflare_string_json_response_is_parsed():
    def handler(req):
        return httpx.Response(200, json={"success": True, "result": {"response": '{"actions": []}'}})

    assert await cf(handler).complete_json("s", "u", {}, timeout=2) == {"actions": []}


async def test_cloudflare_retries_bounded_on_429_then_fails():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, json={"errors": []})

    with pytest.raises(ProviderError) as e:
        await cf(handler, retries=1).complete_json("s", "u", {}, timeout=2)
    assert e.value.kind == ErrorKind.RATE_LIMITED and len(calls) == 2


async def test_cloudflare_auth_error_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401, json={})

    with pytest.raises(ProviderError) as e:
        await cf(handler, retries=3).complete_json("s", "u", {}, timeout=2)
    assert e.value.kind == ErrorKind.AUTH and len(calls) == 1


async def test_cloudflare_timeout_classified():
    async def handler(req):
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    with pytest.raises(ProviderError) as e:
        await cf(handler, retries=0).complete_json("s", "u", {}, timeout=0.05)
    assert e.value.kind == ErrorKind.TIMEOUT


async def test_cloudflare_stream_text_sse():
    body = b'data: {"response":"Hello"}\n\ndata: {"response":" there"}\n\ndata: [DONE]\n\n'

    def handler(req):
        assert json.loads(req.content)["stream"] is True
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    toks = [t async for t in cf(handler).stream_text("s", "u", timeout=2)]
    assert "".join(toks) == "Hello there"


async def test_llm_realizer_guard_falls_back_on_unapproved_amount():
    from app.domain.realizer import LLMRealizer
    from app.providers.mock import MockLLM

    plan = ResponsePlan([Act.CLARIFY], Language.EN, disclosure_allowed=True, approved_amounts={20000})
    real = await LLMRealizer(MockLLM(failure="hallucinate"), time.monotonic, 2).realize(plan)
    assert real.source == "template_fallback" and real.guard_violations
    ok = await LLMRealizer(MockLLM(), time.monotonic, 2).realize(plan)
    assert ok.source == "llm" and ok.text == plan.render()


# ---------------------------------------------------------------- Cartesia (local fake server)


async def _serve(handler):
    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}"


async def test_cartesia_stt_contract():
    got = {}

    async def handler(ws):
        got["path"] = ws.request.path
        got["key"] = ws.request.headers.get("X-API-Key")
        audio = b""
        async for msg in ws:
            if isinstance(msg, bytes):
                audio += msg
                if len(audio) >= 640:
                    await ws.send(json.dumps({"type": "transcript", "is_final": False, "text": "hai"}))
            elif msg == "finalize":
                await ws.send(json.dumps({"type": "transcript", "is_final": True, "text": "はい、本人です"}))
                await ws.send(json.dumps({"type": "flush_done"}))
            elif msg == "close":
                await ws.send(json.dumps({"type": "done"}))
                return

    server, url = await _serve(handler)
    try:
        stt = CartesiaSTT("k-test", "ink-whisper", "2026-08-14", url=url)
        stream = await stt.open_stream("ja", 16000)
        await stream.send_audio(b"\x00" * 640)
        await stream.finalize()
        evs = []

        async def collect():
            async for ev in stream.events():
                evs.append(ev)
                if ev.type == STTEventType.FINAL:
                    await stream.close()

        await asyncio.wait_for(collect(), 3)
        assert "language=ja" in got["path"] and "encoding=pcm_s16le" in got["path"]
        assert "sample_rate=16000" in got["path"] and got["key"] == "k-test"
        types = [e.type for e in evs]
        assert STTEventType.PARTIAL in types and STTEventType.FINAL in types
        assert next(e.text for e in evs if e.type == STTEventType.FINAL) == "はい、本人です"
    finally:
        server.close()


async def test_cartesia_tts_contract_and_cancel_on_barge_in():
    log: list[dict] = []

    async def handler(ws):
        async for raw in ws:
            msg = json.loads(raw)
            log.append(msg)
            if msg.get("cancel"):
                continue
            ctx = msg["context_id"]
            for _ in range(50):
                await ws.send(
                    json.dumps(
                        {
                            "type": "chunk",
                            "data": base64.b64encode(b"\x01\x00" * 160).decode(),
                            "done": False,
                            "context_id": ctx,
                        }
                    )
                )
                await asyncio.sleep(0.005)
            await ws.send(json.dumps({"type": "done", "done": True, "context_id": ctx}))

    server, url = await _serve(handler)
    try:
        tts = CartesiaTTS("k", "sonic-3", "2026-08-14", "voice-en", "voice-ja", 16000, url=url)
        chunks = [c async for c in tts.synthesize("hello", "en", context_id="c1")]
        assert len(chunks) == 50 and all(len(c) == 320 for c in chunks)
        req = log[0]
        assert req["output_format"] == {"container": "raw", "encoding": "pcm_s16le", "sample_rate": 16000}
        assert req["voice"] == {"id": "voice-en"} and req["language"] == "en" and req["continue"] is False
        # consume a few chunks then stop (what barge-in does) -> a cancel message is sent
        from contextlib import aclosing

        async with aclosing(tts.synthesize("こんにちは", "ja", context_id="c2")) as gen:
            n = 0
            async for _ in gen:
                n += 1
                if n == 3:
                    break
        for _ in range(40):  # the fake server reads the cancel after finishing its current burst
            await asyncio.sleep(0.05)
            if any(m.get("cancel") for m in log):
                break
        assert any(m.get("cancel") and m["context_id"] == "c2" for m in log)
        assert any(m.get("voice") == {"id": "voice-ja"} for m in log)
        await tts.aclose()
    finally:
        server.close()


# ---------------------------------------------------------------- Twilio


def test_twilio_signature_properties():
    url = "https://example.com/telephony/twilio/status"
    p = {"CallSid": "CA1", "CallStatus": "completed", "From": "+819000000001"}
    sig = compute_twilio_signature("tok", url, p)
    assert validate_twilio_signature("tok", url, dict(reversed(list(p.items()))), sig)
    assert not validate_twilio_signature("tok", url, {**p, "CallStatus": "failed"}, sig)
    assert not validate_twilio_signature("other", url, p, sig)
    assert not validate_twilio_signature("tok", url, p, "")
    assert validate_twilio_signature("tok", "https://example.com:443/telephony/twilio/status", p, sig)


async def test_twilio_rest_contract():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, str(req.url), req.content.decode(), req.headers.get("authorization")))
        if req.url.path.endswith("/Calls.json"):
            return httpx.Response(201, json={"sid": "CA123", "status": "queued"})
        return httpx.Response(200, json={"sid": "CA123"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=("ACx", "tok"))
    tw = TwilioTelephony("ACx", "tok", "+15550001111", "https://demo.example.com", client=client)
    h = await tw.place_call("+819012345678", "sess-1")
    assert h.call_id == "CA123"
    method, url, body, auth = seen[0]
    assert url == "https://api.twilio.com/2010-04-01/Accounts/ACx/Calls.json" and auth.startswith("Basic ")
    assert "StatusCallbackEvent=initiated" in body and "StatusCallbackEvent=completed" in body
    assert "session_id%3Dsess-1" in body
    res = await tw.transfer("CA123", "+815000000000")
    assert res.ok and "%3CDial%3E%2B815000000000%3C%2FDial%3E" in seen[1][2]
    await tw.hangup("CA123")
    assert "Status=completed" in seen[2][2]


# ---------------------------------------------------------------- live (opt-in)
live = pytest.mark.skipif(os.environ.get("RUN_LIVE_PROVIDER_TESTS") != "1", reason="live provider tests disabled")


@live
@pytest.mark.live_provider
async def test_live_cloudflare_nlu():
    from app.config import get_settings
    from app.domain.commands import interpretation_json_schema

    s = get_settings()
    llm = CloudflareLLM(s.cloudflare_account_id, s.cloudflare_api_token, s.cloudflare_ai_model)
    out = await llm.complete_json(
        "Return JSON actions for the caller utterance.", "yes that's me", interpretation_json_schema(), timeout=10
    )
    assert "actions" in out
