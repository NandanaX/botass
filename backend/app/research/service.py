"""Research service used by the API: today's signal performance, per-signal forensics with a
counterfactual decision replay, and the background experiment study."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.db.models import Signal
from app.exchanges.bybit import BybitClient, ExchangeError
from app.jsonutil import jsonable
from app.market.service import MarketDataService
from app.research.data import DEFAULT_CACHE, ResearchData
from app.research.decision import active_study_filters, decide
from app.research.forensics import LIVE_WINDOW, analyze_signal, reconstruct
from app.research.portfolio import CorrelationBook
from app.research.simulate import CostModel, prepare_funding, simulate_trade
from app.settings_store import SettingsStore
from app.signals.service import signal_to_dict
from app.strategy.features import compute_features
from app.timeutil import TIMEFRAMES, tf_delta, utcnow

log = logging.getLogger(__name__)


class ResearchService:
    def __init__(self, client: BybitClient, market: MarketDataService, settings: SettingsStore, session_factory,
                 cache_dir=DEFAULT_CACHE) -> None:
        self.data = ResearchData(client, cache_dir)
        self.market = market
        self.settings = settings
        self._session_factory = session_factory
        self.cache_dir = cache_dir
        self._forensics: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self.study_status: dict[str, Any] = {"state": "idle", "started_at": None, "finished_at": None, "error": None,
                                             "step": None}
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ signals
    async def signals_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        async with self._session_factory()() as session:
            rows = (await session.execute(select(Signal).where(Signal.candle_close_time >= start,
                                                               Signal.candle_close_time < end)
                                          .order_by(Signal.candle_close_time))).scalars().all()
        return [signal_to_dict(s) for s in rows]

    async def signal(self, signal_id: str) -> dict[str, Any] | None:
        async with self._session_factory()() as session:
            s = await session.get(Signal, signal_id)
            return signal_to_dict(s) if s else None

    async def _frames(self, sig: dict[str, Any]) -> dict[str, Any]:
        s = self.settings.current
        tf = sig["timeframe"]
        t = pd.Timestamp(sig["candle_open_time"])
        warm = tf_delta(tf) * (LIVE_WINDOW + 50)
        end = t + tf_delta(tf) * (s.evaluation.max_bars + 30)
        df = await self.data.candles(sig["symbol"], tf, t - warm, end)
        htf = {}
        for h in sorted(set(s.strategy.zones.htf_timeframes) | set(s.strategy.mtf.timeframes), key=lambda x: TIMEFRAMES[x]):
            if TIMEFRAMES[h] > TIMEFRAMES[tf]:
                htf[h] = await self.data.candles(sig["symbol"], h, t - tf_delta(h) * (LIVE_WINDOW + 50), end)
        minute = None
        try:
            minute = await self.data.candles(sig["symbol"], "1m", t, min(end, utcnow()))
        except ExchangeError:
            pass
        funding = None
        try:
            funding = await self.data.funding(sig["symbol"], t - timedelta(days=1), min(end, utcnow()))
        except ExchangeError:
            pass
        btc = {}
        if sig["symbol"] != "BTCUSDT":
            btc[tf] = await self.data.candles("BTCUSDT", tf, t - warm, t + tf_delta(tf))
        return {"df": df, "htf": htf, "minute": minute, "funding": funding, "btc": btc}

    async def _corr_book(self, symbols: list[str], at: pd.Timestamp) -> CorrelationBook:
        closes = {}
        for s in symbols:
            closes[s] = await self.data.candles(s, "1h", at - timedelta(days=12), at + timedelta(hours=1))
        return CorrelationBook.build(closes, self.settings.current.decision.portfolio.corr_window_hours)

    async def forensics(self, sig: dict[str, Any], peers: list[dict[str, Any]] | None = None,
                        corr: CorrelationBook | None = None) -> dict[str, Any]:
        key = (sig["id"], sig["evaluation_status"])
        if key in self._forensics:
            return self._forensics[key]
        s = self.settings.current
        fr = await self._frames(sig)
        if peers is None:
            t = pd.Timestamp(sig["candle_close_time"])
            peers = await self.signals_between(t - timedelta(hours=2), t + timedelta(hours=2))
        if corr is None:
            corr = await self._corr_book(sorted({p["symbol"] for p in peers} | {sig["symbol"]}),
                                         pd.Timestamp(sig["candle_close_time"]))
        try:
            rules = await self.market.get_instrument_rules(sig["symbol"])
        except ExchangeError:
            rules = None
        costs = CostModel(fee_pct=s.risk.fee_rate_pct, entry_slip_pct=s.risk.slippage_pct)
        out = await asyncio.to_thread(
            analyze_signal, sig, fr["df"], fr["htf"], s.strategy, s.evaluation.max_bars, costs, fr["funding"],
            fr["minute"], peers, fr["btc"], corr.corr, s.decision.regime, s.decision.entry_quality, 100,
            s.risk.sizing(), rules)
        out["counterfactual"] = await self._counterfactual(sig, fr, rules)
        out = jsonable(out)
        if sig["evaluation_status"] != "PENDING":
            self._forensics[key] = out
        return out

    async def _counterfactual(self, sig: dict[str, Any], fr: dict[str, Any], rules) -> dict[str, Any] | None:
        """Decision engine verdict on this signal, using the data available at its decision
        time and the shadow portfolio formed by earlier signals of the same day."""
        s = self.settings.current
        rec = await asyncio.to_thread(reconstruct, sig, fr["df"], fr["htf"], s.strategy, LIVE_WINDOW,
                                      s.risk.sizing(), rules)
        if not rec.get("ok") or rec["evaluation"].setup is None:
            return None
        return {"features": rec["features"], "evaluation": rec["evaluation"], "htf": rec["htf_features"],
                "df": fr["df"], "funding": fr["funding"]}

    def _simulate_plan(self, sig: dict[str, Any], cf: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any] | None:
        """Cost-aware hypothetical outcome of the decision engine's proposed (shadow) plan."""
        s = self.settings.current
        df = cf["df"]
        tf = sig["timeframe"]
        ff = compute_features(df, tf, s.strategy)
        j = int(np.searchsorted(ff.open_time, pd.Timestamp(sig["candle_open_time"]).value))
        if j >= len(ff) or ff.open_time[j] != pd.Timestamp(sig["candle_open_time"]).value:
            return None
        o = simulate_trade(sig["direction"], ff.open, ff.high, ff.low, ff.close, ff.open_time,
                           int(tf_delta(tf).total_seconds()) * 1_000_000_000, j, plan["stop_loss"], plan["take_profit"],
                           s.evaluation.max_bars, CostModel(fee_pct=s.risk.fee_rate_pct, entry_slip_pct=s.risk.slippage_pct),
                           prepare_funding(cf.get("funding")), allow_open=False)
        exit_ns = int(ff.open_time[o.exit_index]) + int(tf_delta(tf).total_seconds() * 1e9) if o.status == "FILLED" else None
        return {"status": o.status, "reason": o.reason, "gross_r": o.gross_r if o.status == "FILLED" else None,
                "net_r": o.net_r if o.status == "FILLED" else None, "exit_ns": exit_ns,
                "exit_time": pd.Timestamp(exit_ns, tz="UTC").isoformat() if exit_ns else None,
                "label": "counterfactual simulation, not a trade"}

    async def today(self, day: date | None, tz: str = "Asia/Colombo") -> dict[str, Any]:
        z = ZoneInfo(tz)
        day = day or datetime.now(z).date()
        start = datetime(day.year, day.month, day.day, tzinfo=z)
        end = start + timedelta(days=1)
        sigs = await self.signals_between(start, end)
        if not sigs:
            return {"date": day.isoformat(), "timezone": tz, "signals": [], "summary": None}
        corr = await self._corr_book(sorted({x["symbol"] for x in sigs}), pd.Timestamp(sigs[-1]["candle_close_time"]))
        rows, replay_open, replay_closed = [], [], []
        study = active_study_filters(self.cache_dir)
        btc_cache: dict[str, Any] = {}
        for sig in sigs:
            try:
                fx = await self.forensics(sig, sigs, corr)
            except Exception as exc:  # noqa: BLE001
                log.exception("forensics failed", extra={"signal_id": sig["id"]})
                fx = {"error": str(exc)}
            cf = fx.pop("counterfactual", None) if isinstance(fx, dict) else None
            decision = None
            if cf:
                dec_ns = pd.Timestamp(sig["candle_close_time"]).value
                still = [p for p in replay_open if p["exit_ns"] is None or p["exit_ns"] > dec_ns]
                closed = [p for p in replay_open if p["exit_ns"] is not None and p["exit_ns"] <= dec_ns]
                replay_closed += closed
                replay_open = still
                day_r = sum(p["r"] for p in replay_closed)
                streak = 0
                for p in sorted(replay_closed, key=lambda p: p["exit_ns"], reverse=True):
                    if p["r"] > 0:
                        break
                    streak += 1
                f = cf["features"]
                tf = sig["timeframe"]
                if tf not in btc_cache and sig["symbol"] != "BTCUSDT":
                    btc_cache[tf] = None
                cluster_prior = sum(1 for o in sigs if o["symbol"] != sig["symbol"] and o["direction"] == sig["direction"]
                                    and 0 <= dec_ns - pd.Timestamp(o["candle_close_time"]).value <= 3_600_000_000_000)
                decision = await asyncio.to_thread(
                    decide, cf["evaluation"], f, sig["symbol"], s_dec(self.settings), data_ok=True,
                    open_positions=[{"symbol": p["symbol"], "timeframe": p["timeframe"], "direction": p["direction"]}
                                    for p in replay_open],
                    realized_today_r=day_r, losing_streak=streak,
                    last_loss_ns=max((p["exit_ns"] for p in replay_closed if p["r"] <= 0), default=None),
                    corr_fn=lambda a, b, t=dec_ns: corr.corr(a, b, t), btc_features=None,
                    htf4h_features=cf["htf"].get("4h"), study=study, now_ns=dec_ns, cluster_prior=cluster_prior)
                if decision["outcome"] == "QUALIFIED":
                    plan = decision.get("proposed_plan")
                    sim = self._simulate_plan(sig, cf, plan) if plan else None
                    if sim is None:  # no shadow plan: the production plan is the proposed plan
                        ver = (fx.get("verification") or {}) if isinstance(fx, dict) else {}
                        exit_t = (ver.get("replay_gross") or {}).get("exit_time")
                        sim = {"status": "FILLED", "net_r": (ver.get("replay_net") or {}).get("r"),
                               "gross_r": (ver.get("replay_gross") or {}).get("r"),
                               "exit_ns": pd.Timestamp(exit_t).value if exit_t else None, "exit_time": exit_t,
                               "label": "counterfactual simulation, not a trade"}
                    decision["proposed_outcome"] = sim
                    if sim.get("status") == "FILLED":
                        replay_open.append({"symbol": sig["symbol"], "timeframe": sig["timeframe"],
                                            "direction": sig["direction"], "r": sim.get("net_r") or 0.0,
                                            "exit_ns": sim.get("exit_ns")})
                decision = jsonable(decision)
            rows.append({"signal": sig, "forensics": fx, "counterfactual_decision": decision})
        return {"date": day.isoformat(), "timezone": tz, "signals": rows, "summary": summarize_day(rows),
                "study": {k: study.get(k) for k in ("available", "keys", "combined_verdict", "positive_expectancy",
                                                    "generated_at")}}

    # ------------------------------------------------------------------ study
    def study(self) -> dict[str, Any] | None:
        p = self.cache_dir / "study_latest.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def start_study(self, rebuild: bool, days: int) -> dict[str, Any]:
        if self._task and not self._task.done():
            return self.study_status
        self.study_status = {"state": "running", "started_at": utcnow().isoformat(), "finished_at": None,
                             "error": None, "step": "starting"}
        self._task = asyncio.create_task(self._run_study(rebuild, days), name="research-study")
        return self.study_status

    async def _run_study(self, rebuild: bool, days: int) -> None:
        from app.research import build, study
        from app.research.fetch import WATCHLIST
        try:
            s = self.settings.current
            symbols = list(dict.fromkeys(WATCHLIST + s.watchlist))
            if rebuild:
                self.study_status["step"] = "fetching history"
                now = utcnow()
                for sym in symbols:
                    for tf in ("4h", "1h", "15m"):
                        await self.data.candles(sym, tf, now - timedelta(days=days))
                    await self.data.funding(sym, now - timedelta(days=days))
                self.study_status["step"] = "replaying the strategy on history"
                await asyncio.to_thread(build.main, 60, ["15m", "1h", "4h"], 6)
            self.study_status["step"] = "running experiments"
            await asyncio.to_thread(study.main, self.cache_dir)
            self.study_status.update(state="done", finished_at=utcnow().isoformat(), step=None)
        except Exception as exc:  # noqa: BLE001
            log.exception("research study failed")
            self.study_status.update(state="failed", finished_at=utcnow().isoformat(), error=str(exc))


def s_dec(settings: SettingsStore):
    return settings.current.decision


def summarize_day(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [r for r in rows if r["signal"]["evaluation_status"] not in ("PENDING", "INVALIDATED")]
    gross = [r["signal"]["evaluation"].get("r_multiple") for r in done if r["signal"].get("evaluation")]
    net = []
    for r in done:
        fx = r["forensics"] or {}
        v = ((fx.get("verification") or {}).get("replay_net") or {}).get("r")
        if v is not None:
            net.append(v)
    taken = [r for r in done if (r.get("counterfactual_decision") or {}).get("outcome") == "QUALIFIED"]
    taken_net = [((r["counterfactual_decision"] or {}).get("proposed_outcome") or {}).get("net_r") for r in taken]
    taken_net = [x for x in taken_net if x is not None]
    causes: dict[str, dict[str, int]] = {}
    for r in done:
        for c in (r["forensics"] or {}).get("causes", []):
            causes.setdefault(c["code"], {"confirmed": 0, "suspected": 0, "undetermined": 0})[c["status"]] += 1

    def stats(xs):
        xs = [x for x in xs if x is not None and math.isfinite(x)]
        if not xs:
            return None
        return {"n": len(xs), "wins": sum(1 for x in xs if x > 0), "win_rate_pct": round(100 * np.mean([x > 0 for x in xs]), 1),
                "total_r": round(float(sum(xs)), 3), "avg_r": round(float(np.mean(xs)), 3)}

    return {"signals": len(rows), "resolved": len(done), "pending": sum(1 for r in rows if r["signal"]["evaluation_status"] == "PENDING"),
            "reported_gross": stats(gross), "verified_net": stats(net),
            "counterfactual_taken": stats(taken_net), "counterfactual_taken_count": len(taken),
            "cause_counts": causes,
            "reproduced_all": all(((r["forensics"] or {}).get("verification") or {}).get("reproduced") for r in done)
            if done else None}
