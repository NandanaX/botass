"""Strategy Validation Monitor API (Phase 6).

``/overview`` returns FORWARD paper-trading results (signals detected live after each
variant's evaluation period started). ``/historical`` returns HISTORICAL re-simulations;
the two are never mixed. Nothing here places orders or changes the production engine."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import ContainerDep
from app.db.models import PaperTrade
from app.db.session import session_factory
from app.timeutil import utcnow

router = APIRouter(prefix="/api/validation", tags=["validation"])


@router.get("/trades/{trade_id}", summary="One paper-trade record")
async def trade(trade_id: str) -> dict[str, Any]:
    async with session_factory()() as session:
        t = await session.get(PaperTrade, trade_id)
    if t is None:
        raise HTTPException(404, "paper trade not found")
    return {col.name: (v.isoformat() if isinstance(v := getattr(t, col.name), datetime) else v)
            for col in PaperTrade.__table__.columns}

_hist: dict[str, Any] = {"state": "idle", "error": None, "started_at": None, "finished_at": None}
VariantKey = Literal["A", "B", "C"]


def _corr(c):
    return c.scanner.correlation_fn(c.settings.current)


@router.get("/overview", summary="Forward paper-trading results, risk state and promotion readiness per variant")
async def overview(c: ContainerDep) -> dict[str, Any]:
    return await c.forward.overview(_corr(c))


@router.get("/forward-tests", summary="Paper-only tests of research candidates (no alerts, separate from A, B and C)")
async def forward_tests(c: ContainerDep) -> dict[str, Any]:
    return await c.experiments.report()


@router.get("/trades", summary="Paper-trade records (forward only)")
async def trades(c: ContainerDep, variant: VariantKey | None = None, status: str | None = Query(None, max_length=16),
                 limit: int = Query(200, ge=1, le=2000),
                 period: Literal["current", "all"] = Query("current", description="all includes closed evaluation periods")
                 ) -> list[dict[str, Any]]:
    await c.forward._ready()
    out = []
    if period == "all":
        from sqlalchemy import select

        from app.db.models import StrategyVariant
        async with session_factory()() as session:
            ids = (await session.execute(select(StrategyVariant.id, StrategyVariant.key))).all()
        for vid, key in ids:
            if not variant or key == variant:
                rows = await c.forward.rows(vid)
                out += [r for r in rows if status is None or r["status"] == status]
        out.sort(key=lambda r: r["detected_at"], reverse=True)
        from app.forward.service import _public
        return [_public(r) for r in out[:limit]]
    for key, v in c.forward.variants.items():
        if variant and key != variant:
            continue
        rows = await c.forward.rows(v.id)
        out += [r for r in rows if status is None or r["status"] == status]
    out.sort(key=lambda r: r["detected_at"], reverse=True)
    from app.forward.service import _public
    return [_public(r) for r in out[:limit]]


@router.get("/historical", summary="Historical robustness and stress tests (re-simulation; not forward results)")
async def historical(c: ContainerDep) -> dict[str, Any]:
    p = c.research.cache_dir / "robustness_latest.json"
    if not p.exists():
        raise HTTPException(404, "no historical robustness run yet; POST /api/validation/historical")
    return {**json.loads(p.read_text(encoding="utf-8")), "status": _hist}


@router.post("/historical", summary="Re-run the historical robustness/stress study in the background", status_code=202)
async def run_historical(c: ContainerDep) -> dict[str, Any]:
    if _hist["state"] == "running":
        return _hist
    from app.research import robustness

    async def job():
        try:
            await asyncio.to_thread(robustness.run, c.research.cache_dir, c.settings.current.evaluation.max_bars)
            _hist.update(state="done", finished_at=utcnow().isoformat())
        except Exception as exc:  # noqa: BLE001
            _hist.update(state="failed", error=str(exc), finished_at=utcnow().isoformat())

    _hist.update(state="running", error=None, started_at=utcnow().isoformat(), finished_at=None)
    asyncio.create_task(job(), name="robustness")
    return _hist


class ReviewBody(BaseModel):
    decision: Literal["APPROVED", "REJECTED", "DEFERRED"]
    note: str = Field(min_length=3, max_length=2000)
    reviewer: str = Field(min_length=1, max_length=64)


@router.post("/promotion/{key}/decision", summary="Record a manual promotion review (never changes production)")
async def decide(c: ContainerDep, key: VariantKey, body: ReviewBody) -> dict[str, Any]:
    try:
        return await c.forward.record_decision(key, body.decision, body.note, body.reviewer, _corr(c))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class ResetBody(BaseModel):
    note: str = Field(min_length=3, max_length=2000)
    reviewer: str = Field(min_length=1, max_length=64)


@router.post("/risk/{key}/reset-drawdown", summary="Manually reset a paper account's max-drawdown circuit breaker")
async def reset(c: ContainerDep, key: VariantKey, body: ResetBody) -> dict[str, Any]:
    await c.forward.reset_drawdown(key, body.note, body.reviewer)
    return {"reset": True, "variant": key}
