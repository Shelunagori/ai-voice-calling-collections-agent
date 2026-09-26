"""Persistence tests. SQLite file DB always; PostgreSQL when TEST_DATABASE_URL is set (CI)."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.domain.scenarios import SCENARIOS
from app.persistence.db import init_schema, make_engine
from app.persistence.repository import Repository

PG = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(params=["sqlite", "postgres"])
async def repo(request, tmp_path):
    if request.param == "postgres":
        if not PG:
            pytest.skip("TEST_DATABASE_URL not set")
        url = Settings(database_url=PG).database_url
        engine = make_engine(url)
        import sqlalchemy as sa

        async with engine.begin() as conn:  # clean slate
            from app.persistence.tables import metadata

            await conn.run_sync(metadata.drop_all)
            await conn.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
        await engine.dispose()
    else:
        url = f"sqlite+aiosqlite:///{tmp_path}/p.db"
    engine = make_engine(url)
    mode = await init_schema(engine, url, auto_migrate=True)
    assert mode == "alembic_upgrade_head"
    r = Repository(engine)
    yield r
    await engine.dispose()


async def test_seed_is_idempotent_and_reset(repo):
    assert await repo.seed_demo_data() == len(SCENARIOS)
    assert await repo.seed_demo_data() == 0
    d, a = await repo.get_account("A")
    assert d.full_name == "Haruto Sato" and a.outstanding_balance == 80000
    await repo.set_stop_contact(a.account_id, datetime.now(UTC))
    assert {x["scenario_key"]: x for x in await repo.list_accounts()}["A"]["stop_contact"]
    await repo.seed_demo_data(reset=True)
    assert not {x["scenario_key"]: x for x in await repo.list_accounts()}["A"]["stop_contact"]


async def test_promise_unique_per_session_and_retention(repo):
    await repo.seed_demo_data()
    _, a = await repo.get_account("A")
    sid = uuid.uuid4()
    started = datetime.now(UTC) - timedelta(days=40)
    await repo.create_session(
        session_id=sid,
        account=a,
        scenario_key="A",
        channel="browser",
        language="en",
        input_mode="text",
        providers={"llm": "mock"},
        state={"call_status": "IN_PROGRESS", "identity_status": "VERIFIED", "promise_status": "NONE"},
        started_at=started,
        retention_days=30,
    )
    data = {
        "promise_id": str(uuid.uuid4()),
        "amount": 30000,
        "currency": "JPY",
        "due_date": "2026-10-15",
        "confirmation_turn": 4,
        "policy_decision_ids": [],
    }
    assert await repo.insert_promise(sid, a.account_id, data, datetime.now(UTC)) is True
    data2 = {**data, "promise_id": str(uuid.uuid4())}
    assert await repo.insert_promise(sid, a.account_id, data2, datetime.now(UTC)) is False  # DB-level idempotency
    await repo.insert_turn(sid, {"index": 0, "text": "secret words", "turn_index": 1}, "caller", started)
    assert await repo.purge_expired_transcripts() == 1
    detail = await repo.get_session_detail(sid)
    assert detail["turns"][0]["text"] is None and detail["promise"]["amount"] == 30000


async def test_webhook_ledger_dedupes(repo):
    assert await repo.record_webhook_once("CA1:completed:1", "CA1", "status", {}) is True
    assert await repo.record_webhook_once("CA1:completed:1", "CA1", "status", {}) is False


def test_migration_matches_models(tmp_path):
    """`alembic check`: the migration history produces exactly the declared schema."""
    from alembic import command
    from alembic.config import Config

    from app.persistence.db import BACKEND_DIR, _alembic_upgrade

    url = f"sqlite+aiosqlite:///{tmp_path}/m.db"
    _alembic_upgrade(url)
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.check(cfg)
