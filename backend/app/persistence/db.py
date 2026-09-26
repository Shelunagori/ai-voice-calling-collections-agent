"""Engine creation and schema management."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from ..config import Settings
from .tables import metadata

log = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parents[2]


def make_engine(url: str) -> AsyncEngine:
    if url.startswith("sqlite") and ":memory:" in url:
        return create_async_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    if url.startswith("sqlite"):
        return create_async_engine(url, connect_args={"check_same_thread": False})
    return create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, pool_recycle=1800)


def _alembic_upgrade(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")


async def init_schema(engine: AsyncEngine, url: str, auto_migrate: bool) -> str:
    """In-memory SQLite (tests) uses create_all; everything else runs Alembic migrations."""
    if url.startswith("sqlite") and ":memory:" in url:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        return "create_all"
    if not auto_migrate:
        return "skipped"
    # Alembic's env.py drives its own event loop, so run it in a worker thread.
    await asyncio.to_thread(_alembic_upgrade, url)
    return "alembic_upgrade_head"


# ----------------------------------------------------------------------------------
# production persistence guard
# ----------------------------------------------------------------------------------

PRODUCTION_ENVS = {"production", "staging"}
_RAILWAY_MARKERS = ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID")


class PersistenceConfigError(RuntimeError):
    """The configured database cannot safely be used in this environment."""


def running_on_railway(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return any(env.get(k) for k in _RAILWAY_MARKERS)


def check_persistence(s: Settings, environ: Mapping[str, str] | None = None) -> None:
    """Refuse to run production/staging (APP_ENV, or Railway as a safety net) on a database
    that does not survive a redeploy. Messages never include the URL or credentials."""
    production_like = s.app_env in PRODUCTION_ENVS or running_on_railway(environ)
    if not production_like or s.database_durable:
        return
    where = f"APP_ENV={s.app_env}" + (" on Railway" if running_on_railway(environ) else "")
    if s.allow_ephemeral_database:
        log.critical(
            "EPHEMERAL DATABASE IN USE: ALLOW_EPHEMERAL_DATABASE=true with a %s database (%s). "
            "Sessions, audit trail, promises and stop-contact flags WILL BE LOST on restart/redeploy. "
            "Emergency/dev escape hatch only - attach PostgreSQL and set DATABASE_URL.",
            s.database_backend,
            where,
        )
        return
    raise PersistenceConfigError(
        f"Refusing to start: {where} requires a durable PostgreSQL database, but the configured database "
        f"is {s.database_backend} (DATABASE_URL is missing or empty). Attach PostgreSQL and set "
        "DATABASE_URL (Railway: DATABASE_URL=${{Postgres.DATABASE_URL}}). "
        "ALLOW_EPHEMERAL_DATABASE=true overrides this for emergencies only; data would be lost on redeploy."
    )


def head_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    return head or ""


async def current_revision(engine: AsyncEngine) -> str | None:
    """Alembic revision recorded in the database, or None (no alembic_version table)."""
    async with engine.connect() as conn:

        def read(sync_conn: sa.Connection) -> str | None:
            if not sa.inspect(sync_conn).has_table("alembic_version"):
                return None
            return sync_conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()

        return await conn.run_sync(read)


async def verify_schema(engine: AsyncEngine, s: Settings, mode: str) -> str | None:
    """With AUTO_MIGRATE=false in production, the schema must already be at head."""
    rev = await current_revision(engine)
    if mode == "skipped" and s.app_env in PRODUCTION_ENVS and rev != head_revision():
        raise PersistenceConfigError(
            f"Refusing to start: AUTO_MIGRATE=false and the database schema is at revision {rev or '(none)'}, "
            f"not head {head_revision()}. Run `alembic upgrade head` (backend directory, same DATABASE_URL) "
            "or set AUTO_MIGRATE=true."
        )
    return rev
