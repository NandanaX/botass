"""Strategy D: research and forward-shadow results. Read only: nothing here can change or promote it."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import ContainerDep
from app.research.data import DEFAULT_CACHE

router = APIRouter(prefix="/api/strategy-d", tags=["strategy-d"])
STUDY_FILE = "p10_latest.json"


@router.get("/overview", summary="Strategy D's forward shadow results, paper account and latest decisions")
async def overview(c: ContainerDep) -> dict[str, Any]:
    return await c.strategy_d.report()


@router.get("/decisions", summary="Every recorded 15m decision: QUALIFIED, WATCHLIST or REJECTED, with its reason")
async def decisions(c: ContainerDep, symbol: str | None = Query(None, pattern=r"^[A-Z0-9]{2,20}USDT$"),
                    decision: Literal["QUALIFIED", "WATCHLIST", "REJECTED"] | None = None,
                    limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
    return await c.strategy_d.decisions(symbol, decision, limit)


@router.get("/context/{symbol}", summary="1D, 4H, 1H and 15m bias as known when a 15m candle closed")
async def context(c: ContainerDep, symbol: str, at: datetime | None = None, direction: Literal["LONG", "SHORT"] | None = None) -> dict[str, Any]:
    if symbol.upper() not in c.settings.current.watchlist:
        raise HTTPException(404, f"{symbol} is not on the watchlist")
    return await c.strategy_d.context(symbol.upper(), at, direction)


@router.get("/study", summary="Historical comparison of Strategy D with the baseline strategies (research data)")
async def study() -> dict[str, Any]:
    path = DEFAULT_CACHE / STUDY_FILE
    if not path.exists():
        raise HTTPException(404, "No Phase 10 study has been run yet: python -m app.research.p10_study")
    return json.loads(path.read_text(encoding="utf-8"))
