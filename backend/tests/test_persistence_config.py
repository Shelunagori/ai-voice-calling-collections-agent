"""Production persistence safety (Railway finding, 2026-09-27).

Observed: the deployed backend had an empty DATABASE_URL, so it silently used the
default SQLite file inside the container and lost sessions/audit on every redeploy.

Now: PostgreSQL whenever DATABASE_URL is set; in production/staging (APP_ENV, with Railway
detection as a safety net) a non-durable database refuses to start unless the explicit
ALLOW_EPHEMERAL_DATABASE escape hatch is set (logged CRITICAL). /ready reports backend,
durability and migration revision, never the URL, host or credentials.
"""

from __future__ import annotations

import json
import logging
import os

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.config import Settings, settings_for_tests
from app.main import create_app
from app.persistence.db import (
    PersistenceConfigError,
    check_persistence,
    head_revision,
    make_engine,
    running_on_railway,
)
from tests.test_api import agent_done, fast_providers, recv_until

PG = os.environ.get("TEST_DATABASE_URL")
needs_pg = pytest.mark.skipif(not PG, reason="TEST_DATABASE_URL not set")


# ------------------------------------------------------------------ URL handling
def test_railway_style_urls_use_asyncpg_and_translate_sslmode():
    s = Settings(database_url="postgresql://u:p@db.internal:5432/railway?sslmode=require")
    assert s.database_url == "postgresql+asyncpg://u:p@db.internal:5432/railway?ssl=require"
    assert Settings(database_url="postgres://u:p@h/db").database_url.startswith("postgresql+asyncpg://")
    s = Settings(database_url="postgresql://u:p@h/db")
    assert s.database_backend == "postgresql" and s.database_durable
    assert make_engine(s.database_url).dialect.driver == "asyncpg"


def test_local_default_is_sqlite_and_not_durable(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    s = Settings(_env_file=None)
    assert s.database_backend == "sqlite" and not s.database_durable
    assert s.allow_ephemeral_database is False  # never on by default


def test_empty_database_url_env_falls_back_to_sqlite(monkeypatch):
    # The exact Railway situation: the variable exists but is empty.
    monkeypatch.setenv("DATABASE_URL", "")
    assert Settings(_env_file=None).database_backend == "sqlite"


# ------------------------------------------------------------------ production guard
@pytest.mark.parametrize("env", ["production", "staging"])
def test_production_without_postgres_refuses_to_start(env):
    s = settings_for_tests(app_env=env)  # in-memory SQLite
    with pytest.raises(PersistenceConfigError, match="DATABASE_URL"):
        check_persistence(s, environ={})
    app = create_app(s, providers=fast_providers())
    with pytest.raises(PersistenceConfigError):
        with TestClient(app):
            pass


def test_railway_detection_is_a_safety_net_even_if_app_env_is_wrong():
    s = settings_for_tests(app_env="local")
    assert running_on_railway({"RAILWAY_ENVIRONMENT_NAME": "production"})
    with pytest.raises(PersistenceConfigError):
        check_persistence(s, environ={"RAILWAY_ENVIRONMENT_NAME": "production"})
    check_persistence(s, environ={})  # plain local run is fine


def test_escape_hatch_allows_start_with_critical_log(caplog):
    s = settings_for_tests(app_env="production", allow_ephemeral_database=True)
    with caplog.at_level(logging.CRITICAL):
        check_persistence(s, environ={})
    assert any(r.levelno == logging.CRITICAL and "ALLOW_EPHEMERAL_DATABASE" in r.getMessage() for r in caplog.records)


def test_local_and_test_modes_unchanged():
    check_persistence(settings_for_tests(), environ={})
    check_persistence(settings_for_tests(app_env="local"), environ={})


# ------------------------------------------------------------------ /ready
def test_ready_reports_backend_without_leaking_connection_details():
    with TestClient(create_app(settings_for_tests(), providers=fast_providers())) as c:
        db = c.get("/ready").json()["dependencies"]["database"]
    assert db["backend"] == "sqlite" and db["durable"] is False and "revision" in db
    assert set(db) <= {"status", "mode", "backend", "durable", "revision"}


@needs_pg
def test_ready_on_postgres_is_durable_and_at_head(tmp_path):
    s = settings_for_tests(database_url=PG, app_env="production")
    _reset_pg(s.database_url)
    with TestClient(create_app(s, providers=fast_providers())) as c:
        body = c.get("/ready").json()
    db = body["dependencies"]["database"]
    assert db == {
        "status": "ok",
        "mode": "alembic_upgrade_head",
        "backend": "postgresql",
        "durable": True,
        "revision": head_revision(),
    }
    dumped = json.dumps(body)
    url = sa.engine.make_url(s.database_url)
    for secret in (s.database_url, url.host, url.password, url.username):
        assert secret and secret not in dumped


# ------------------------------------------------------------------ AUTO_MIGRATE
@needs_pg
def test_auto_migrate_off_in_production_requires_schema_at_head():
    s = settings_for_tests(database_url=PG, app_env="production", auto_migrate=False)
    _reset_pg(s.database_url)
    with pytest.raises(PersistenceConfigError, match="alembic upgrade head"):
        with TestClient(create_app(s, providers=fast_providers())):
            pass
    with TestClient(create_app(settings_for_tests(database_url=PG, app_env="production"), providers=fast_providers())):
        pass  # migrates to head
    with TestClient(create_app(s, providers=fast_providers())) as c:  # now at head: starts
        assert c.get("/ready").json()["dependencies"]["database"]["mode"] == "skipped"


# ------------------------------------------------------------------ durability
@needs_pg
def test_sessions_and_audit_survive_a_backend_restart():
    s = settings_for_tests(database_url=PG, app_env="production")
    _reset_pg(s.database_url)
    with TestClient(create_app(s, providers=fast_providers())) as c:
        with c.websocket_connect("/ws/session?scenario=E&lang=en&mode=text") as ws:
            created = recv_until(ws, lambda e: e["type"] == "session.created")
            recv_until(ws, agent_done)
            ws.send_text(json.dumps({"type": "text", "text": "Please don't call me again."}))
            recv_until(ws, lambda e: e["type"] == "session.ended")
        sid = created["session_id"]
    # "redeploy": a brand-new process/app on the same database
    with TestClient(create_app(s, providers=fast_providers())) as c2:
        d = c2.get(f"/api/sessions/{sid}").json()
        acc = {a["scenario_key"]: a for a in c2.get("/api/accounts").json()}
    assert d["session"]["ended_reason"] == "stop_contact_requested"
    assert any(e["type"] == "stop_contact.requested" for e in d["audit"])
    assert d["turns"] and d["latency"]
    assert acc["E"]["debtor_stop_contact"] is True  # seed on restart does not reset flags


def _reset_pg(url: str) -> None:
    import asyncio

    from app.persistence.tables import metadata

    async def go() -> None:
        engine = make_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(metadata.drop_all)
            await conn.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
        await engine.dispose()

    asyncio.run(go())
