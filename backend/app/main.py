"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api import routes, telephony, ws_browser
from .config import Settings, get_settings
from .domain.clock import Clock, SystemClock
from .observability import configure_logging, correlation_id, metrics
from .persistence.db import init_schema, make_engine
from .persistence.repository import Repository
from .providers.factory import build_providers
from .runtime import Providers
from .state import AppState

log = logging.getLogger(__name__)
MAX_BODY_BYTES = 64 * 1024


def create_app(
    settings: Settings | None = None, providers: Providers | None = None, policy_clock: Clock | None = None
) -> FastAPI:
    """`providers` / `policy_clock` are injection points for tests."""
    s = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if s.app_env != "test":
            configure_logging(s.log_level)
        engine = make_engine(s.database_url)
        db_mode = await init_schema(engine, s.database_url, s.auto_migrate)
        repo = Repository(engine)
        if s.seed_demo_data:
            await repo.seed_demo_data()
        provs = providers or build_providers(s)
        state = AppState(
            settings=s, repo=repo, providers=provs, db_mode=db_mode, policy_clock=policy_clock or SystemClock()
        )
        app.state.app_state = state
        purge = asyncio.create_task(_retention_loop(repo))
        log.info("startup", extra={"providers": provs.labels, "db_mode": db_mode, "env": s.app_env})
        try:
            yield
        finally:
            # Graceful shutdown: stop accepting sessions, end live ones cleanly.
            state.draining = True
            purge.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await purge
            for sess in list(state.sessions.values()):
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(sess.end("server_shutdown"), 5)
            # Let transport handlers flush their recorders before the engine goes away.
            for _ in range(100):
                if not state.sessions and not state.closing:
                    break
                await asyncio.sleep(0.05)
            for p in (provs.llm, provs.tts, provs.telephony):
                closer = getattr(p, "aclose", None)
                if closer:
                    with contextlib.suppress(Exception):
                        await closer()
            await engine.dispose()

    app = FastAPI(title="AI Voice Collections Agent (portfolio POC)", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        correlation_id.set(rid[:64])
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "payload too large"}, status_code=413)
        try:
            response = await call_next(request)
        except Exception:
            metrics.inc("http_errors", {"path": request.url.path[:40]})
            log.exception("unhandled_error")
            return JSONResponse({"detail": "internal error", "request_id": rid}, status_code=500)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        metrics.inc("http_requests", {"status": str(response.status_code)[0] + "xx"})
        return response

    app.include_router(routes.router)
    app.include_router(ws_browser.router)
    app.include_router(telephony.router)
    from .api import evaluation as eval_routes

    app.include_router(eval_routes.router)
    return app


async def _retention_loop(repo: Repository) -> None:
    while True:
        try:
            n = await repo.purge_expired_transcripts()
            if n:
                log.info("transcripts_purged", extra={"rows": n})
        except Exception:
            log.exception("retention_purge_failed")
        await asyncio.sleep(6 * 3600)


def _app_factory() -> FastAPI:
    return create_app()
