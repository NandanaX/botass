from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.ai.explainer import ExplanationContext, get_explainer
from app.api.deps import ContainerDep, SymbolPath, Timeframe
from app.db.models import AnalysisSnapshot, Signal
from app.db.session import session_factory
from app.exchanges.bybit import ExchangeError
from app.jsonutil import jsonable
from app.market.service import CandleSeries
from app.risk.sizing import InstrumentRules, SizingConfig
from app.signals.service import signal_to_dict
from app.strategy.engine import Evaluation, evaluate_at
from app.strategy.features import Features, compute_features
from app.timeutil import TIMEFRAMES
from app.workers.scanner import levels_from_zones

router = APIRouter(prefix="/api/market", tags=["market"])


async def load_snapshots(timeframe: str | None = None) -> list[dict[str, Any]]:
    async with session_factory()() as session:
        q = select(AnalysisSnapshot)
        if timeframe:
            q = q.where(AnalysisSnapshot.timeframe == timeframe)
        return [r.data for r in (await session.execute(q)).scalars().all()]


@router.get("/tickers", summary="Live tickers for the watchlist")
async def tickers(c: ContainerDep) -> dict[str, Any]:
    wl = c.settings.current.watchlist
    return {"tickers": [c.market.state.ticker_view(s) for s in wl], "connection": c.market.state.connection_view()}


@router.get("/overview", summary="Dashboard overview: tickers + latest analysis per watchlisted symbol")
async def overview(c: ContainerDep, timeframe: Timeframe = "15m") -> dict[str, Any]:
    wl = c.settings.current.watchlist
    snaps = {s["symbol"]: s for s in await load_snapshots(timeframe)}
    return {
        "timeframe": timeframe,
        "markets": [{"ticker": c.market.state.ticker_view(s), "analysis": snaps.get(s)} for s in wl],
        "connection": c.market.state.connection_view(),
        "scanner": c.scanner.public_status(),
    }


async def analyze(c, symbol: str, timeframe: str, sizing: SizingConfig | None = None
                  ) -> tuple[CandleSeries, Features, InstrumentRules | None, Evaluation]:
    """On-demand analysis of the latest closed candle with the live scanner's code path:
    closed candles only, higher timeframes aligned to the decision time, current
    instrument rules and the configured (or overridden) sizing assumptions."""
    params = c.settings.current.strategy
    try:
        series = await c.market.get_closed_candles(symbol, timeframe)
    except ExchangeError as exc:
        raise HTTPException(503, f"market data unavailable: {exc}") from exc
    if series.df.empty:
        raise HTTPException(404, "no candles available")
    f = await asyncio.to_thread(compute_features, series.df, timeframe, params)
    htf: dict[str, Features] = {}
    wanted = set(params.zones.htf_timeframes) | (set(params.mtf.timeframes) if params.mtf.enabled else set())
    for tf in sorted(wanted, key=lambda t: TIMEFRAMES[t]):
        if TIMEFRAMES[tf] <= TIMEFRAMES[timeframe]:
            continue
        try:
            hs = await c.market.get_closed_candles(symbol, tf)
        except ExchangeError:
            continue  # analysis still works; HTF zones/trend are then reported as unavailable
        if not hs.df.empty:
            htf[tf] = await asyncio.to_thread(compute_features, hs.df, tf, params)
    try:
        rules = await c.market.get_instrument_rules(symbol)
    except ExchangeError:
        rules = None
    ev = await asyncio.to_thread(evaluate_at, f, len(f) - 1, params, htf, sizing or c.settings.current.risk.sizing(),
                                 rules, True, symbol)
    return series, f, rules, ev


def _swings(f: Features, lo: int) -> list[dict[str, Any]]:
    """Confirmed swing pivots from bar ``lo`` onwards (with the bar that confirmed them)."""
    n = len(f)
    pv = f.pivots
    out = []
    for idx, conf, price, is_high in zip(pv.index, pv.confirmed_at, pv.price, pv.is_high, strict=True):
        if idx >= lo and conf <= n - 1:
            out.append({"time": int(f.open_time[idx] // 1_000_000_000), "price": float(price),
                        "kind": "high" if is_high else "low",
                        "confirmed_time": int(f.open_time[conf] // 1_000_000_000)})
    return out


@router.get("/{symbol}/candles", summary="Closed candles with EMA overlays, S/R zones, swings and signal markers")
async def candles(c: ContainerDep, symbol: SymbolPath, timeframe: Timeframe = "15m",
                  limit: int = Query(500, ge=50, le=1000)) -> dict[str, Any]:
    series, f, _rules, ev = await analyze(c, symbol, timeframe)
    params = c.settings.current.strategy
    n = len(f)
    lo = max(0, n - limit)
    times = (f.open_time[lo:] // 1_000_000_000).astype(int).tolist()

    def line(arr: np.ndarray) -> list[dict[str, float]]:
        return [{"time": t, "value": float(v)} for t, v in zip(times, arr[lo:], strict=True) if np.isfinite(v)]

    async with session_factory()() as session:
        sigs = (await session.execute(
            select(Signal).where(Signal.symbol == symbol, Signal.timeframe == timeframe,
                                 Signal.candle_open_time >= series.df["open_time"].iloc[lo].to_pydatetime())
            .order_by(Signal.candle_open_time))).scalars().all()
    live = c.market.live_candle(symbol, timeframe)
    if live and not live.get("confirmed") and live["open_time"] > series.df["open_time"].iloc[-1]:
        live = {**live, "time": int(live["open_time"].timestamp()), "open_time": live["open_time"].isoformat()}
    else:
        live = None
    return jsonable({
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": [
            {"time": t, "open": float(o), "high": float(h), "low": float(lw), "close": float(cl), "volume": float(v)}
            for t, o, h, lw, cl, v in zip(times, f.open[lo:], f.high[lo:], f.low[lo:], f.close[lo:], f.volume[lo:],
                                          strict=True)
        ],
        "live_candle": live,
        "overlays": {
            f"ema{params.ema_fast}": line(f.ema_fast),
            f"ema{params.ema_mid}": line(f.ema_mid),
            f"ema{params.ema_slow}": line(f.ema_slow),
        },
        "zones": ev.zones,
        "htf_zones": ev.htf_zones,
        "swings": _swings(f, lo),
        "levels": levels_from_zones(ev.zones),
        "signals": [signal_to_dict(s) for s in sigs],
        "analysis": ev.to_dict(),
        "data_quality": {"current": series.is_current,
                         "gaps": [[a.isoformat(), b.isoformat()] for a, b in series.gaps],
                         "refreshed_at": series.refreshed_at.isoformat() if series.refreshed_at else None},
        "ticker": c.market.state.ticker_view(symbol),
    })


def _nearest_zone(zones: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    cands = [z for z in zones if z["zone_type"] == kind and z["status"] != "INVALIDATED"]
    return cands[0] if cands else None


@router.get("/{symbol}/setup", summary="Trade-setup analysis: zones, candidate setups, stop/targets and sizing")
async def setup(
    c: ContainerDep, symbol: SymbolPath, timeframe: Timeframe = "15m",
    account_balance: float | None = Query(None, gt=0, le=1e9),
    risk_pct: float | None = Query(None, gt=0, le=10),
    leverage: float | None = Query(None, ge=1, le=200),
) -> dict[str, Any]:
    base = c.settings.current.risk
    if leverage is not None and leverage > base.max_leverage:
        raise HTTPException(422, f"leverage {leverage:g}x exceeds the configured maximum {base.max_leverage:g}x")
    overrides = {"account_balance": account_balance, "risk_pct": risk_pct, "leverage": leverage}
    sizing = base.sizing().model_copy(update={k: v for k, v in overrides.items() if v is not None})
    series, f, rules, ev = await analyze(c, symbol, timeframe, sizing)
    d = ev.to_dict()
    zones = ev.zones
    htf_major = {tf: {"support": _nearest_zone(zs, "SUPPORT"), "resistance": _nearest_zone(zs, "RESISTANCE")}
                 for tf, zs in ev.htf_zones.items()}
    async with session_factory()() as session:
        active = (await session.execute(
            select(Signal).where(Signal.symbol == symbol, Signal.timeframe == timeframe,
                                 Signal.evaluation_status == "PENDING")
            .order_by(Signal.candle_open_time.desc()).limit(5))).scalars().all()
    if ev.setup is not None:
        decision = "QUALIFIED"
    elif any(st.status == "REJECTED" for st in ev.setups):
        decision = "REJECTED"
    elif ev.setups:
        decision = "WATCHLIST"
    else:
        decision = "NO_SETUP"
    return jsonable({
        "symbol": symbol,
        "timeframe": timeframe,
        "market": {
            "ticker": c.market.state.ticker_view(symbol),
            "candle_open_time": ev.open_time,
            "candle_close_time": ev.close_time,
            "close": ev.close,
            "trend": ev.trend,
            "structure": ev.structure,
            "ema_alignment": ev.ema_alignment,
            "macd_state": ev.macd_state,
            "ready": ev.ready,
            "not_ready_reason": ev.not_ready_reason,
            "indicators": ev.indicators,
            "observations": ev.observations,
            "data_current": series.is_current,
            "refreshed_at": series.refreshed_at.isoformat() if series.refreshed_at else None,
        },
        "structure": {
            "nearest_support": _nearest_zone(zones, "SUPPORT"),
            "nearest_resistance": _nearest_zone(zones, "RESISTANCE"),
            "htf": htf_major,
            "zones": zones,
            "htf_zones": ev.htf_zones,
            "swings": _swings(f, max(0, len(f) - 150))[-12:],
        },
        "setups": d["setups"],
        "primary_setup": d["setups"][0] if d["setups"] else None,
        "decision": decision,
        "decision_note": None if ev.setups else (
            "No breakout or pullback setup matches the configured rules on the last closed candle. "
            "The system does not force a trade."),
        "sizing_inputs": sizing.model_dump(),
        "instrument": rules.to_dict() if rules else None,
        "active_signals": [signal_to_dict(s) for s in active],
        "disclaimer": ("Research analysis from closed candles. Zones are technical classifications, not guarantees "
                       "that price will react. Nothing is executed; losses can exceed the planned risk."),
    })


@router.get("/{symbol}/analysis", summary="Latest scanner analysis for a symbol/timeframe")
async def analysis(c: ContainerDep, symbol: SymbolPath, timeframe: Timeframe = "15m") -> dict[str, Any]:
    async with session_factory()() as session:
        snap = await session.get(AnalysisSnapshot, (symbol, timeframe))
    if snap is None:
        raise HTTPException(404, "no analysis yet for this symbol/timeframe (is it in the watchlist and scanned?)")
    return snap.data


@router.get("/{symbol}/explanation", summary="Plain-English explanation of the latest analysis")
async def explanation(c: ContainerDep, symbol: SymbolPath, timeframe: Timeframe = "15m") -> dict[str, Any]:
    async with session_factory()() as session:
        snap = await session.get(AnalysisSnapshot, (symbol, timeframe))
        latest_signal = (await session.execute(
            select(Signal).where(Signal.symbol == symbol, Signal.timeframe == timeframe)
            .order_by(Signal.candle_open_time.desc()).limit(1))).scalar_one_or_none()
    if snap is None:
        raise HTTPException(404, "no analysis yet for this symbol/timeframe")
    ev = snap.data["evaluation"]
    if not ev.get("ready"):
        raise HTTPException(409, ev.get("not_ready_reason") or "analysis not ready")
    signal = None
    if latest_signal is not None and latest_signal.candle_open_time.isoformat().replace("+00:00", "Z") \
            == ev["open_time"]:
        signal = signal_to_dict(latest_signal)
    elif ev.get("setup"):
        signal = ev["setup"]
    ctx = ExplanationContext(
        symbol=symbol, timeframe=timeframe, candle_close_time=ev["close_time"], trend=ev["trend"],
        ema_alignment=ev["ema_alignment"], macd_state=ev["macd_state"], structure=ev["structure"],
        indicators=ev["indicators"], long_checks=ev["long_checks"], short_checks=ev["short_checks"],
        observations=ev["observations"], signal=signal, levels=snap.data.get("levels", []))
    result = await get_explainer().explain(ctx)
    return {"context": ctx.model_dump(), "explanation": result.model_dump()}


@router.get("/timeframes", summary="Supported timeframes")
async def timeframes() -> list[str]:
    return list(TIMEFRAMES)
