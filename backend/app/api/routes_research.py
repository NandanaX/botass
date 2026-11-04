"""Signal Intelligence Lab API: forensics, today's performance and the experiment study.

Everything here is research output. Counterfactual results are simulations of what a rule
*would* have done on past data; they are not trades."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import ContainerDep
from app.exchanges.bybit import ExchangeError

router = APIRouter(prefix="/api/research", tags=["research"])


@router.get("/today", summary="Signals of one local day with verified outcomes, forensics and counterfactual decisions")
async def today(c: ContainerDep, day: date | None = None,
                tz: str = Query("Asia/Colombo", max_length=64)) -> dict[str, Any]:
    try:
        return await c.research.today(day, tz)
    except ExchangeError as exc:
        raise HTTPException(503, f"market data unavailable: {exc}") from exc


@router.get("/signals/{signal_id}/forensics", summary="Full forensic report of one stored signal (with chart data)")
async def forensics(c: ContainerDep, signal_id: str) -> dict[str, Any]:
    sig = await c.research.signal(signal_id)
    if sig is None:
        raise HTTPException(404, "signal not found")
    try:
        out = await c.research.forensics(sig)
    except ExchangeError as exc:
        raise HTTPException(503, f"market data unavailable: {exc}") from exc
    out = dict(out)
    out.pop("counterfactual", None)
    return out


@router.get("/study", summary="Latest experiment study (hypotheses, walk-forward, calibration, analyses)")
async def study(c: ContainerDep, include_trades: bool = False) -> dict[str, Any]:
    s = c.research.study()
    if s is None:
        raise HTTPException(404, "no study yet; start one with POST /api/research/study")
    if not include_trades:
        s = {k: v for k, v in s.items() if k != "trades_sample"}
    return {**s, "status": c.research.study_status}


@router.get("/study/trades", summary="Recent baseline trades with the proposed strategy's counterfactual decision")
async def study_trades(c: ContainerDep, limit: int = Query(200, ge=1, le=400)) -> list[dict[str, Any]]:
    s = c.research.study()
    if s is None:
        raise HTTPException(404, "no study yet")
    return (s.get("trades_sample") or [])[:limit]


class StudyRequest(BaseModel):
    rebuild: bool = Field(True, description="Fetch new history and replay the strategy before the experiments")
    days: int = Field(400, ge=120, le=1500)


@router.post("/study", summary="Start the study in the background (several minutes)", status_code=202)
async def start_study(c: ContainerDep, body: StudyRequest) -> dict[str, Any]:
    return c.research.start_study(body.rebuild, body.days)


@router.get("/study/status", summary="Background study status")
async def study_status(c: ContainerDep) -> dict[str, Any]:
    return c.research.study_status


@router.get("/decision/filters", summary="Study changes the decision engine applies in shadow mode")
async def decision_filters(c: ContainerDep) -> dict[str, Any]:
    from app.research.decision import active_study_filters
    return {"mode": c.settings.current.decision.mode, **active_study_filters(c.research.cache_dir)}
