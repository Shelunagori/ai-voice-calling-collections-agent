"""Regression tests for defects found in the adversarial review (written red-first)."""

from __future__ import annotations

import json
import re
import time

from fastapi.testclient import TestClient

from app.config import settings_for_tests
from app.domain.commands import Action, Interpretation, ProposedAction
from app.domain.models import CallStatus, Language, PromiseStatus
from app.domain.nlu_rules import interpret
from app.domain.realizer import LLMRealizer
from app.domain.responses import guard
from app.domain.understanding import merge
from app.main import create_app
from app.providers.factory import build_providers
from app.providers.mock import MockLLM
from app.telephony.signature import compute_twilio_signature
from app.voice.lifecycle import VoiceState
from tests.conftest import drain, make_controller, make_session, say, verify

T = __import__("datetime").date(2026, 10, 1)


# ---------------------------------------------------------------- 1. telephony spoofing when disabled
def test_telephony_routes_absent_when_disabled(tmp_path):
    s = settings_for_tests(database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db")
    app = create_app(s, build_providers(s))
    with TestClient(app) as c:
        form = {"CallSid": "CAattacker"}
        for token in ("fake-token", ""):
            sig = compute_twilio_signature(token, "/telephony/twilio/voice", form)
            r = c.post("/telephony/twilio/voice", data=form, headers={"x-twilio-signature": sig})
            assert r.status_code == 404
        r = c.post("/telephony/twilio/status", data={"CallSid": "CA", "CallStatus": "completed"})
        assert r.status_code == 404
        assert not app.state.app_state.pending_calls


def test_pending_calls_bounded_and_token_checked_before_pop(tmp_path):
    from app.providers.mock import FakeTelephony
    from tests.test_api import TW_BASE, fast_providers

    tel = FakeTelephony(auth_token="tw-test-token")
    tel.live = True
    s = settings_for_tests(
        telephony_enabled=True, twilio_account_sid="AC", twilio_auth_token="tw-test-token",
        twilio_phone_number="+15550001111", twilio_webhook_base_url=TW_BASE,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
    )  # fmt: skip
    app = create_app(s, providers=fast_providers(tel))
    with TestClient(app) as c:
        for i in range(300):
            form = {"CallSid": f"X{i}"}
            sig = compute_twilio_signature("tw-test-token", TW_BASE + "/telephony/twilio/voice", form)
            c.post("/telephony/twilio/voice", data=form, headers={"x-twilio-signature": sig})
        assert len(app.state.app_state.pending_calls) <= 100
        form = {"CallSid": "CAgood"}
        sig = compute_twilio_signature("tw-test-token", TW_BASE + "/telephony/twilio/voice", form)
        twiml = c.post("/telephony/twilio/voice", data=form, headers={"x-twilio-signature": sig}).text
        sid = re.search(r"name='session_id' value=\"([^\"]+)\"", twiml)[1]
        with c.websocket_connect("/telephony/twilio/media") as ws:  # wrong token must not consume the entry
            ws.send_text(json.dumps({"event": "start", "start": {"streamSid": "M", "customParameters":
                                     {"session_id": sid, "token": "bad"}}}))  # fmt: skip
            time.sleep(0.2)
        assert sid in app.state.app_state.pending_calls


# ---------------------------------------------------------------- 2. yes to a non-read-back question
def test_yes_to_no_discount_question_does_not_confirm():
    c, _ = make_controller("A")
    verify(c)
    say(c, "30,000 yen in two weeks")
    say(c, "can you give me a discount?")
    say(c, "yes")
    assert c.state.promise is None and c.state.promise_status != PromiseStatus.CONFIRMED


def test_unclear_during_confirmation_then_yes_after_readback_repeat_confirms():
    c, _ = make_controller("A")
    verify(c)
    say(c, "30,000 yen in two weeks")
    say(c, "hmm")  # clarify + read-back repeated
    say(c, "yes")
    assert c.state.promise_status == PromiseStatus.CONFIRMED


# ---------------------------------------------------------------- 3. output guard coverage
def _plans():
    c, _ = make_controller("A")
    unverified = c.start().plan
    verify(c)
    verified = say(c, "30,000 yen in two weeks").plan
    return unverified, verified


def test_guard_blocks_unapproved_money_forms():
    unverified, verified = _plans()
    assert not guard("You have an unpaid bill of fifty thousand yen that is past due.", unverified).ok
    assert not guard("Just 5,000 yen would do.", verified).ok
    assert not guard("Pay JPY 7,000 please.", verified).ok
    assert not guard("Pay ¥30,000 by 3/31.", verified).ok
    assert guard(verified.render(), verified).ok


async def test_llm_realizer_rejects_numbers_not_in_template():
    class Liar(MockLLM):
        async def stream_text(self, system, user, *, timeout):
            yield "Sure, pay twenty thousand yen and we're done."

    _, verified = _plans()
    r = await LLMRealizer(Liar(), time.monotonic, 2).realize(verified)
    assert r.source == "template_fallback"


# ---------------------------------------------------------------- 4. LLM affirm vs rules deny
def test_llm_affirm_cannot_override_rules_deny():
    llm = Interpretation(actions=[ProposedAction(action=Action.AFFIRM)], source="llm")
    rules = interpret("no, that is wrong", Language.EN, T)
    merged, _ = merge(llm, rules)
    assert not merged.has(Action.AFFIRM) and merged.has(Action.DENY)


# ---------------------------------------------------------------- 5/6. typed input edge cases
async def test_typing_over_final_goodbye_does_not_crash():
    sess, tr, clk, _ = make_session("E")
    await sess.start(run_background=False)
    await drain(sess, clk)
    await sess.on_text("stop calling me")
    for _ in range(10):
        await clk.advance_async(0.05)
    await sess.on_text("wait, one more thing")
    await drain(sess, clk)
    assert sess.state == VoiceState.ENDED
    assert sess.c.state.stop_contact
    assert not sess.errors
    t = sess._process_task
    assert t is None or t.cancelled() or t.exception() is None, repr(t.exception())


async def test_queued_texts_are_not_dropped():
    sess, tr, clk, _ = make_session("A")
    await sess.start(run_background=False)
    await drain(sess, clk)
    sess._applied = True
    sess.lifecycle.to(VoiceState.PROCESSING, "test")
    await sess.on_text("first extra")
    await sess.on_text("second extra")
    assert "first extra" in (sess._pending_text or "") and "second extra" in (sess._pending_text or "")


# ---------------------------------------------------------------- 7. TTS failure must not trap the caller
async def test_readback_rerequests_count_toward_transfer():
    c, _ = make_controller("A")
    verify(c)
    say(c, "30,000 yen in two weeks", delivered=0.0)
    for _ in range(3):
        out = say(c, "yes", delivered=0.0)
    assert c.state.human_transfer_requested, out.plan.acts


async def test_text_only_delivery_counts_in_text_mode():
    sess, tr, clk, prov = make_session("A")

    async def broken(*a, **k):
        raise RuntimeError("tts down")
        yield b""  # pragma: no cover

    await sess.start(run_background=False)
    await drain(sess, clk)
    for line in ["yes", "April 12, 1988", "30,000 yen in two weeks"]:
        await sess.on_text(line)
        await drain(sess, clk)
    prov.tts.synthesize = broken
    await sess.on_text("I can pay 30,000 yen in two weeks")
    await drain(sess, clk)
    await sess.on_text("yes")
    await drain(sess, clk)
    assert sess.c.state.promise_status == PromiseStatus.CONFIRMED


# ---------------------------------------------------------------- 8. transfer status preserved
async def test_transfer_status_not_overwritten_by_disconnect():
    sess, tr, clk, _ = make_session("G")
    await sess.start(run_background=False)
    await drain(sess, clk)
    await sess.on_text("let me talk to a human")
    await drain(sess, clk)
    assert sess.c.state.call_status == CallStatus.TRANSFER_REQUESTED
    assert sess.c.state.ended_reason == "transferred_to_human"


def test_controller_ignores_turns_after_transfer():
    c, _ = make_controller("G")
    verify(c)
    say(c, "operator please")
    before = c.state.snapshot()
    say(c, "I can pay 30,000 yen tomorrow")
    assert c.state.proposed_amount is None and c.state.snapshot()["call_status"] == before["call_status"]


# ---------------------------------------------------------------- 9. late STT final
async def test_late_final_does_not_leak_into_next_turn():
    sess, tr, clk, _ = make_session("A", input_mode="voice")
    await sess.start(run_background=False)
    await drain(sess, clk)
    from app.providers.base import STTEvent, STTEventType

    # a FINAL that arrives while nobody is speaking must be discarded
    assert sess.state == VoiceState.LISTENING
    stream = sess._stt_stream
    await stream._q.put(STTEvent(STTEventType.FINAL, text="no", at=clk.monotonic()))
    await clk.advance_async(0.1)
    assert sess._turn_text == []


# ---------------------------------------------------------------- notification recorded before end
async def test_notification_emitted_before_session_end():
    sess, tr, clk, prov = make_session("A")
    await sess.start(run_background=False)
    await drain(sess, clk)
    for line in ["yes", "April 12, 1988", "30,000 yen in two weeks", "yes"]:
        await sess.on_text(line)
        await drain(sess, clk)
    await sess.end("done")
    types = [e["type"] for e in tr.events]
    assert "notification" in types and types.index("notification") < types.index("session.ended")


# ---------------------------------------------------------------- lower-severity hardening
def test_rate_limit_uses_proxy_appended_address():
    from starlette.requests import Request

    from app.api.routes import client_ip

    scope = {"type": "http", "headers": [(b"x-forwarded-for", b"1.2.3.4, 203.0.113.9")], "client": ("10.0.0.1", 1)}
    # the left-most entry is client-controlled; the right-most is appended by the platform proxy
    assert client_ip(Request(scope)) == "203.0.113.9"


def test_phone_session_detail_requires_operator(tmp_path):
    import asyncio
    import uuid
    from datetime import UTC, datetime

    s = settings_for_tests(operator_token="op", database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db")
    app = create_app(s, build_providers(s))
    with TestClient(app) as c:
        repo = app.state.app_state.repo
        sid = uuid.uuid4()

        async def mk():
            _, a = await repo.get_account("A")
            await repo.create_session(session_id=sid, account=a, scenario_key="A", channel="phone", language="ja",
                                      input_mode="voice", providers={}, state={"call_status": "IN_PROGRESS",
                                      "identity_status": "UNVERIFIED", "promise_status": "NONE"},
                                      started_at=datetime.now(UTC), retention_days=30, call_id="CAx")  # fmt: skip

        c.portal.call(mk) if hasattr(c, "portal") else asyncio.run(mk())
        assert c.get(f"/api/sessions/{sid}").status_code == 401
        assert c.get(f"/api/sessions/{sid}", headers={"Authorization": "Bearer op"}).status_code == 200
