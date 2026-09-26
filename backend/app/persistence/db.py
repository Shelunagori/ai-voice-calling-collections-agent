"""Engine creation and schema management."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

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
