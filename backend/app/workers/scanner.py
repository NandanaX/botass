"""Background market scanner.

The scanner wakes just after each candle close (and at least every ``scan_interval`` seconds)
and checks whether a new candle has *closed* for each watchlisted symbol/timeframe. Only then
does it refresh history, recompute features, run ``evaluate_at`` on the latest closed candle,
persist the analysis snapshot, create signals (idempotently) and evaluate the outcome of
pending signals. Candles are fetched for all coins at once; coins are evaluated one at a time
in watchlist order, so risk limits and signal order don't depend on network timing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.analysis.frame import build_frame
from app.analysis.persist import detections_at
from app.api.hub import EventHub
from app.db.models import AnalysisSnapshot, NotificationEvent, PatternDetection, Signal
from app.db.session import upsert_stmt
from app.exchanges.bybit import ExchangeError
from app.jsonutil import jsonable
from app.market.service import MarketDataService
from app.notifications.messages import candidate_event, watchlist_digest_event
from app.research.decision import decide, live_correlation, shadow_state
from app.risk.sizing import InstrumentRules
from app.settings_store import RuntimeSettings, SettingsStore
from app.signals.service import SignalService, signal_to_dict
from app.strategy.engine import Evaluation, evaluate_at
from app.strategy.features import Features, compute_features
from app.timeutil import TIMEFRAMES, utcnow

log = logging.getLogger(__name__)


# After a close, how long to keep retrying quickly when the exchange hasn't published the candle yet.
CATCH_UP_SECONDS = 30.0
CATCH_UP_RETRY_SECONDS = 2.0
WAKE_MARGIN_SECONDS = 0.05
# The same coin, timeframe and direction is announced at most once per this many of its candles.
WATCH_ALERT_COOLDOWN_CANDLES = 8
WATCH_ALERT_MAX_AGE_SECONDS = 600


def seconds_to_next_scan(now: datetime, timeframes: list[str], grace: float, interval: float, behind: bool = False) -> float:
    """Sleep until just after the next candle close plus the grace period, or ``interval`` if that's sooner."""
    # Capped at a day: weekly candles close on a Monday midnight, which is also a daily boundary.
    step = min(86_400, *(TIMEFRAMES[t] for t in timeframes))
    since_close = now.timestamp() % step
    if behind and since_close < grace + CATCH_UP_SECONDS:
        return CATCH_UP_RETRY_SECONDS
    until = grace - since_close if since_close < grace else step - since_close + grace
    return max(WAKE_MARGIN_SECONDS, min(interval, until + WAKE_MARGIN_SECONDS))


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
        self.forward = None  # ForwardService (Phase 6), set by the container
        self.experiments = None  # ForwardExperiments: paper-only forward tests, set by the container
        self.center = None  # NotificationService (Phase 7), set by the container
        self._watching: dict[tuple[str, str, str, str], str] = {}  # candidates on the watchlist at the last candle
        self._watch_new: list[dict[str, Any]] = []  # candidates that just got close, sent together after the cycle
        self._watch_sent: dict[tuple[str, str, str], datetime] | None = None  # last announced, restored from history
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
                await asyncio.sleep(seconds_to_next_scan(self.market.now(), self.settings.current.timeframes, self.grace_seconds,
                                                         self.interval, bool(self.status.get("series_behind"))))
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

    async def _prepare_symbol(self, symbol: str, settings: RuntimeSettings, now: datetime
                              ) -> tuple[dict[str, Features], dict[str, Any], int]:
        """Candles and features of one coin. Runs alongside the other coins'; the client's rate limit still applies."""
        features: dict[str, Features] = {}
        series_by_tf: dict[str, Any] = {}
        behind = 0
        for tf in settings.required_timeframes:  # longest first, so HTF data is current
            feats, series = await self._features_for(symbol, tf, settings, now)
            if feats is not None:
                features[tf] = feats
                series_by_tf[tf] = series
            if not series.is_current:
                behind += 1  # working from cached candles: no new candle can be seen
        return features, series_by_tf, behind

    async def scan_once(self, now: datetime | None = None) -> None:
        settings = self.settings.current
        now = now or self.market.now()
        # Only act once the grace period after a candle close has elapsed, so the
        # exchange has finalised the candle.
        effective_now = now - timedelta(seconds=self.grace_seconds)
        self.status["is_leader"] = True
        cycle_errors: list[str] = []
        market_errors = behind = 0
        prepared = {symbol: asyncio.create_task(self._prepare_symbol(symbol, settings, effective_now))
                    for symbol in settings.watchlist}
        try:
            for symbol in settings.watchlist:
                sym_status = self.status["symbols"].setdefault(symbol, {})
                try:
                    features, series_by_tf, symbol_behind = await prepared[symbol]
                    behind += symbol_behind
                except ExchangeError as exc:
                    sym_status["error"] = str(exc)
                    market_errors += 1
                    log.warning("market data unavailable", extra={"symbol": symbol, "error": str(exc)})
                    continue
                except Exception as exc:  # noqa: BLE001 - one coin must not stop the others
                    sym_status["error"] = str(exc)
                    cycle_errors.append(f"{symbol}: {exc}")
                    self.status["errors"] += 1
                    log.exception("scanner failed preparing a symbol", extra={"symbol": symbol})
                    continue
                sym_status["error"] = None
                for tf in settings.timeframes:
                    f = features.get(tf)
                    if f is None or self._last_evaluated.get((symbol, tf)) == f.time_at(len(f) - 1).isoformat():
                        continue  # no newly closed candle
                    try:
                        await self._evaluate(symbol, tf, features, series_by_tf[tf], settings, now)
                        await self.signals.evaluate_pending(symbol, tf, series_by_tf[tf].df, settings.evaluation.max_bars,
                                                            settings.risk.fee_rate_pct, settings.risk.slippage_pct)
                    except Exception as exc:  # noqa: BLE001 - retried next cycle; other coins continue
                        cycle_errors.append(f"{symbol} {tf}: {exc}")
                        self.status["errors"] += 1
                        log.exception("scanner failed evaluating a candle", extra={"symbol": symbol, "timeframe": tf})
        finally:
            for task in prepared.values():
                task.cancel()  # only matters if this cycle is itself cancelled at shutdown
        self.status["last_error"] = cycle_errors[0] if cycle_errors else None
        self.status["cycle_errors"] = len(cycle_errors)
        self.status["market_errors"] = market_errors
        self.status["series_behind"] = behind
        if self.center is not None:
            await self._send_watch_digest(now)
            await self.center.group_correlated(self.correlation_fn(settings), settings.validation.risk.corr_threshold)

    async def _load_watch_sent(self) -> dict[tuple[str, str, str], datetime]:
        """What was announced recently, so a restart doesn't repeat it all."""
        since = utcnow() - timedelta(days=2)
        async with self._session_factory()() as session:
            rows = (await session.execute(select(NotificationEvent).where(
                NotificationEvent.event_type == "WATCHLIST_DIGEST", NotificationEvent.occurred_at >= since)
                .order_by(NotificationEvent.occurred_at))).scalars().all()
        return {(x["symbol"], x["timeframe"], x["direction"]): r.occurred_at for r in rows for x in (r.payload or {}).get("items", [])}

    async def _send_watch_digest(self, now: datetime) -> None:
        """Early look: one message listing what just got close. Never interrupts scanning."""
        pending, self._watch_new = self._watch_new, []
        if not pending:
            return
        try:
            if self._watch_sent is None:
                self._watch_sent = await self._load_watch_sent()
            fresh: dict[tuple[str, str, str], dict[str, Any]] = {}
            for item in pending:
                key = (item["symbol"], item["timeframe"], item["direction"])
                last = self._watch_sent.get(key)
                if key not in fresh and (last is None or (now - last).total_seconds()
                                         >= WATCH_ALERT_COOLDOWN_CANDLES * TIMEFRAMES[item["timeframe"]]):
                    fresh[key] = item
            if fresh:
                self._watch_sent.update(dict.fromkeys(fresh, now))
                await self.center.emit(watchlist_digest_event(list(fresh.values()), now, now + timedelta(seconds=WATCH_ALERT_MAX_AGE_SECONDS)))
        except Exception:  # noqa: BLE001 - notifications never interrupt scanning
            log.exception("watchlist digest failed")

    def correlation_fn(self, settings: RuntimeSettings):
        """Rolling correlation of hourly returns from the scanner's cached closed 1h candles."""
        closes = {}
        for s in settings.watchlist:
            c = self._features.get((s, "1h"))
            if c is not None:
                closes[s] = (c[2].open_time, c[2].close)
        window = settings.validation.risk.corr_window_hours
        return lambda a, b: live_correlation(closes, a, b, window)

    async def _decide(self, symbol: str, f: Features, ev: Evaluation, features: dict[str, Features], data_ok: bool,
                      settings: RuntimeSettings, now: datetime) -> dict[str, Any] | None:
        """Run the decision engine on a QUALIFIED setup. Never interrupts signal creation."""
        try:
            since = now - timedelta(days=7)
            async with self._session_factory()() as session:
                rows = (await session.execute(select(Signal).where(Signal.created_at >= since))).scalars().all()
                recent = [signal_to_dict(s) for s in rows]
            now_ns = int(now.timestamp() * 1e9)
            state = shadow_state(recent, now_ns)
            cfg = settings.decision
            closes = {}
            for s in settings.watchlist:
                c = self._features.get((s, "1h"))
                if c is not None:
                    closes[s] = (c[2].open_time, c[2].close)
            window = cfg.portfolio.corr_window_hours
            corr_fn = lambda a, b: live_correlation(closes, a, b, window)  # noqa: E731
            btc = self._features.get(("BTCUSDT", f.timeframe))
            dec_ns = int(f.close_time[ev.index])
            cluster_prior = sum(1 for s in recent if s["symbol"] != symbol and s["direction"] == ev.setup.direction
                                and 0 <= dec_ns - int(datetime.fromisoformat(s["candle_close_time"]).timestamp() * 1e9)
                                <= 3_600_000_000_000)
            return await asyncio.to_thread(
                decide, ev, f, symbol, cfg, data_ok=data_ok, open_positions=state["open"],
                realized_today_r=state["realized_today_r"], losing_streak=state["losing_streak"],
                last_loss_ns=state["last_loss_ns"], corr_fn=corr_fn, btc_features=btc[2] if btc else None,
                htf4h_features=features.get("4h") if f.timeframe != "4h" else None, now_ns=now_ns,
                cost_pct=2 * settings.risk.fee_rate_pct + settings.risk.slippage_pct + 0.05,
                cluster_prior=cluster_prior)
        except Exception:  # noqa: BLE001
            log.exception("decision engine failed", extra={"symbol": symbol})
            return {"outcome": "ERROR", "mode": settings.decision.mode, "reason": "decision engine error",
                    "checks": []}

    async def _notify_candidates(self, symbol: str, tf: str, ev: Evaluation, data_ok: bool) -> None:
        """Candidates that did not qualify. A watchlist candidate notifies only when it first appears
        (it can sit there for many candles); a qualified setup is announced by the signal service."""
        if self.center is None:
            return
        try:
            events = []
            watching: set[tuple[str, str, str, str]] = set()
            for st in ev.setups:
                if st.status == "QUALIFIED":
                    if not data_ok:
                        events.append(candidate_event(symbol, tf, st, ev.open_time, ev.close_time, "REJECTED",
                                                      "qualified on rules, but ignored because of candle data gaps or stale data"))
                elif st.status == "WATCHLIST":
                    key = (symbol, tf, st.direction, st.strategy)
                    watching.add(key)
                    if key not in self._watching:
                        events.append(candidate_event(symbol, tf, st, ev.open_time, ev.close_time))
                        if data_ok:
                            why = next((r.get("message") for r in st.reasons or [] if r.get("message")), st.explanation)
                            self._watch_new.append({"symbol": symbol, "timeframe": tf, "direction": st.direction, "why": why})
                elif st.status == "REJECTED":
                    events.append(candidate_event(symbol, tf, st, ev.open_time, ev.close_time))
            self._watching = {k: v for k, v in self._watching.items() if k[:2] != (symbol, tf)}
            self._watching.update({k: ev.open_time for k in watching})
            for event in events:
                await self.center.emit(event)
        except Exception:  # noqa: BLE001 - notifications never interrupt scanning
            log.exception("candidate notification failed", extra={"symbol": symbol, "timeframe": tf})

    async def _persist_detections(self, symbol: str, tf: str, f: Features, series: Any, settings: RuntimeSettings,
                                  i: int) -> None:
        """Store structure events, chart-pattern breakouts and candlestick patterns confirmed on
        this candle. Failures are logged and never interrupt signal scanning."""
        try:
            fr = await asyncio.to_thread(build_frame, series.df, tf, settings.strategy, settings.analysis, f)
            rows = await asyncio.to_thread(detections_at, fr, i, symbol, settings.strategy.version)
            if rows:
                async with self._session_factory()() as session:
                    await session.execute(upsert_stmt(session, PatternDetection, rows, ["id"], ["status", "data"]))
                    await session.commit()
                self.status["detections"] = self.status.get("detections", 0) + len(rows)
        except Exception:  # noqa: BLE001
            log.exception("pattern detection persistence failed", extra={"symbol": symbol, "timeframe": tf})

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
        self.status["last_evaluated_at"] = utcnow().isoformat()
        self.status["last_candle"] = {"symbol": symbol, "timeframe": tf, "open_time": last_open,
                                      "close_time": snapshot["candle_close_time"]}
        self.status["symbols"].setdefault(symbol, {})[tf] = {"last_candle": last_open,
                                                             "signal_status": snapshot["signal_status"]}
        await self.hub.broadcast("analysis", snapshot)
        await self._persist_detections(symbol, tf, f, series, settings, i)
        await self._notify_candidates(symbol, tf, ev, data_ok)
        if self.forward is not None:
            corr = self.correlation_fn(settings)
            await self.forward.on_evaluation(symbol, tf, f, ev, data_ok, now, htf, rules, corr)
        if self.experiments is not None:
            await self.experiments.on_evaluation(symbol, tf, f, ev, data_ok, now)
        if ev.setup is not None:
            if data_ok:
                decision = await self._decide(symbol, f, ev, features, data_ok, settings, now)
                created = await self.signals.handle_setup(self.market.exchange, symbol, ev, settings, now, decision)
                if created:
                    self.status["signals_created"] += 1
            else:
                log.warning("setup ignored because of data quality issues",
                            extra={"symbol": symbol, "timeframe": tf, "gaps": len(recent_gaps)})
        self._last_evaluated[(symbol, tf)] = last_open
