from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import asc, desc, func, select

from app.api.deps import ContainerDep, Timeframe
from app.api.routes_market import load_snapshots
from app.db.models import Notification, Signal
from app.db.session import session_factory
from app.signals.evaluation import METHODOLOGY
from app.signals.service import signal_to_dict

router = APIRouter(prefix="/api", tags=["signals"])


def _setup_summary(ev: dict[str, Any]) -> dict[str, Any] | None:
    setups = ev.get("setups") or []
    if not setups:
        return None
    top = setups[0]
    reasons = top.get("reasons") or []
    return {"direction": top["direction"], "strategy": top["strategy"], "status": top["status"],
            "score": top.get("score"), "reason": reasons[0]["message"] if reasons else None}


def _nearest(zones: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    for z in zones:
        if z["zone_type"] == kind and z["status"] != "INVALIDATED":
            return {"lower": z["lower"], "upper": z["upper"], "tests": z["tests"], "status": z["status"]}
    return None


@router.get("/scanner", summary="Scanner table: latest analysis for every watchlisted symbol/timeframe")
async def scanner(
    c: ContainerDep,
    symbol: str | None = Query(None, max_length=32),
    timeframe: Timeframe | None = None,
    trend: Literal["BULLISH", "BEARISH", "NEUTRAL"] | None = None,
    signal_status: str | None = Query(None, max_length=32),
) -> dict[str, Any]:
    settings = c.settings.current
    snaps = await load_snapshots(timeframe)
    async with session_factory()() as session:
        active = (await session.execute(select(Signal).where(Signal.evaluation_status == "PENDING"))).scalars().all()
    active_by_key: dict[tuple[str, str], list[dict]] = {}
    for s in active:
        active_by_key.setdefault((s.symbol, s.timeframe), []).append({"id": s.id, "direction": s.direction,
                                                                       "score": s.score})
    rows = []
    for snap in snaps:
        if snap["symbol"] not in settings.watchlist or snap["timeframe"] not in settings.timeframes:
            continue
        ev = snap["evaluation"]
        ind = ev.get("indicators", {})
        row = {
            "symbol": snap["symbol"],
            "timeframe": snap["timeframe"],
            "ticker": c.market.state.ticker_view(snap["symbol"]),
            "close": ind.get("close"),
            "trend": ev.get("trend"),
            "rsi": ind.get("rsi"),
            "macd_state": ev.get("macd_state"),
            "macd_hist": ind.get("macd_hist"),
            "ema_alignment": ev.get("ema_alignment"),
            "structure": ev.get("structure"),
            "long_passed": ev.get("long_passed"),
            "short_passed": ev.get("short_passed"),
            "signal_status": snap["signal_status"],
            "setup": _setup_summary(ev),
            "nearest_support": _nearest(ev.get("zones") or [], "SUPPORT"),
            "nearest_resistance": _nearest(ev.get("zones") or [], "RESISTANCE"),
            "active_signals": active_by_key.get((snap["symbol"], snap["timeframe"]), []),
            "candle_close_time": snap["candle_close_time"],
            "updated_at": snap["updated_at"],
            "data_quality": snap.get("data_quality"),
        }
        if symbol and symbol.upper() not in row["symbol"]:
            continue
        if trend and row["trend"] != trend:
            continue
        if signal_status and row["signal_status"] != signal_status:
            continue
        rows.append(row)
    rows.sort(key=lambda r: (r["symbol"], r["timeframe"]))
    return {"rows": rows, "status": c.scanner.public_status()}


@router.get("/scanner/status", summary="Background scanner status")
async def scanner_status(c: ContainerDep) -> dict[str, Any]:
    return c.scanner.public_status()


SORTS = {"created_at": Signal.created_at, "candle_close_time": Signal.candle_close_time, "score": Signal.score,
         "symbol": Signal.symbol}


@router.get("/signals", summary="List signals with filtering, sorting and pagination")
async def list_signals(
    status: Literal["active", "closed", "all"] = "all",
    symbol: str | None = Query(None, pattern=r"^[A-Z0-9]{2,20}USDT$"),
    timeframe: Timeframe | None = None,
    direction: Literal["LONG", "SHORT"] | None = None,
    evaluation_status: str | None = Query(None, max_length=24),
    min_score: int = Query(0, ge=0, le=100),
    sort: Literal["created_at", "candle_close_time", "score", "symbol"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    q = select(Signal)
    if status == "active":
        q = q.where(Signal.evaluation_status == "PENDING")
    elif status == "closed":
        q = q.where(Signal.evaluation_status != "PENDING")
    if symbol:
        q = q.where(Signal.symbol == symbol)
    if timeframe:
        q = q.where(Signal.timeframe == timeframe)
    if direction:
        q = q.where(Signal.direction == direction)
    if evaluation_status:
        q = q.where(Signal.evaluation_status == evaluation_status)
    if min_score:
        q = q.where(Signal.score >= min_score)
    async with session_factory()() as session:
        total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
        col = SORTS[sort]
        q = q.order_by(desc(col) if order == "desc" else asc(col), desc(Signal.created_at)).limit(limit).offset(offset)
        items = (await session.execute(q)).scalars().all()
    return {"total": total, "items": [signal_to_dict(s) for s in items]}


@router.get("/signals/stats", summary="Signal counts and hypothetical outcome statistics")
async def signal_stats() -> dict[str, Any]:
    async with session_factory()() as session:
        rows = (await session.execute(select(Signal.direction, Signal.evaluation_status, Signal.evaluation,
                                             Signal.notification_status))).all()
    by_eval: dict[str, int] = {}
    by_notif: dict[str, int] = {}
    rs: list[float] = []
    for _, ev_status, evaluation, notif in rows:
        by_eval[ev_status] = by_eval.get(ev_status, 0) + 1
        by_notif[notif] = by_notif.get(notif, 0) + 1
        if evaluation and evaluation.get("r_multiple") is not None:
            rs.append(evaluation["r_multiple"])
    hits = by_eval.get("TARGET_HIT", 0)
    stops = by_eval.get("STOP_HIT", 0) + by_eval.get("AMBIGUOUS_STOP", 0)
    return {
        "total": len(rows),
        "active": by_eval.get("PENDING", 0),
        "long": sum(1 for r in rows if r[0] == "LONG"),
        "short": sum(1 for r in rows if r[0] == "SHORT"),
        "by_evaluation_status": by_eval,
        "by_notification_status": by_notif,
        "hypothetical": {
            "label": "Hypothetical outcomes of live signals (not realized trades)",
            "methodology": METHODOLOGY,
            "resolved": len(rs),
            "target_hits": hits,
            "stop_hits": stops,
            "hit_rate_pct": hits / (hits + stops) * 100 if hits + stops else None,
            "avg_r_multiple": sum(rs) / len(rs) if rs else None,
        },
    }


@router.get("/signals/{signal_id}", summary="Signal detail")
async def get_signal(signal_id: str) -> dict[str, Any]:
    async with session_factory()() as session:
        sig = await session.get(Signal, signal_id)
        if sig is None:
            raise HTTPException(404, "signal not found")
        notes = (await session.execute(select(Notification).where(Notification.signal_id == signal_id)
                                       .order_by(Notification.created_at))).scalars().all()
    return {**signal_to_dict(sig), "notifications": [notification_to_dict(n) for n in notes]}


def notification_to_dict(n: Notification) -> dict[str, Any]:
    return {"id": n.id, "signal_id": n.signal_id, "channel": n.channel, "kind": n.kind, "status": n.status,
            "attempts": n.attempts, "error": n.error, "message": n.message, "created_at": n.created_at.isoformat(),
            "sent_at": n.sent_at.isoformat() if n.sent_at else None}


@router.get("/notifications", summary="Notification delivery history")
async def notifications(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    async with session_factory()() as session:
        total = (await session.execute(select(func.count(Notification.id)))).scalar_one()
        items = (await session.execute(select(Notification).order_by(desc(Notification.created_at))
                                       .limit(limit).offset(offset))).scalars().all()
    return {"total": total, "items": [notification_to_dict(n) for n in items]}
