"""Stop-contact scope (real PSTN finding, 2026-09-26).

Observed: after "Please don't call me again" on a Scenario E phone call, a Scenario A
call to the same number was still dialled, because stop-contact lived only on E's
collection account.

Scope now (owner's ruling): debtor -> all of that debtor's accounts, plus the
*contact point* (the dialled number) where a real person asked us to stop. A later
outbound call is blocked if the account, its debtor, or the destination has an active
stop-contact request. Numbers are stored only as a salted hash + masked label.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import settings_for_tests
from app.domain.clock import FakeClock
from app.domain.contact import contact_key, mask_number
from app.domain.policy import Decision, PolicyEngine, Rule
from app.domain.scenarios import SCENARIOS
from app.main import create_app
from app.providers.mock import FakeTelephony
from tests.test_api import TW_BASE, agent_done, db_url, fast_providers, recv_until, signed_post

X = "+819012345678"  # the reviewer's allowlisted test number (synthetic here)
Y = "+819012340000"  # a second, unrelated allowlisted number
OP = {"Authorization": "Bearer op-secret"}
STOP_REASON = "debtor/contact has an active stop-contact request"


@pytest.fixture
def tc(tmp_path):
    tel = FakeTelephony(auth_token="tw-test-token")
    tel.live = True
    s = settings_for_tests(
        operator_token="op-secret",
        telephony_enabled=True,
        twilio_account_sid="ACtest",
        twilio_auth_token="tw-test-token",
        twilio_phone_number="+15550001111",
        twilio_webhook_base_url=TW_BASE,
        demo_call_allowed_numbers=f"{X},{Y}",
        rate_limit_calls_per_hour=100,
        database_url=db_url(tmp_path),
    )
    clock = FakeClock(datetime(2026, 10, 1, 1, 0, tzinfo=UTC))  # 10:00 JST
    app = create_app(s, providers=fast_providers(tel), policy_clock=clock)
    with TestClient(app) as c:
        c.tel = tel
        c.clock = clock
        yield c


def call(c, scenario: str, to: str = X) -> dict:
    r = c.post("/api/operator/calls", json={"to": to, "scenario": scenario, "language": "en"}, headers=OP)
    assert r.status_code == 200, r.text
    return r.json()


def stop_decision(body: dict) -> dict:
    return next(d for d in body["decisions"] if d["rule"] == Rule.STOP_CONTACT_ACTIVE.value)


def phone_call_saying_stop(c, scenario: str, to: str = X) -> str:
    """Place a real (fake-Twilio) outbound call, connect the media stream, and have the
    person who answers say "Please don't call me again." Returns the session id."""
    started = call(c, scenario, to)
    assert started["status"] == "dialing"
    sid = started["session_id"]
    twiml = signed_post(c, f"/telephony/twilio/voice?session_id={sid}", {"CallSid": started["call_id"]}).text
    token = re.search(r"name='token' value=\"([^\"]+)\"", twiml)[1]
    with c.websocket_connect("/telephony/twilio/media") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(
            json.dumps(
                {
                    "event": "start",
                    "start": {
                        "streamSid": "MZ1",
                        "callSid": started["call_id"],
                        "customParameters": {"session_id": sid, "token": token},
                    },
                }
            )
        )
        ws.receive_text()  # first agent audio: the session is live
        sess = c.app.state.app_state.sessions[__import__("uuid").UUID(sid)]
        c.portal.call(sess.on_text, "Please don't call me again.")
        for _ in range(100):
            if sess.lifecycle.terminal:
                break
            time.sleep(0.05)
        ws.send_text(json.dumps({"event": "stop"}))
    for _ in range(100):  # the recorder writes asynchronously
        acc = {a["scenario_key"]: a for a in c.get("/api/accounts").json()}
        if acc[scenario]["stop_contact"]:
            break
        time.sleep(0.05)
    d = c.get(f"/api/sessions/{sid}", headers=OP).json()
    assert d["session"]["stop_contact"] is True and d["session"]["ended_reason"] == "stop_contact_requested"
    return sid


# ------------------------------------------------------------------ outbound gating
def test_stop_in_E_blocks_every_scenario_to_the_same_contact_and_never_dials(tc):
    phone_call_saying_stop(tc, "E")
    dialled = len(tc.tel.calls)
    for key in sorted(SCENARIOS):  # E again, A, B, ... G
        body = call(tc, key, X)
        assert body["status"] == "blocked_by_policy", key
        d = stop_decision(body)
        assert d["decision"] == Decision.BLOCK.value and d["reason"].startswith(STOP_REASON), (key, d)
        assert "contact_point" in d["details"]["scopes"]
    assert len(tc.tel.calls) == dialled  # Twilio create-call never invoked while blocked


def test_debtor_scope_blocks_that_debtor_on_any_number(tc):
    phone_call_saying_stop(tc, "E")
    body = call(tc, "E", Y)  # different number, same debtor
    d = stop_decision(body)
    assert body["status"] == "blocked_by_policy" and d["decision"] == "BLOCK"
    assert d["details"]["scopes"] == ["account", "debtor"]


def test_unrelated_debtor_on_another_number_is_not_blocked(tc):
    phone_call_saying_stop(tc, "E")
    n = len(tc.tel.calls)
    body = call(tc, "A", Y)
    assert body["status"] == "dialing" and stop_decision(body)["decision"] == "ALLOW"
    assert len(tc.tel.calls) == n + 1


def test_blocked_decisions_are_audited_before_any_dial(tc):
    phone_call_saying_stop(tc, "E")
    body = call(tc, "A", X)
    assert [d["rule"] for d in body["decisions"]] == [
        Rule.CONTACT_WINDOW.value,
        Rule.MAX_CONTACT_ATTEMPTS.value,
        Rule.STOP_CONTACT_ACTIVE.value,
    ]
    assert "session_id" not in body  # nothing was created on the Twilio side


def test_eligibility_view_reflects_every_affected_account_and_the_contact_point(tc):
    phone_call_saying_stop(tc, "E")
    acc = {a["scenario_key"]: a for a in tc.get("/api/accounts").json()}
    assert acc["E"]["stop_contact"] and acc["E"]["debtor_stop_contact"] and not acc["E"]["eligible"]
    assert acc["A"]["eligible"] and not acc["A"]["debtor_stop_contact"]  # A is another synthetic debtor
    cps = tc.get("/api/contact-points").json()
    assert len(cps) == 1 and cps[0]["stop_contact"] is True
    assert cps[0]["label"] == mask_number(X) and X not in json.dumps(cps)
    # reset restores everything
    assert tc.post("/api/operator/reset-demo", headers=OP).status_code == 200
    assert tc.get("/api/contact-points").json() == []
    assert call(tc, "A", X)["status"] == "dialing"


def test_browser_demo_stop_contact_flags_the_debtor(tc):
    with tc.websocket_connect("/ws/session?scenario=E&lang=en&mode=text") as ws:
        recv_until(ws, agent_done)
        ws.send_text(json.dumps({"type": "text", "text": "Please don't call me again."}))
        recv_until(ws, lambda e: e["type"] == "session.ended")
    for _ in range(100):
        acc = {a["scenario_key"]: a for a in tc.get("/api/accounts").json()}
        if acc["E"]["debtor_stop_contact"]:
            break
        time.sleep(0.05)
    assert acc["E"]["debtor_stop_contact"] and not acc["E"]["eligible"]
    assert tc.get("/api/contact-points").json() == []  # browser sessions have no contact point


# ------------------------------------------------------------------ other rules unchanged
def test_max_attempts_and_calling_hours_still_apply(tc):
    for _ in range(3):
        assert call(tc, "A", Y)["status"] == "dialing"
    body = call(tc, "A", Y)
    assert body["status"] == "blocked_by_policy"
    assert {d["rule"]: d["decision"] for d in body["decisions"]}[Rule.MAX_CONTACT_ATTEMPTS.value] == "BLOCK"
    tc.clock.set(datetime(2026, 10, 1, 14, 0, tzinfo=UTC))  # 23:00 JST
    body = call(tc, "B", Y)
    assert {d["rule"]: d["decision"] for d in body["decisions"]}[Rule.CONTACT_WINDOW.value] == "BLOCK"


# ------------------------------------------------------------------ units
def test_policy_scopes():
    acc = SCENARIOS["A"].account
    now = datetime(2026, 10, 1, 1, 0, tzinfo=UTC)
    pe = PolicyEngine()

    def stop(**kw):
        return next(d for d in pe.evaluate_contact(acc, now, **kw) if d.rule == Rule.STOP_CONTACT_ACTIVE)

    assert stop().decision == Decision.ALLOW
    for kw, scopes in (
        ({"debtor_stop_contact": True}, ["debtor"]),
        ({"contact_point_stop_contact": True}, ["contact_point"]),
    ):
        d = stop(**kw)
        assert d.decision == Decision.BLOCK and d.details["scopes"] == scopes and d.reason.startswith(STOP_REASON)
    d = next(x for x in pe.evaluate_contact(replace(acc, stop_contact=True), now) if x.rule == Rule.STOP_CONTACT_ACTIVE)
    assert d.decision == Decision.BLOCK and d.details["scopes"] == ["account"]


def test_contact_key_is_stable_salted_and_not_the_number():
    k = contact_key(X)
    assert k == contact_key(X) and k != contact_key(Y) and X not in k and len(k) == 64
    assert mask_number(X) == "+81•••••••678"


def test_migration_0002_backfills_debtor_flag_from_accounts(tmp_path):
    """Existing deployments: an account already flagged (e.g. Scenario E on Railway)
    becomes a debtor-level stop after upgrading. Contact points cannot be backfilled."""
    import sqlite3

    from alembic import command
    from alembic.config import Config

    from app.persistence.db import BACKEND_DIR

    path = tmp_path / "m.db"
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(cfg, "0001")
    e, a = SCENARIOS["E"], SCENARIOS["A"]
    con = sqlite3.connect(path)
    for sc, stopped in ((e, 1), (a, 0)):
        con.execute(
            "INSERT INTO debtors (id, full_name, display_name_ja, name_aliases, date_of_birth, phone_e164,"
            " preferred_language, timezone, synthetic) VALUES (?, ?, '', '[]', '1990-01-01', '+81', 'ja', 'Asia/Tokyo', 1)",
            (sc.debtor.debtor_id.hex, sc.debtor.full_name),
        )
        con.execute(
            "INSERT INTO collection_accounts (id, debtor_id, scenario_key, creditor_name, outstanding_balance, currency,"
            " allowed_min_payment, max_extension_days, discount_authority, contact_attempts, stop_contact, stop_contact_at)"
            " VALUES (?, ?, ?, 'x', 1, 'JPY', 1, 1, 0, 0, ?, ?)",
            (
                sc.account.account_id.hex,
                sc.debtor.debtor_id.hex,
                sc.key,
                stopped,
                "2026-09-26 16:00:00" if stopped else None,
            ),
        )
    con.commit()
    con.close()
    command.upgrade(cfg, "head")
    con = sqlite3.connect(path)
    rows = dict(con.execute("SELECT full_name, stop_contact FROM debtors").fetchall())
    assert rows == {e.debtor.full_name: 1, a.debtor.full_name: 0}
    assert con.execute("SELECT COUNT(*) FROM contact_points").fetchone()[0] == 0
    con.close()
