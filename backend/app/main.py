from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import (
    routes_analysis,
    routes_market,
    routes_notifications,
    routes_planning,
    routes_strategy_d,
    routes_research,
    routes_signals,
    routes_system,
    routes_tools,
    routes_validation,
)
from app.api.deps import ContainerDep
from app.api.hub import hub
from app.config import Settings, get_settings
from app.container import Container, build_container
from app.db.session import dispose_engine, get_engine, init_engine, session_factory
from app.logging_setup import configure_logging
from app.settings_store import RuntimeSettings, settings_store
from app.timeutil import TIMEFRAMES, utcnow
from app.workers.leader import LeaderLock

log = logging.getLogger("app")

TICKER_FALLBACK_INTERVAL = 5.0
LEADER_RETRY_SECONDS = 30.0


async def _leader_loop(c: Container) -> None:
    """Acquire scanner leadership (retrying) and run the scanner while leader."""
    assert c.leader is not None
    while not await c.leader.try_acquire():
        c.scanner.status["is_leader"] = False
        await asyncio.sleep(LEADER_RETRY_SECONDS)
        with contextlib.suppress(Exception):
            await c.settings.refresh()
    c.scanner.status["is_leader"] = True
    await c.scanner.run()


async def start_background(c: Container) -> None:
    env = c.env
    await c.market.sync_clock()
    try:
        await c.forward.startup()
    except Exception:  # noqa: BLE001 - retried on the first candle evaluation
        log.exception("could not resume open paper trades at startup")
    settings = c.settings.current
    await c.market.poll_tickers(settings.watchlist)
    if env.market_stream_enabled:
        c.tasks.append(c.market.start_stream(env.bybit_ws_url, settings.watchlist, list(TIMEFRAMES)))
        c.tasks.append(asyncio.create_task(
            c.market.ticker_fallback_loop(lambda: c.settings.current.watchlist, TICKER_FALLBACK_INTERVAL),
            name="ticker-fallback"))
    if env.scanner_enabled:
        c.leader = LeaderLock(get_engine())
        c.tasks.append(asyncio.create_task(_leader_loop(c), name="scanner"))
        await c.notifications.sync_cursor()
        c.tasks.append(asyncio.create_task(c.notifications.run(), name="notification-outbox"))
        c.tasks.append(asyncio.create_task(c.health.run(), name="health-monitor"))
    c.health.tasks = c.tasks

    async def on_settings_change(new: RuntimeSettings) -> None:
        c.market.update_subscriptions(new.watchlist, list(TIMEFRAMES))
        c.scanner.invalidate()
        await c.hub.broadcast("settings", new.model_dump(mode="json"))

    c.settings.subscribe(on_settings_change)


async def stop_background(c: Container) -> None:
    c.market.stop_stream()
    for task in c.tasks:
        task.cancel()
    for task in c.tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    await c.backtests.shutdown()
    if c.leader is not None:
        with contextlib.suppress(Exception):
            await c.leader.release()
    await c.client.close()
    if c.telegram is not None:
        await c.telegram.close()


def create_app(env: Settings | None = None, container_factory=None, run_background: bool = True) -> FastAPI:
    env = env or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(env.log_level, env.log_json)
        init_engine(env.database_url)
        await settings_store.load()
        factory = container_factory or build_container
        c = factory(env, settings_store, hub, session_factory)
        app.state.container = c
        app.state.started_at = utcnow()
        c.started_at, c.api_latency_ms = app.state.started_at, app.state.api_latency_ms
        log.info("starting CryptoSignal AI", extra={"env": env.app_env, "telegram": env.telegram_configured,
                                                    "db": "postgresql" if env.is_postgres else "sqlite"})
        if run_background:
            await start_background(c)
        try:
            yield
        finally:
            log.info("shutting down")
            await stop_background(c)
            await dispose_engine()

    app = FastAPI(
        title="CryptoSignal AI",
        version="1.0.0",
        description=("Crypto futures market analysis and research signals (Bybit USDT perpetuals). "
                     "Analysis only: this API never places orders."),
        lifespan=lifespan,
    )
    latencies: deque[float] = deque(maxlen=1000)
    app.state.api_latency_ms = latencies
    allowed_origins = set(env.cors_origin_list)

    @app.middleware("http")
    async def guard_and_measure(request: Request, call_next):
        """Inside CORS, so that its headers also reach error responses. The API has no login and
        listens on localhost, so without the origin check any website open in the browser could
        trigger actions here (CORS stops a page reading responses, not sending the request)."""
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin not in allowed_origins:
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 - reported as an API error, not as "backend unreachable"
            log.exception("unhandled error", extra={"path": request.url.path})
            return JSONResponse({"detail": "internal server error"}, status_code=500)
        if request.url.path != "/api/system/health":
            latencies.append((time.perf_counter() - started) * 1000)
        return response

    app.add_middleware(CORSMiddleware, allow_origins=env.cors_origin_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type"])
    app.include_router(routes_market.router)
    app.include_router(routes_signals.router)
    app.include_router(routes_tools.router)
    app.include_router(routes_analysis.router)
    app.include_router(routes_research.router)
    app.include_router(routes_planning.router)
    app.include_router(routes_validation.router)
    app.include_router(routes_strategy_d.router)
    app.include_router(routes_notifications.router)
    app.include_router(routes_system.router)

    @app.exception_handler(Exception)
    async def unhandled(_, exc: Exception) -> JSONResponse:  # pragma: no cover - safety net
        log.exception("unhandled error")
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    @app.get("/api/health", tags=["health"], summary="Liveness probe")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "time": utcnow().isoformat()}

    @app.get("/api/health/ready", tags=["health"], summary="Readiness: database, market data and scanner")
    async def ready(c: ContainerDep) -> JSONResponse:
        checks: dict[str, Any] = {}
        try:
            async with session_factory()() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["database"] = f"error: {exc}"
        wl = c.settings.current.watchlist
        stale = [s for s in wl if c.market.state.is_stale(s)]
        checks["market_data"] = "ok" if not stale else f"stale: {', '.join(stale)}"
        checks["websocket"] = "connected" if c.market.state.ws_connected else "disconnected"
        checks["scanner"] = {"running": c.scanner.status["running"], "is_leader": c.scanner.status["is_leader"],
                             "last_cycle_at": c.scanner.status["last_cycle_at"],
                             "last_error": c.scanner.status["last_error"]}
        checks["telegram"] = "configured" if c.env.telegram_configured else "not configured"
        ok = checks["database"] == "ok"
        return JSONResponse({"status": "ready" if ok else "degraded", "checks": checks},
                            status_code=200 if ok else 503)

    @app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        if origin and origin not in allowed_origins:
            await ws.close(code=1008)
            return
        await hub.connect(ws)
        c: Container = ws.app.state.container
        try:
            await ws.send_json({"event": "hello", "ts": utcnow().isoformat(), "data": {
                "connection": c.market.state.connection_view(),
                "tickers": [c.market.state.ticker_view(s) for s in c.settings.current.watchlist],
                "scanner": c.scanner.public_status(),
                "notifications": await c.notifications.unread_count()}})
            while True:
                msg = await ws.receive_text()
                if msg == "ping":
                    await ws.send_json({"event": "pong", "ts": utcnow().isoformat(), "data": None})
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(ws)

    return app


app = create_app()
