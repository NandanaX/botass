from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from app.backtest.engine import BacktestConfig, run_backtest
from app.db.models import BacktestRun
from app.exchanges.bybit import ExchangeError
from app.jsonutil import jsonable
from app.market.service import MarketDataService
from app.timeutil import TIMEFRAMES, tf_delta, utcnow

log = logging.getLogger(__name__)


def _clean(value: Any) -> Any:
    """Make results JSON-safe (numpy types, NaN/inf -> None)."""
    return jsonable(value)


def run_to_dict(run: BacktestRun, include_details: bool = True) -> dict[str, Any]:
    data = {
        "id": run.id,
        "name": run.name,
        "status": run.status,
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "config": run.config,
        "metrics": run.metrics,
        "assumptions": run.assumptions,
        "error": run.error,
        "kind": "historical_backtest",
    }
    if include_details:
        data["trades"] = run.trades
        data["equity_curve"] = run.equity_curve
    return data


class BacktestService:
    def __init__(self, market: MarketDataService, session_factory, timeout_seconds: float = 600.0) -> None:
        self.market = market
        self._session_factory = session_factory
        self.timeout = timeout_seconds
        self._tasks: set[asyncio.Task] = set()

    async def submit(self, cfg: BacktestConfig) -> BacktestRun:
        run = BacktestRun(
            id=str(uuid.uuid4()),
            name=cfg.name or f"{cfg.symbol} {cfg.timeframe} {cfg.start:%Y-%m-%d}→{cfg.end:%Y-%m-%d}",
            status="QUEUED",
            created_at=utcnow(),
            config=cfg.model_dump(mode="json"),
        )
        async with self._session_factory()() as session:
            session.add(run)
            await session.commit()
        task = asyncio.create_task(self._execute(run.id, cfg), name=f"backtest-{run.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    async def _set(self, run_id: str, **fields: Any) -> None:
        async with self._session_factory()() as session:
            run = await session.get(BacktestRun, run_id)
            if run is None:
                return
            for k, v in fields.items():
                setattr(run, k, v)
            await session.commit()

    async def _execute(self, run_id: str, cfg: BacktestConfig) -> None:
        await self._set(run_id, status="RUNNING")
        try:
            result = await asyncio.wait_for(self._run(cfg), timeout=self.timeout)
            await self._set(run_id, status="COMPLETED", finished_at=utcnow(), metrics=_clean(result.metrics),
                            trades=_clean(result.trades), equity_curve=_clean(result.equity_curve),
                            assumptions=result.assumptions)
            log.info("backtest completed", extra={"run_id": run_id, "trades": result.metrics.get("trades")})
        except (ExchangeError, ValueError, TimeoutError) as exc:
            await self._set(run_id, status="FAILED", finished_at=utcnow(), error=str(exc) or type(exc).__name__)
            log.warning("backtest failed", extra={"run_id": run_id, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            await self._set(run_id, status="FAILED", finished_at=utcnow(), error=f"internal error: {exc}")
            log.exception("backtest crashed", extra={"run_id": run_id})

    async def _run(self, cfg: BacktestConfig):
        params = cfg.strategy
        warmup = tf_delta(cfg.timeframe) * (params.warmup_bars + 50)
        candles = await self.market.load_history(cfg.symbol, cfg.timeframe, cfg.start - warmup, cfg.end)
        if candles.empty:
            raise ValueError("no historical candles available for this range")
        htf: dict = {}
        htf_tfs = set(params.zones.htf_timeframes) | (set(params.mtf.timeframes) if params.mtf.enabled else set())
        for tf in sorted(htf_tfs, key=lambda t: TIMEFRAMES[t]):
            if TIMEFRAMES[tf] > TIMEFRAMES[cfg.timeframe]:
                htf_warm = tf_delta(tf) * (max(params.warmup_bars, params.zones.lookback) + 50)
                htf[tf] = await self.market.load_history(cfg.symbol, tf, cfg.start - htf_warm, cfg.end)
        try:
            rules = await self.market.get_instrument_rules(cfg.symbol)
        except ExchangeError as exc:
            log.warning("instrument rules unavailable; quantities not rounded", extra={"error": str(exc)})
            rules = None
        funding = None
        if cfg.include_funding:
            try:
                funding = await self.market.load_funding(cfg.symbol, cfg.start, cfg.end)
            except ExchangeError as exc:
                log.warning("funding history unavailable; excluded from backtest", extra={"error": str(exc)})
        return await asyncio.to_thread(run_backtest, cfg, candles, htf, funding, rules)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
