"""System Health API. Every status comes from a heartbeat or a data-freshness check: a component
is never HEALTHY merely because the process is running, and UNKNOWN means it could not be verified."""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import ContainerDep
from app.db.models import NotificationDelivery, PaperTrade, Signal
from app.db.session import session_factory
from app.logging_setup import recent_problems
from app.timeutil import TIMEFRAMES, utcnow

router = APIRouter(prefix="/api/system", tags=["system"])

STATUS_RANK = {"HEALTHY": 0, "UNKNOWN": 1, "DEGRADED": 2, "DOWN": 3}
WS_FRESH = timedelta(seconds=60)
TELEGRAM_VERIFIABLE = timedelta(hours=24)


def _component(key: str, name: str, status: str, detail: str, **metrics: Any) -> dict[str, Any]:
    return {"key": key, "name": name, "status": status, "detail": detail, "metrics": metrics}


def _age(iso_or_dt: str | datetime | None, now: datetime) -> timedelta | None:
    if iso_or_dt is None:
        return None
    return now - (datetime.fromisoformat(iso_or_dt) if isinstance(iso_or_dt, str) else iso_or_dt)


def _s(delta: timedelta | None) -> str:
    if delta is None:
        return "never"
    sec = int(delta.total_seconds())
    return f"{sec}s ago" if sec < 120 else f"{sec // 60}m ago" if sec < 7200 else f"{sec // 3600}h ago"


def _iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else v


def database(c, now) -> dict[str, Any]:
    p = c.health.probes
    age = _age(p.db_checked_at, now)
    if p.db_ok is None or age is None or age > timedelta(minutes=3):
        return _component("database", "Database", "UNKNOWN", "No recent write check: the health monitor is not running.", latency_ms=p.db_latency_ms)
    if not p.db_ok:
        return _component("database", "Database", "DOWN", f"Write check failed: {p.db_error}", latency_ms=None)
    slow = (p.db_latency_ms or 0) > 1000
    return _component("database", "Database", "DEGRADED" if slow else "HEALTHY", f"Write check OK {_s(age)}" + (", but slow" if slow else ""), latency_ms=p.db_latency_ms)


def bybit_rest(c, now) -> dict[str, Any]:
    p = c.health.probes
    age = _age(p.rest_checked_at, now)
    if p.rest_ok is None or age is None or age > timedelta(minutes=5):
        return _component("bybit_rest", "Bybit REST API", "UNKNOWN", "No recent request to verify it.")
    if not p.rest_ok:
        status = "DOWN" if p.rest_failures >= 3 else "DEGRADED"
        return _component("bybit_rest", "Bybit REST API", status, f"{p.rest_failures} failed check(s): {p.rest_error}", last_ok=_iso(p.rest_last_ok_at))
    return _component("bybit_rest", "Bybit REST API", "HEALTHY", f"Answered {_s(age)}", latency_ms=p.rest_latency_ms, clock_skew_s=p.clock_skew_s, last_ok=_iso(p.rest_last_ok_at))


def bybit_ws(c, now) -> dict[str, Any]:
    if not c.env.market_stream_enabled:
        return _component("bybit_ws", "Bybit WebSocket", "UNKNOWN", "The stream is disabled in this configuration.")
    s = c.market.state
    age = _age(s.ws_last_message_at, now)
    metrics = {"reconnects": s.ws_reconnects, "last_message": _iso(s.ws_last_message_at), "rest_fallback": s.rest_fallback_active}
    if not s.ws_connected:
        return _component("bybit_ws", "Bybit WebSocket", "DOWN", f"Disconnected; last data {_s(age)}. {str(s.last_error or '')[:150]}", **metrics)
    if age is None or age > WS_FRESH:
        return _component("bybit_ws", "Bybit WebSocket", "DEGRADED", f"Connected but last data {_s(age)}.", **metrics)
    return _component("bybit_ws", "Bybit WebSocket", "HEALTHY", f"Last data {_s(age)}", **metrics)


def market_data(c, now) -> dict[str, Any]:
    s, wl = c.market.state, c.settings.current.watchlist
    stale = [x for x in wl if s.is_stale(x, now)]
    times = [s.tickers[x].updated_at for x in wl if x in s.tickers]
    newest = max(times) if times else None
    metrics = {"coins": len(wl), "stale_coins": stale, "last_update": _iso(newest)}
    if not times:
        return _component("market_data", "Market data freshness", "UNKNOWN", "No price received since the app started.", **metrics)
    if len(stale) == len(wl):
        return _component("market_data", "Market data freshness", "DOWN", f"Every price is stale; newest {_s(_age(newest, now))}.", **metrics)
    if stale:
        return _component("market_data", "Market data freshness", "DEGRADED", f"Stale: {', '.join(stale)}", **metrics)
    return _component("market_data", "Market data freshness", "HEALTHY", f"All {len(wl)} coins fresh; newest {_s(_age(newest, now))}", **metrics)


def scanner(c, now) -> dict[str, Any]:
    st = c.scanner.status
    metrics = {"last_cycle": st.get("last_cycle_at"), "cycle_ms": st.get("last_cycle_ms"), "cycles": st.get("cycles"), "errors": st.get("errors"), "cycle_errors": st.get("cycle_errors", 0)}
    if not c.env.scanner_enabled:
        return _component("scanner", "Market scanner", "UNKNOWN", "The scanner is disabled in this configuration.", **metrics)
    if not st.get("running"):
        return _component("scanner", "Market scanner", "DOWN", "The scanner task is not running.", **metrics)
    if not st.get("is_leader"):
        return _component("scanner", "Market scanner", "UNKNOWN", "Waiting for scanner leadership.", **metrics)
    age = _age(st.get("last_cycle_at"), now)
    if age is None:
        return _component("scanner", "Market scanner", "UNKNOWN", "Started; no cycle has completed yet.", **metrics)
    limit = timedelta(seconds=max(180, c.scanner.interval * 5))
    if age > limit:
        return _component("scanner", "Market scanner", "DOWN", f"Last completed cycle {_s(age)}.", **metrics)
    if st.get("cycle_errors") or st.get("market_errors"):
        return _component("scanner", "Market scanner", "DEGRADED", f"Running, but failing on some coins: {str(st.get('last_error') or 'market data unavailable')[:200]}", **metrics)
    if st.get("series_behind"):
        return _component("scanner", "Market scanner", "DEGRADED", f"Running, but {st['series_behind']} candle series could not refresh, so new candles are not being seen.", **metrics)
    return _component("scanner", "Market scanner", "HEALTHY", f"Last completed cycle {_s(age)}", **metrics)


def signal_engine(c, now, last_signal) -> dict[str, Any]:
    st = c.scanner.status
    metrics = {"evaluations": st.get("evaluations"), "last_candle": st.get("last_candle"), "last_evaluated": st.get("last_evaluated_at"), "last_signal": _iso(last_signal), "signals_this_run": st.get("signals_created")}
    tfs = c.settings.current.timeframes
    shortest = timedelta(seconds=min(TIMEFRAMES[t] for t in tfs)) if tfs else None
    age = _age(st.get("last_evaluated_at"), now)
    if age is None or shortest is None:
        return _component("signal_engine", "Signal engine", "UNKNOWN", "No candle has closed since the app started, so it is not verified yet.", **metrics)
    if age > shortest * 3:
        return _component("signal_engine", "Signal engine", "DOWN", f"Last candle evaluated {_s(age)}; expected every {int(shortest.total_seconds() // 60)} minutes.", **metrics)
    if age > shortest * 1.5:
        return _component("signal_engine", "Signal engine", "DEGRADED", f"Last candle evaluated {_s(age)}.", **metrics)
    return _component("signal_engine", "Signal engine", "HEALTHY", f"Last candle evaluated {_s(age)}", **metrics)


def paper_worker(c, now, last_event) -> dict[str, Any]:
    f = c.forward
    metrics = {"open_trades": len(f.open), "records_this_run": f.status.get("records"), "last_evaluation": f.status.get("last_evaluation_at"), "last_event": f.status.get("last_event_at") or _iso(last_event)}
    if not f.vs.enabled:
        return _component("paper_worker", "Paper-trading worker", "UNKNOWN", "Forward validation is switched off in Settings.", **metrics)
    if f.status.get("last_error"):
        return _component("paper_worker", "Paper-trading worker", "DOWN", f"Last run failed: {str(f.status['last_error'])[:200]}", **metrics)
    age = _age(f.status.get("last_evaluation_at"), now)
    if age is None:
        return _component("paper_worker", "Paper-trading worker", "UNKNOWN", "No candle processed since the app started.", **metrics)
    if age > timedelta(minutes=45):
        return _component("paper_worker", "Paper-trading worker", "DEGRADED", f"Last processed a candle {_s(age)}.", **metrics)
    return _component("paper_worker", "Paper-trading worker", "HEALTHY", f"Processed a candle {_s(age)}; {len(f.open)} open", **metrics)


def telegram(c, now, last_sent, pending, failed_after_sent, retrying=0) -> dict[str, Any]:
    metrics = {"last_delivery": _iso(last_sent), "pending": pending, "retrying": retrying, "failed_since_last_delivery": failed_after_sent}
    if not c.env.telegram_configured:
        return _component("telegram", "Telegram delivery", "UNKNOWN", "Telegram is not configured.", **metrics)
    if not c.settings.current.alerts.telegram_enabled:
        return _component("telegram", "Telegram delivery", "UNKNOWN", "Telegram alerts are switched off in Settings.", **metrics)
    if c.health.probes.backlog:
        return _component("telegram", "Telegram delivery", "DOWN", f"{c.health.probes.backlog} message(s) stuck for over 10 minutes.", **metrics)
    if retrying or failed_after_sent or pending > 5:
        return _component("telegram", "Telegram delivery", "DEGRADED", f"{retrying} failing and being retried, {failed_after_sent} given up; {pending} waiting.", **metrics)
    age = _age(last_sent, now)
    if age is None or age > TELEGRAM_VERIFIABLE:
        return _component("telegram", "Telegram delivery", "UNKNOWN", f"Nothing to verify it with: last delivery {_s(age)}. No failures recorded.", **metrics)
    return _component("telegram", "Telegram delivery", "HEALTHY", f"Last delivered {_s(age)}", **metrics)


def notifications(c, now) -> dict[str, Any]:
    n = c.notifications
    metrics = {"heartbeat": _iso(n.heartbeat_at), "buffered": n.stats.get("buffered"), "store_failures": n.stats.get("store_failures"), "malformed": n.stats.get("malformed")}
    age = _age(n.heartbeat_at, now)
    if age is None:
        return _component("notifications", "Notification service", "UNKNOWN", "The worker has not reported yet.", **metrics)
    if age > timedelta(minutes=2):
        return _component("notifications", "Notification service", "DOWN", f"Worker last ran {_s(age)}.", **metrics)
    if n.stats.get("buffered"):
        return _component("notifications", "Notification service", "DEGRADED", f"{n.stats['buffered']} notification(s) waiting for the database.", **metrics)
    return _component("notifications", "Notification service", "HEALTHY", f"Worker ran {_s(age)}", **metrics)


def monitor(c, now) -> dict[str, Any]:
    age = _age(c.health.heartbeat_at, now)
    if age is None:
        return _component("monitor", "Health monitor", "UNKNOWN", "Has not completed a check yet.")
    if age > timedelta(minutes=3):
        return _component("monitor", "Health monitor", "DOWN", f"Last check {_s(age)}: the statuses above may be out of date.")
    return _component("monitor", "Health monitor", "HEALTHY", f"Last check {_s(age)}")


@router.get("/health", summary="Operational status from heartbeats and data-freshness checks")
async def health(c: ContainerDep) -> dict[str, Any]:
    now = utcnow()
    started = time.perf_counter()
    db_error = None
    last_signal = last_event = last_sent = None
    pending = failed = retrying = 0
    try:
        async with session_factory()() as session:
            last_signal = (await session.execute(select(func.max(Signal.created_at)))).scalar()
            last_event = (await session.execute(select(func.max(PaperTrade.updated_at)))).scalar()
            last_sent = (await session.execute(select(func.max(NotificationDelivery.sent_at)))).scalar()
            pending = (await session.execute(select(func.count()).select_from(NotificationDelivery).where(
                NotificationDelivery.status.in_(("PENDING", "RETRY", "SENDING"))))).scalar_one()
            retrying = (await session.execute(select(func.count()).select_from(NotificationDelivery).where(
                NotificationDelivery.status == "RETRY"))).scalar_one()
            q = select(func.count()).select_from(NotificationDelivery).where(NotificationDelivery.status == "FAILED")
            if last_sent is not None:
                q = q.where(NotificationDelivery.updated_at > last_sent)
            failed = (await session.execute(q)).scalar_one()
    except Exception as exc:  # noqa: BLE001 - this page must still answer when the database does not
        db_error = str(exc)[:200]
    read_ms = round((time.perf_counter() - started) * 1000, 1)

    def safe(fn: Callable[..., dict[str, Any]], key: str, name: str, *args: Any) -> dict[str, Any]:
        try:
            return fn(c, now, *args)
        except Exception as exc:  # noqa: BLE001 - a bad check reports UNKNOWN, never a false HEALTHY
            return _component(key, name, "UNKNOWN", f"Check failed: {exc}"[:200])

    latencies = sorted(getattr(c, "api_latency_ms", None) or [])
    components = [
        safe(database, "database", "Database"), safe(bybit_rest, "bybit_rest", "Bybit REST API"), safe(bybit_ws, "bybit_ws", "Bybit WebSocket"),
        safe(market_data, "market_data", "Market data freshness"), safe(scanner, "scanner", "Market scanner"),
        safe(signal_engine, "signal_engine", "Signal engine", last_signal), safe(paper_worker, "paper_worker", "Paper-trading worker", last_event),
        safe(telegram, "telegram", "Telegram delivery", last_sent, pending, failed, retrying), safe(notifications, "notifications", "Notification service"),
        safe(monitor, "monitor", "Health monitor"),
    ]
    if db_error:
        for comp in components:
            if comp["key"] == "telegram":
                comp.update(status="UNKNOWN", detail="Delivery records cannot be read from the database.")
    incidents: list[dict[str, Any]] = []
    try:
        incidents = await c.notifications.incidents(None, 30)
    except Exception:  # noqa: BLE001
        pass
    worst = max((comp["status"] for comp in components), key=lambda s: STATUS_RANK[s])
    return {
        "generated_at": now.isoformat(), "overall": worst, "components": components, "started_at": _iso(c.started_at),
        "uptime_seconds": round((now - c.started_at).total_seconds()) if c.started_at else None,
        "api_latency_ms": {"samples": len(latencies), "p50": round(statistics.median(latencies), 1) if latencies else None,
                           "p95": round(latencies[int(len(latencies) * 0.95) - 1], 1) if len(latencies) >= 20 else None},
        "database_read_ms": read_ms, "database_error": db_error,
        "last_signal": _iso(last_signal), "last_paper_event": _iso(last_event), "last_telegram_delivery": _iso(last_sent),
        "last_candle": c.scanner.status.get("last_candle"), "scanner_heartbeat": c.scanner.status.get("last_cycle_at"),
        "recent_problems": list(recent_problems.items)[-50:][::-1], "incidents": incidents, "pauses": c.health.probes.pauses[::-1],
        "decision_mode": c.settings.current.decision.mode, "real_money_trading": False,
    }
