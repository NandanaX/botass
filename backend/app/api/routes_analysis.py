"""Technical analysis engine endpoints (Phase 4)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, select

from app.analysis.frame import build_frame
from app.analysis.validation import ABLATIONS, ablation, event_study
from app.api.deps import ContainerDep, SymbolPath, Timeframe
from app.db.models import PatternDetection
from app.db.session import session_factory
from app.exchanges.bybit import ExchangeError
from app.jsonutil import jsonable
from app.timeutil import TIMEFRAMES, tf_delta, utcnow

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def _err(exc: Exception) -> HTTPException:
    if isinstance(exc, ExchangeError):
        return HTTPException(503, f"market data unavailable: {exc}")
    return HTTPException(404, str(exc))


@router.get("/detections", summary="Stored confirmed detections (structure events, chart/candlestick patterns)")
async def detections(symbol: str | None = Query(None, pattern=r"^[A-Z0-9]{2,20}USDT$"), timeframe: Timeframe | None = None,
                     kind: Literal["structure", "chart_pattern", "candlestick"] | None = None,
                     limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
    async with session_factory()() as session:
        q = select(PatternDetection).order_by(desc(PatternDetection.candle_time)).limit(limit)
        if symbol:
            q = q.where(PatternDetection.symbol == symbol)
        if timeframe:
            q = q.where(PatternDetection.timeframe == timeframe)
        if kind:
            q = q.where(PatternDetection.kind == kind)
        rows = (await session.execute(q)).scalars().all()
    return [{"id": r.id, "symbol": r.symbol, "timeframe": r.timeframe, "kind": r.kind, "name": r.name,
             "direction": r.direction, "status": r.status, "candle_time": r.candle_time.isoformat(),
             "analysis_version": r.analysis_version, "strategy_version": r.strategy_version,
             "explanation": (r.data or {}).get("explanation") or (r.data or {}).get("label")} for r in rows]


@router.get("/{symbol}", summary="Full technical analysis of the latest closed candle")
async def analyze(c: ContainerDep, symbol: SymbolPath, timeframe: Timeframe = "1h",
                  futures: bool = True) -> dict[str, Any]:
    try:
        return await c.analysis.analyze(symbol, timeframe, futures)
    except (ExchangeError, LookupError) as exc:
        raise _err(exc) from exc


@router.get("/{symbol}/mtf", summary="Top-down multi-timeframe bias (1W to 5M)")
async def mtf(c: ContainerDep, symbol: SymbolPath) -> dict[str, Any]:
    return await c.analysis.mtf(symbol)


@router.get("/{symbol}/overlays", summary="Chart annotations: structure, patterns, trendlines, Fibonacci, FVGs")
async def overlays(c: ContainerDep, symbol: SymbolPath, timeframe: Timeframe = "1h",
                   limit: int = Query(500, ge=50, le=1000)) -> dict[str, Any]:
    try:
        return await c.analysis.overlays(symbol, timeframe, limit)
    except (ExchangeError, LookupError) as exc:
        raise _err(exc) from exc


@router.get("/{symbol}/report", summary="Human-readable technical analysis report (deterministic)")
async def report(c: ContainerDep, symbol: SymbolPath, timeframe: Timeframe = "1h") -> dict[str, Any]:
    try:
        return await c.analysis.report(symbol, timeframe)
    except (ExchangeError, LookupError) as exc:
        raise _err(exc) from exc


@router.get("/{symbol}/validation", summary="Event study (in/out-of-sample) and optional walk-forward ablation")
async def validation(c: ContainerDep, symbol: SymbolPath, timeframe: Timeframe = "1h",
                     days: int = Query(180, ge=7, le=1500), horizon: int = Query(10, ge=1, le=200),
                     include_ablation: bool = False, folds: int = Query(3, ge=2, le=6)) -> dict[str, Any]:
    s = c.settings.current
    end = utcnow()
    start = end - timedelta(days=days)
    bars = (end - start) / tf_delta(timeframe)
    if bars > 60_000:
        raise HTTPException(422, "range too large for this timeframe (max 60k candles)")
    warm = tf_delta(timeframe) * (max(s.strategy.warmup_bars, s.strategy.zones.lookback) + 50)
    try:
        df = await c.market.load_history(symbol, timeframe, start - warm, end)
    except ExchangeError as exc:
        raise _err(exc) from exc
    if df.empty:
        raise HTTPException(404, "no history")
    fr = await asyncio.to_thread(build_frame, df, timeframe, s.strategy, s.analysis)
    first = int((df["open_time"] < start).sum())
    out: dict[str, Any] = {"symbol": symbol, "event_study": await asyncio.to_thread(event_study, fr, horizon, 0.7, first)}
    if include_ablation:
        htf = {}
        for tf in sorted(set(s.strategy.zones.htf_timeframes), key=lambda t: TIMEFRAMES[t]):
            if TIMEFRAMES[tf] > TIMEFRAMES[timeframe]:
                htf[tf] = await c.market.load_history(symbol, tf, start - tf_delta(tf) * 400, end)
        try:
            rules = await c.market.get_instrument_rules(symbol)
        except ExchangeError:
            rules = None
        base = {"risk_per_trade_pct": s.risk.risk_per_trade_pct, "fee_rate_pct": s.risk.fee_rate_pct,
                "slippage_pct": s.risk.slippage_pct, "leverage": s.risk.leverage}
        out["ablation"] = await asyncio.to_thread(ablation, symbol, timeframe, df, htf, start, end, s.strategy, base,
                                                  rules, folds, list(ABLATIONS))
    return jsonable(out)
