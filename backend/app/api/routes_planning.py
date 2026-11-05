"""Experimental trade planning: the Trade Setup card, the position calculator and the Phase 9 study.

Read-only with respect to signals, paper trades and settings. Nothing here executes anything."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import ContainerDep, SymbolPath, Timeframe
from app.api.routes_market import analyze
from app.db.models import PaperTrade
from app.db.session import session_factory
from app.exchanges.bybit import ExchangeError
from app.jsonutil import jsonable
from app.planning.card import build_card, load_study
from app.planning.sizing import PositionInput, plan_position
from app.planning.stops import STOP_METHODS
from app.planning.targets import Costs

router = APIRouter(prefix="/api/planning", tags=["planning"])


async def open_paper_positions() -> tuple[list[dict[str, Any]], float]:
    """Open paper positions across the three accounts, one row per coin and direction, and the largest
    account's open risk. The accounts are separate; this only shows what the plan would overlap with."""
    async with session_factory()() as session:
        rows = (await session.execute(select(PaperTrade).where(PaperTrade.status == "OPEN"))).scalars().all()
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    per_variant: dict[str, float] = {}
    for r in rows:
        seen.setdefault((r.symbol, r.direction), {"symbol": r.symbol, "direction": r.direction, "timeframe": r.timeframe})
        per_variant[r.variant_key] = per_variant.get(r.variant_key, 0.0) + (r.risk_amount or 0.0)
    return list(seen.values()), max(per_variant.values(), default=0.0)


@router.get("/{symbol}/trade-plan", summary="Experimental plan for each triggered setup on the latest closed candle")
async def trade_plan(c: ContainerDep, symbol: SymbolPath, timeframe: Timeframe = "1h",
                     stop_method: str = Query("A_original", max_length=20),
                     tp: list[float] = Query([1.0, 2.0, 3.0], description="Take-profit candidates in R", max_length=5)) -> dict[str, Any]:
    if stop_method not in STOP_METHODS:
        raise HTTPException(422, f"unknown stop method; use one of {', '.join(STOP_METHODS)}")
    if any(not 0 < x <= 20 for x in tp) or list(tp) != sorted(set(tp)):
        raise HTTPException(422, "take-profit multiples must be increasing values above 0 and up to 20")
    _series, f, rules, ev = await analyze(c, symbol, timeframe)
    s = c.settings.current
    pc, rl = s.validation.paper, s.validation.risk
    costs = Costs(pc.taker_fee_pct, pc.taker_fee_pct, pc.slippage_pct, pc.stop_slippage_pct)
    positions, open_risk = await open_paper_positions()
    corr = c.scanner.correlation_fn(s) if positions else None
    card = await asyncio.to_thread(build_card, symbol, timeframe, f, len(f) - 1, ev, rules, stop_method, list(tp), costs, positions,
                                   corr, rl.corr_threshold, load_study(c.research.cache_dir))
    near = lambda kind: next((z for z in ev.zones if z["zone_type"] == kind and z["status"] != "INVALIDATED"), None)  # noqa: E731
    return jsonable({**card, "ready": ev.ready, "not_ready_reason": ev.not_ready_reason,
                     "support": near("SUPPORT"), "resistance": near("RESISTANCE"),
                     "ticker": c.market.state.ticker_view(symbol),
                     "defaults": {"equity": pc.starting_equity, "risk_pct": rl.risk_per_trade_pct, "leverage": s.risk.leverage,
                                  "max_leverage": s.risk.max_leverage, "entry_fee_pct": pc.taker_fee_pct, "exit_fee_pct": pc.taker_fee_pct,
                                  "entry_slippage_pct": pc.slippage_pct, "stop_slippage_pct": pc.stop_slippage_pct,
                                  "maintenance_margin_pct": s.risk.maintenance_margin_pct, "max_open_risk_pct": rl.max_open_risk_pct,
                                  "open_risk_usdt": open_risk},
                     "open_positions": positions})


@router.post("/position", summary="Risk-based position size with exchange checks. Estimates only.")
async def position(inp: PositionInput, c: ContainerDep) -> dict[str, Any]:
    rules = None
    if inp.symbol:
        try:
            rules = await c.market.get_instrument_rules(inp.symbol)
        except ExchangeError as exc:
            raise HTTPException(503, f"could not load Bybit instrument rules for {inp.symbol}: {exc}") from exc
    return jsonable(plan_position(inp, rules))


@router.get("/study", summary="Phase 9 research results. Historical, already-inspected data; every variant is Unvalidated.")
async def study(c: ContainerDep) -> dict[str, Any]:
    data = load_study(c.research.cache_dir)
    if data is None:
        raise HTTPException(404, "no Phase 9 study yet; run python -m app.research.p9_study")
    return data
