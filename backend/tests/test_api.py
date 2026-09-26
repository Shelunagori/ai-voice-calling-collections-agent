from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import settings_for_tests
from app.domain.clock import FakeClock
from app.main import create_app
from app.providers.mock import FakeTelephony, MockNotifier, MockSTT, MockTTS
from app.runtime import Providers
from app.telephony.signature import compute_twilio_signature


def fast_providers(telephony=None) -> Providers:
    # Fast mock TTS so real-time pacing does not slow the tests down.
    return Providers(
        llm=None,
        stt=MockSTT(time.monotonic),
        tts=MockTTS(16000, chars_per_s_en=2000, chars_per_s_ja=2000),
        telephony=telephony or FakeTelephony(auth_token="tw-test-token"),
        notifier=MockNotifier(),
        labels={"llm": "mock-rules", "stt": "mock", "tts": "mock", "telephony": "fake"},
    )


def db_url(tmp_path) -> str:
    # File-backed SQLite exercises the real Alembic migration path (not create_all).
    return f"sqlite+aiosqlite:///{tmp_path}/test.db"


@pytest.fixture
def client(tmp_path):
    app = create_app(
        settings_for_tests(operator_token="op-secret", database_url=db_url(tmp_path)), providers=fast_providers()
    )
    with TestClient(app) as c:
        yield c


def recv_until(ws, pred, limit=400):
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("bytes") is not None:
            continue
        if msg.get("text") is None:
            continue
        ev = json.loads(msg["text"])
        if pred(ev):
            return ev
    raise AssertionError("expected event not received")


def agent_done(ev):
    return ev["type"] == "turn.agent"


def test_health_ready_capabilities(client):
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["dependencies"]["database"]["status"] == "ok"
    cap = client.get("/api/capabilities").json()
    assert cap["mock_mode"]["stt"] and cap["telephony"]["active"] is False
    assert cap["browser_voice_available"] is False
    assert "not legal advice" in cap["disclaimer"]
    assert len(client.get("/api/scenarios").json()) == 7
    assert "http_requests_total" in client.get("/metrics").text


def test_browser_text_session_end_to_end_persists_audit(client):
    with client.websocket_connect("/ws/session?scenario=A&lang=en&mode=text") as ws:
        created = recv_until(ws, lambda e: e["type"] == "session.created")
        sid = created["session_id"]
        recv_until(ws, agent_done)
        for line in ["Yes, this is Haruto.", "April 12, 1988", "I can pay 30,000 yen in two weeks", "yes"]:
            ws.send_text(json.dumps({"type": "text", "text": line}))
            recv_until(ws, agent_done)
        st = recv_until(ws, lambda e: e["type"] == "state")
        assert st["collection"]["promise_status"] == "CONFIRMED"
        ws.send_text(json.dumps({"type": "end"}))
        recv_until(ws, lambda e: e["type"] == "session.ended")
    # recorder is flushed on close
    for _ in range(50):
        d = client.get(f"/api/sessions/{sid}").json()
        if d.get("promise") and d["session"].get("ended_at"):
            break
        time.sleep(0.05)
    assert d["promise"]["amount"] == 30000
    types = {e["type"] for e in d["audit"]}
    assert {"session.created", "identity.verified", "promise.confirmed", "policy.decision", "session.ended"} <= types
    assert any(t["speaker"] == "caller" for t in d["turns"])
    assert d["session"]["promise_status"] == "CONFIRMED"
    lat = client.get("/api/metrics/latency").json()
    assert any("input=text" in k for k in lat["groups"])


def test_stop_contact_marks_account(client):
    with client.websocket_connect("/ws/session?scenario=E&lang=ja&mode=text") as ws:
        recv_until(ws, agent_done)
        ws.send_text(json.dumps({"type": "text", "text": "もう電話しないでください"}))
        recv_until(ws, lambda e: e["type"] == "session.ended")
    for _ in range(50):
        acc = {a["scenario_key"]: a for a in client.get("/api/accounts").json()}
        if acc["E"]["stop_contact"]:
            break
        time.sleep(0.05)
    assert acc["E"]["stop_contact"] is True


def test_voice_mode_degrades_to_text_without_stt(client):
    with client.websocket_connect("/ws/session?scenario=A&lang=en&mode=voice") as ws:
        created = recv_until(ws, lambda e: e["type"] == "session.created")
        assert created["input_mode"] == "text"


def test_invalid_params_rejected(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/session?scenario=Z") as ws:
        ws.receive_text()


def test_client_cannot_inject_state(client):
    with client.websocket_connect("/ws/session?scenario=A&lang=en&mode=text") as ws:
        recv_until(ws, agent_done)
        ws.send_text(json.dumps({"type": "state", "collection": {"identity_status": "VERIFIED"}}))
        ws.send_text(json.dumps({"type": "text", "text": "how much do I owe?"}))
        ev = recv_until(ws, agent_done)
        assert "¥" not in ev["text"]
        st = recv_until(ws, lambda e: e["type"] == "state")
        assert st["collection"]["identity_status"] == "UNVERIFIED"


def test_operator_endpoints_require_token(client):
    assert client.post("/api/operator/reset-demo").status_code == 401
    assert client.post("/api/operator/reset-demo", headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = client.post("/api/operator/reset-demo", headers={"Authorization": "Bearer op-secret"})
    assert ok.status_code == 200
    r = client.post("/api/operator/calls", json={"to": "+819000000001"}, headers={"Authorization": "Bearer op-secret"})
    assert r.status_code == 409  # telephony disabled


def test_operator_disabled_without_token(tmp_path):
    app = create_app(settings_for_tests(database_url=db_url(tmp_path)), providers=fast_providers())
    with TestClient(app) as c:
        assert c.post("/api/operator/reset-demo", headers={"Authorization": "Bearer x"}).status_code == 403


def test_payload_limit(client):
    r = client.post("/api/eval/run", content=b"x" * 70000, headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_eval_run_and_fetch(client):
    r = client.post("/api/eval/run")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 25 and body["passed"] == body["total"]
    runs = client.get("/api/eval/runs").json()
    detail = client.get(f"/api/eval/runs/{runs[0]['id']}").json()
    assert len(detail["results"]) == body["total"]


# ---------------------------------------------------------------- telephony
TW_BASE = "https://demo.example.com"


@pytest.fixture
def tclient(tmp_path):
    tel = FakeTelephony(auth_token="tw-test-token")
    tel.live = True
    s = settings_for_tests(
        operator_token="op-secret",
        telephony_enabled=True,
        twilio_account_sid="ACtest",
        twilio_auth_token="tw-test-token",
        twilio_phone_number="+15550001111",
        twilio_webhook_base_url=TW_BASE,
        demo_call_allowed_numbers="+819012345678",
        database_url=db_url(tmp_path),
    )
    clock = FakeClock(datetime(2026, 10, 1, 1, 0, tzinfo=UTC))  # 10:00 JST
    app = create_app(s, providers=fast_providers(tel), policy_clock=clock)
    with TestClient(app) as c:
        c.tel = tel
        c.clock = clock
        yield c


def signed_post(c, path, form):
    sig = compute_twilio_signature("tw-test-token", TW_BASE + path, form)
    return c.post(path, data=form, headers={"X-Twilio-Signature": sig})


def test_webhook_signature_required(tclient):
    r = tclient.post(
        "/telephony/twilio/status",
        data={"CallSid": "CA1", "CallStatus": "ringing"},
        headers={"X-Twilio-Signature": "forged"},
    )
    assert r.status_code == 403
    r = signed_post(tclient, "/telephony/twilio/status", {"CallSid": "CA1", "CallStatus": "ringing"})
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_status_callback_idempotent(tclient):
    form = {"CallSid": "CA2", "CallStatus": "completed", "SequenceNumber": "3"}
    assert signed_post(tclient, "/telephony/twilio/status", form).json()["status"] == "ok"
    assert signed_post(tclient, "/telephony/twilio/status", form).json()["status"] == "duplicate_ignored"


def test_outbound_call_guardrails(tclient):
    h = {"Authorization": "Bearer op-secret"}
    r = tclient.post("/api/operator/calls", json={"to": "+819099999999"}, headers=h)
    assert r.status_code == 403  # not allow-listed
    r = tclient.post("/api/operator/calls", json={"to": "+819012345678", "scenario": "A"}, headers=h)
    assert r.json()["status"] == "dialing" and tclient.tel.calls
    # outside the demo calling window the policy blocks the call before dialling
    tclient.clock.set(datetime(2026, 10, 1, 14, 0, tzinfo=UTC))  # 23:00 JST
    n = len(tclient.tel.calls)
    r = tclient.post("/api/operator/calls", json={"to": "+819012345678", "scenario": "B"}, headers=h)
    assert r.json()["status"] == "blocked_by_policy" and len(tclient.tel.calls) == n


def test_media_stream_session(tclient):
    twiml = signed_post(tclient, "/telephony/twilio/voice", {"CallSid": "CA9", "From": "+819012345678"}).text
    assert "<Connect><Stream" in twiml
    import re

    sid = re.search(r"name='session_id' value=\"([^\"]+)\"", twiml)[1]
    token = re.search(r"name='token' value=\"([^\"]+)\"", twiml)[1]
    with tclient.websocket_connect("/telephony/twilio/media") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(
            json.dumps(
                {
                    "event": "start",
                    "start": {
                        "streamSid": "MZ1",
                        "callSid": "CA9",
                        "customParameters": {"session_id": sid, "token": token},
                    },
                }
            )
        )
        got_media = json.loads(ws.receive_text())
        assert got_media["event"] == "media" and got_media["streamSid"] == "MZ1"
        ws.send_text(json.dumps({"event": "stop"}))
    for _ in range(50):
        d = tclient.get(f"/api/sessions/{sid}", headers={"Authorization": "Bearer op-secret"}).json()
        if d.get("session", {}).get("ended_at"):
            break
        time.sleep(0.05)
    assert d["session"]["channel"] == "phone" and d["session"]["call_id"] == "CA9"


def test_media_stream_rejects_bad_token(tclient):
    signed_post(tclient, "/telephony/twilio/voice", {"CallSid": "CA10"})
    from starlette.websockets import WebSocketDisconnect

    with tclient.websocket_connect("/telephony/twilio/media") as ws:
        ws.send_text(
            json.dumps(
                {
                    "event": "start",
                    "start": {"streamSid": "MZ", "customParameters": {"session_id": "x", "token": "bad"}},
                }
            )
        )
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


def test_wire_contract_audit_envelope(client):
    """The browser relies on {type:"audit", event_type:<audit type>} for the policy feed."""
    with client.websocket_connect("/ws/session?scenario=A&lang=en&mode=text") as ws:
        ev = recv_until(ws, lambda e: e["type"] == "audit" and e.get("event_type") == "policy.decision")
        assert ev["data"]["rule"] and ev["data"]["decision"] in ("ALLOW", "BLOCK", "NOT_APPLICABLE")
        recv_until(ws, agent_done)
        ws.send_text(json.dumps({"type": "end"}))
        recv_until(ws, lambda e: e["type"] == "session.ended")
