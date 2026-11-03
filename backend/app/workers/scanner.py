"""Background market scanner.

Every ``scan_interval`` seconds the scanner checks whether a new candle has *closed* for
each watchlisted symbol/timeframe. Only then does it refresh history, recompute features,
run ``evaluate_at`` on the latest closed candle, persist the analysis snapshot, create
signals (idempotently) and evaluate the outcome of pending signals.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from app.api.hub import EventHub
from app.db.models import AnalysisSnapshot
from app.db.session import upsert_stmt
from app.exchanges.bybit import ExchangeError
from app.jsonutil import jsonable
from app.market.service import MarketDataService
from app.risk.sizing import InstrumentRules
from app.settings_store import RuntimeSettings, SettingsStore
from app.signals.service import SignalService
from app.strategy.engine import Evaluation, evaluate_at
from app.strategy.features import Features, compute_features
from app.timeutil import TIMEFRAMES, utcnow

log = logging.getLogger(__name__)


def signal_status(ev: Evaluation, data_ok: bool) -> str:
    if not data_ok:
        return "DATA_ISSUE"
    if not ev.ready:
        return "NOT_READY"
    if ev.setup is not None:
        return f"{ev.setup.direction}_SIGNAL"
    if any(st.status == "REJECTED" for st in ev.setups):
        return "REJECTED"
    watch = [st for st in ev.setups if st.status == "WATCHLIST"]
    if watch:
        return f"WATCH_{watch[0].direction}"
    return "NONE"


def levels_from_zones(zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Phase 1 ``levels`` shape (single prices) derived from zones, for older clients."""
    return [{"price": z["mid"], "kind": z["zone_type"].lower(), "touches": z["tests"], "last_index": z["last_test_index"],
             "lower": z["lower"], "upper": z["upper"], "status": z["status"]} for z in zones]


def build_snapshot(symbol: str, f: Features, ev: Evaluation, gaps: list, data_ok: bool, current: bool,
                   rules: InstrumentRules | None = None) -> dict[str, Any]:
    return jsonable({
        "symbol": symbol,
        "timeframe": f.timeframe,
        "candle_open_time": ev.open_time,
        "candle_close_time": ev.close_time,
        "evaluation": ev.to_dict(),
        "signal_status": signal_status(ev, data_ok),
        "levels": levels_from_zones(ev.zones),
        "instrument": rules.to_dict() if rules else None,
        "data_quality": {
            "candles": len(f),
            "gaps": [[a.isoformat(), b.isoformat()] for a, b in gaps],
            "current": current,
        },
        "updated_at": utcnow().isoformat(),
    })


class Scanner:
    def __init__(self, market: MarketDataService, settings: SettingsStore, signals: SignalService, hub: EventHub,
                 session_factory, interval: float = 15.0, grace_seconds: float = 3.0) -> None:
        self.market = market
        self.settings = settings
        self.signals = signals
        self.hub = hub
        self._session_factory = session_factory
        self.interval = interval
        self.grace_seconds = grace_seconds
        self._features: dict[tuple[str, str], tuple[str, str, Features]] = {}
        self._last_evaluated: dict[tuple[str, str], str] = {}
        self.status: dict[str, Any] = {
            "running": False, "is_leader": False, "cycles": 0, "last_cycle_at": None, "last_cycle_ms": None,
            "last_error": None, "errors": 0, "evaluations": 0, "signals_created": 0, "symbols": {},
        }

    def invalidate(self) -> None:
        """Forget evaluation state (called when settings change)."""
        self._features.clear()
        self._last_evaluated.clear()

    async def run(self) -> None:
        self.status["running"] = True
        try:
            while True:
                started = time.perf_counter()
                try:
                    await self.settings.refresh()  # pick up changes made through other instances
                    await self.scan_once()
                    self.status["last_error"] = None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - keep the worker alive
                    self.status["errors"] += 1
                    self.status["last_error"] = str(exc)
                    log.exception("scanner cycle failed")
                self.status["cycles"] += 1
                self.status["last_cycle_at"] = utcnow().isoformat()
                self.status["last_cycle_ms"] = round((time.perf_counter() - started) * 1000)
                await self.hub.broadcast("scanner_status", self.public_status())
                await asyncio.sleep(self.interval)
        finally:
            self.status["running"] = False

    def public_status(self) -> dict[str, Any]:
        return {**self.status, "market": self.market.state.connection_view()}

    async def _features_for(self, symbol: str, tf: str, settings: RuntimeSettings, now: datetime
                            ) -> tuple[Features | None, Any]:
        series = await self.market.get_closed_candles(symbol, tf, now)
        if series.df.empty:
            return None, series
        key = (symbol, tf)
        last = series.df["open_time"].iloc[-1].isoformat()
        phash = settings.strategy.params_hash()
        cached = self._features.get(key)
        if cached and cached[0] == last and cached[1] == phash:
            return cached[2], series
        feats = await asyncio.to_thread(compute_features, series.df, tf, settings.strategy)
        self._features[key] = (last, phash, feats)
        return feats, series

    async def scan_once(self, now: datetime | None = None) -> None:
        settings = self.settings.current
        now = now or self.market.now()
        # Only act once the grace period after a candle close has elapsed, so the
        # exchange has finalised the candle.
        effective_now = now - timedelta(seconds=self.grace_seconds)
        self.status["is_leader"] = True
        for symbol in settings.watchlist:
            sym_status = self.status["symbols"].setdefault(symbol, {})
            features: dict[str, Features] = {}
            series_by_tf: dict[str, Any] = {}
            try:
                for tf in settings.required_timeframes:  # longest first, so HTF data is current
                    feats, series = await self._features_for(symbol, tf, settings, effective_now)
                    if feats is not None:
                        features[tf] = feats
                        series_by_tf[tf] = series
            except ExchangeError as exc:
                sym_status["error"] = str(exc)
                log.warning("market data unavailable", extra={"symbol": symbol, "error": str(exc)})
                continue
            sym_status["error"] = None
            for tf in settings.timeframes:
                f = features.get(tf)
                if f is None or self._last_evaluated.get((symbol, tf)) == f.time_at(len(f) - 1).isoformat():
                    continue  # no newly closed candle
                await self._evaluate(symbol, tf, features, series_by_tf[tf], settings, now)
                await self.signals.evaluate_pending(symbol, tf, series_by_tf[tf].df, settings.evaluation.max_bars)

    async def _evaluate(self, symbol: str, tf: str, features: dict[str, Features], series: Any,
                        settings: RuntimeSettings, now: datetime) -> None:
        f = features[tf]
        i = len(f) - 1
        last_open = f.time_at(i).isoformat()
        htf = {k: v for k, v in features.items() if TIMEFRAMES[k] > TIMEFRAMES[tf]}
        try:
            rules = await self.market.get_instrument_rules(symbol)
        except ExchangeError as exc:
            log.warning("instrument rules unavailable; plans are not rounded", extra={"symbol": symbol,
                                                                                      "error": str(exc)})
            rules = None
        ev = await asyncio.to_thread(evaluate_at, f, i, settings.strategy, htf, settings.risk.sizing(), rules,
                                     True, symbol)
        warmup = settings.strategy.warmup_bars
        recent_gaps = [g for g in series.gaps if g[1] >= f.time_at(max(0, i - warmup)).to_pydatetime()]
        data_ok = not recent_gaps and series.is_current
        snapshot = build_snapshot(symbol, f, ev, series.gaps, data_ok, series.is_current, rules)
        async with self._session_factory()() as session:
            await session.execute(upsert_stmt(session, AnalysisSnapshot,
                                              [{"symbol": symbol, "timeframe": tf, "data": snapshot,
                                                "updated_at": utcnow()}],
                                              ["symbol", "timeframe"], ["data", "updated_at"]))
            await session.commit()
        self.status["evaluations"] += 1
        self.status["symbols"].setdefault(symbol, {})[tf] = {"last_candle": last_open,
                                                             "signal_status": snapshot["signal_status"]}
        await self.hub.broadcast("analysis", snapshot)
        if ev.setup is not None:
            if data_ok:
                created = await self.signals.handle_setup(self.market.exchange, symbol, ev, settings, now)
                if created:
                    self.status["signals_created"] += 1
            else:
                log.warning("setup ignored because of data quality issues",
                            extra={"symbol": symbol, "timeframe": tf, "gaps": len(recent_gaps)})
        self._last_evaluated[(symbol, tf)] = last_open
