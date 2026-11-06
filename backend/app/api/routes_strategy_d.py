"""Strategy D: research and forward-shadow results. Nothing here can change or promote it. The only
write is the manual reset of its paper account's drawdown breaker, which needs explicit confirmation."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import ContainerDep
from app.research.data import DEFAULT_CACHE
from app.strategy_d.service import ResetRefused

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


class ResetBody(BaseModel):
    reason: str = Field(min_length=10, max_length=1000, description="why the account is being reset; kept with the segment")
    reviewer: str = Field(min_length=1, max_length=80)
    confirm: str = Field(description="the exact phrase given as reset_confirm_phrase by the overview")


@router.post("/reset-drawdown", summary="Manually reset D's halted paper account and start a new evaluation segment")
async def reset_drawdown(c: ContainerDep, body: ResetBody) -> dict[str, Any]:
    try:
        return await c.strategy_d.reset_drawdown(body.reason.strip(), body.reviewer.strip(), body.confirm)
    except ResetRefused as exc:
        raise HTTPException(400 if exc.kind == "confirmation" else 409, str(exc)) from exc


@router.get("/study", summary="Historical comparison of Strategy D with the baseline strategies (research data)")
async def study() -> dict[str, Any]:
    path = DEFAULT_CACHE / STUDY_FILE
    if not path.exists():
        raise HTTPException(404, "No Phase 10 study has been run yet: python -m app.research.p10_study")
    return json.loads(path.read_text(encoding="utf-8"))
