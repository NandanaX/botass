"""Backtests, risk calculator, settings and alert endpoints."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import desc, select

from app.api.deps import ContainerDep
from app.backtest.engine import BacktestConfig
from app.backtest.service import run_to_dict
from app.db.models import BacktestRun
from app.db.session import session_factory
from app.exchanges.bybit import ExchangeError
from app.risk.calculator import RiskInput, RiskOutput, calculate_risk
from app.settings_store import RuntimeSettings
from app.timeutil import TIMEFRAMES

router = APIRouter(prefix="/api", tags=["tools"])


# ---------------------------------------------------------------- backtests

@router.post("/backtests", status_code=202, summary="Start a backtest (runs in the background)")
async def create_backtest(cfg: BacktestConfig, c: ContainerDep) -> dict[str, Any]:
    run = await c.backtests.submit(cfg)
    return run_to_dict(run, include_details=False)


@router.get("/backtests", summary="List backtest runs (summary only)")
async def list_backtests(limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
    async with session_factory()() as session:
        runs = (await session.execute(select(BacktestRun).order_by(desc(BacktestRun.created_at)).limit(limit))
                ).scalars().all()
    return [run_to_dict(r, include_details=False) for r in runs]


@router.get("/backtests/compare", summary="Compare metrics of several completed runs")
async def compare_backtests(ids: str = Query(..., description="Comma-separated run ids", max_length=2000)
                            ) -> list[dict[str, Any]]:
    id_list = [i.strip() for i in ids.split(",") if i.strip()][:10]
    async with session_factory()() as session:
        runs = (await session.execute(select(BacktestRun).where(BacktestRun.id.in_(id_list)))).scalars().all()
    by_id = {r.id: r for r in runs}
    return [{**run_to_dict(by_id[i], include_details=False), "equity_curve": by_id[i].equity_curve}
            for i in id_list if i in by_id]


@router.get("/backtests/{run_id}", summary="Backtest run with trades and equity curve")
async def get_backtest(run_id: str) -> dict[str, Any]:
    async with session_factory()() as session:
        run = await session.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(404, "backtest not found")
    return run_to_dict(run)


@router.delete("/backtests/{run_id}", status_code=204, response_class=Response, summary="Delete a backtest run")
async def delete_backtest(run_id: str) -> Response:
    async with session_factory()() as session:
        run = await session.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(404, "backtest not found")
        if run.status in ("QUEUED", "RUNNING"):
            raise HTTPException(409, "cannot delete a running backtest")
        await session.delete(run)
        await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- risk

@router.post("/risk/calculate", response_model=RiskOutput, summary="Position size and risk estimates")
async def risk_calculate(inp: RiskInput, c: ContainerDep) -> RiskOutput:
    rules = None
    if inp.symbol:
        try:
            rules = await c.market.get_instrument_rules(inp.symbol)
        except ExchangeError as exc:
            raise HTTPException(503, f"could not load Bybit instrument rules for {inp.symbol}: {exc}") from exc
        if rules is None:
            raise HTTPException(422, f"{inp.symbol} is not a Bybit USDT perpetual")
    return calculate_risk(inp, rules)


# ---------------------------------------------------------------- settings

def _settings_view(c) -> dict[str, Any]:
    chat = c.env.telegram_chat_id or ""
    return {
        "settings": c.settings.current.model_dump(mode="json"),
        "defaults": RuntimeSettings().model_dump(mode="json"),  # read-only, for "changed from default" in Settings
        "telegram": {
            "configured": c.env.telegram_configured,
            "chat_id_hint": f"…{chat[-4:]}" if len(chat) >= 4 else ("set" if chat else None),
        },
        "available_timeframes": list(TIMEFRAMES),
        "strategy_version": c.settings.current.strategy.version,
    }


@router.get("/settings", summary="Runtime settings (no secrets)")
async def get_settings_route(c: ContainerDep) -> dict[str, Any]:
    return _settings_view(c)


@router.put("/settings", summary="Update runtime settings")
async def put_settings(payload: dict[str, Any], c: ContainerDep,
                       confirm_new_forward_period: bool = Query(False, description="Required when this change restarts the forward test")
                       ) -> dict[str, Any]:
    try:
        new = RuntimeSettings.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False)) from exc
    restarted = c.forward.period_changes(new)
    if restarted and not confirm_new_forward_period:
        raise HTTPException(409, (f"This change ends the current forward test of strategies {', '.join(restarted)} and restarts its "
                                  "evidence from zero. Confirm to continue."))
    added = [s for s in new.watchlist if s not in c.settings.current.watchlist]
    for symbol in added:
        try:
            instrument = await c.client.get_instrument(symbol)
        except ExchangeError as exc:
            raise HTTPException(503, f"could not validate {symbol} with Bybit: {exc}") from exc
        if instrument is None or instrument.get("status") != "Trading":
            raise HTTPException(422, f"{symbol} is not an actively trading Bybit USDT perpetual")
    await c.settings.update(new)
    return _settings_view(c)


# ---------------------------------------------------------------- price alerts

class PriceAlertIn(BaseModel):
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,20}USDT$")
    price: float = Field(gt=0)
    condition: Literal["above", "below"] | None = None
    note: str | None = Field(None, max_length=200)


@router.get("/price-alerts", summary="Price alerts (newest first)")
async def list_price_alerts(c: ContainerDep, status: Literal["ACTIVE", "TRIGGERED", "CANCELLED"] | None = None,
                            limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    return await c.price_alerts.list(status, limit)


@router.post("/price-alerts", status_code=201, summary="Notify once when the live price reaches a level")
async def create_price_alert(body: PriceAlertIn, c: ContainerDep) -> dict[str, Any]:
    if body.symbol not in c.settings.current.watchlist:
        raise HTTPException(422, f"{body.symbol} is not in the watchlist; add it in Settings so its live price is "
                                 "streamed")
    t = c.market.state.ticker_view(body.symbol)
    last = t.get("last_price")
    if not t.get("available") or not isinstance(last, (int, float)) or t.get("stale"):
        raise HTTPException(503, f"no live price for {body.symbol} right now; try again in a few seconds")
    try:
        return await c.price_alerts.create(body.symbol, body.price, float(last), body.condition, body.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/price-alerts/{alert_id}", summary="Cancel a price alert")
async def cancel_price_alert(alert_id: int, c: ContainerDep) -> dict[str, Any]:
    data = await c.price_alerts.cancel(alert_id)
    if data is None:
        raise HTTPException(404, "price alert not found")
    return data


# ---------------------------------------------------------------- alerts

@router.post("/alerts/telegram/test", summary="Send a Telegram test notification")
async def telegram_test(c: ContainerDep) -> dict[str, Any]:
    result = await c.signals.send_test()
    if not result.ok:
        raise HTTPException(502 if c.telegram else 400, result.error or "telegram send failed")
    return {"ok": True, "attempts": result.attempts}
