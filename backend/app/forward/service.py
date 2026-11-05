"""Forward paper-trading service: runs the three variants in parallel on live data.

Hooks (called by the scanner and the market stream):

* ``on_evaluation``: after the scanner evaluates a newly closed candle. Records MISSED
  signals for candles that closed while the tracker was not running (re-evaluated with
  ``evaluate_at`` on closed data only), and for a QUALIFIED setup creates one paper record
  per variant: ACCEPTED (filled at the live bid/ask, or UNFILLED), REJECTED (variant rule)
  or MISSED (detected too late). Then reconciles open trades against the closed candle.
* ``on_ticker``: live exit monitoring on the closing side of the book (bid for longs, ask
  for shorts) and funding accrual at each funding time.

Nothing is simulated ahead of time: entries use the quote at detection, exits use prices
as they arrive (or, if the tracker was down, the candles that closed meanwhile, flagged
``candle_reconstructed``). Funding is estimated from the live predicted rate and replaced
by Bybit's funding history when the trade closes, if available.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import case, func, select, update

from app.db.models import ForwardCursor, PaperTrade, StrategyVariant
from app.forward.costs import Quote, check_exit, entry_fill, exit_fill, settle, trigger_price
from app.forward.risk import AccountState, Position, check_entry
from app.forward.settings import ValidationSettings
from app.forward.variants import VARIANT_RULES, plan_for, variant_config, variant_version
from app.notifications.messages import (
    breaker_event,
    breaker_reset_event,
    missing_price_event,
    paper_exit_event,
    paper_level_event,
    paper_record_events,
    risk_entry_event,
    strategy_group_key,
    strategy_summary_event,
    validation_decision_event,
    validation_period_event,
)
from app.risk.sizing import SizingConfig, size_position
from app.signals.identity import signal_id
from app.strategy.engine import Evaluation, evaluate_at
from app.strategy.features import Features
from app.timeutil import tf_delta, utcnow

log = logging.getLogger(__name__)
FUNDING_INTERVAL = timedelta(hours=8)


def trade_id(variant_id: str, symbol: str, tf: str, direction: str, candle_open: datetime, strategy: str) -> str:
    raw = f"{variant_id}|{symbol}|{tf}|{direction}|{candle_open.isoformat()}|{strategy}"
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def _dt(ns: int) -> datetime:
    from datetime import UTC
    return datetime.fromtimestamp(ns / 1e9, UTC)


class ForwardService:
    def __init__(self, market, settings_store, session_factory, client=None) -> None:
        self.market = market
        self.settings = settings_store
        self._session_factory = session_factory
        self.client = client
        self.variants: dict[str, StrategyVariant] = {}
        self.open: dict[str, dict[str, Any]] = {}  # trade id -> row dict (OPEN only)
        self.drawdown_reset: dict[str, datetime] = {}
        self._lock = asyncio.Lock()
        self._loaded = False
        self.status: dict[str, Any] = {"records": 0, "last_error": None, "last_evaluation_at": None, "last_event_at": None}
        self.center = None  # NotificationService (Phase 7), set by the container

    @property
    def vs(self) -> ValidationSettings:
        return self.settings.current.validation

    # ------------------------------------------------------------------ setup
    async def ensure_variants(self, now: datetime | None = None) -> dict[str, StrategyVariant]:
        """Register the current immutable configurations; close periods of changed ones."""
        now = now or utcnow()
        sv = self.settings.current.strategy.version
        async with self._session_factory()() as session:
            rows = (await session.execute(select(StrategyVariant).where(StrategyVariant.active.is_(True)))).scalars().all()
            active = {r.key: r for r in rows}
            out = {}
            started = []
            for key in VARIANT_RULES:
                cfg = variant_config(key, sv, self.vs)
                ver = variant_version(cfg)
                vid = f"{key}@{ver}"
                cur = active.get(key)
                if cur is not None and cur.id == vid:
                    out[key] = cur
                    continue
                if cur is not None:
                    cur.active = False
                    cur.period_end = now
                existing = await session.get(StrategyVariant, vid)
                if existing is None:
                    existing = StrategyVariant(id=vid, key=key, name=VARIANT_RULES[key]["name"], version=ver,
                                               config=cfg, period_start=now, active=True)
                    session.add(existing)
                    if cur is not None:  # the very first period is not a configuration change
                        started.append(validation_period_event(key, vid, ver, now))
                else:
                    existing.active, existing.period_end = True, None
                out[key] = existing
            await session.commit()
            for v in out.values():
                await session.refresh(v)
        self.variants = out
        if self.center is not None:
            for event in started:
                await self.center.emit(event)
        return out

    def period_changes(self, new) -> list[str]:
        """Variants whose immutable version would change under ``new`` runtime settings."""
        cur = self.settings.current
        return [k for k in VARIANT_RULES
                if variant_version(variant_config(k, new.strategy.version, new.validation))
                != variant_version(variant_config(k, cur.strategy.version, cur.validation))]

    async def startup(self) -> None:
        """Resume monitoring open trades as soon as the process starts. Without this, exits are
        only checked after the next candle evaluation has loaded them."""
        if self.vs.enabled:
            await self._ready()

    async def load_open(self) -> None:
        async with self._session_factory()() as session:
            rows = (await session.execute(select(PaperTrade).where(PaperTrade.status == "OPEN"))).scalars().all()
        self.open = {r.id: _row(r) for r in rows}
        self._loaded = True

    def _current_ids(self) -> dict[str, str]:
        sv = self.settings.current.strategy.version
        return {k: f"{k}@{variant_version(variant_config(k, sv, self.vs))}" for k in VARIANT_RULES}

    async def _ready(self) -> None:
        if not self.variants or {k: v.id for k, v in self.variants.items()} != self._current_ids():
            await self.ensure_variants()  # configuration changed -> new immutable evaluation period
            await self._load_resets()
        if not self._loaded:
            await self.load_open()

    # ------------------------------------------------------------------ account state
    async def account(self, key: str) -> AccountState:
        v = self.variants[key]
        async with self._session_factory()() as session:
            rows = (await session.execute(select(PaperTrade).where(
                PaperTrade.variant_id == v.id, PaperTrade.portfolio_taken.is_(True),
                PaperTrade.status.in_(("OPEN", "CLOSED"))))).scalars().all()
        realized = [(r.exit_time, r.net_pnl or 0.0) for r in rows if r.status == "CLOSED" and r.exit_time]
        open_ = [Position(r.symbol, r.timeframe, r.direction, r.risk_amount or 0.0,
                          (r.quantity or 0) * (r.fill_price or 0)) for r in rows if r.status == "OPEN"]
        return AccountState(self.vs.paper.starting_equity, realized, open_, self.drawdown_reset.get(key))

    # ------------------------------------------------------------------ scanner hook
    async def on_evaluation(self, symbol: str, tf: str, f: Features, ev: Evaluation, data_ok: bool, now: datetime,
                            htf: dict[str, Features], rules, corr: Callable[[str, str], float] | None) -> None:
        if not self.vs.enabled:
            return
        try:
            async with self._lock:
                await self._ready()
                await self._missed_candles(symbol, tf, f, htf, now)
                if ev.setup is not None and ev.setup.status == "QUALIFIED":
                    await self._record_signal(symbol, tf, f, ev, data_ok, now, rules, corr)
                await self._reconcile(symbol, tf, f, now)
            self.status["last_error"] = None
            self.status["last_evaluation_at"] = utcnow().isoformat()
            if self.center is not None:
                await self.center.publish()
        except Exception as exc:  # noqa: BLE001 - never interrupt scanning
            self.status["last_error"] = str(exc)
            log.exception("forward tracker failed", extra={"symbol": symbol, "timeframe": tf})

    async def _missed_candles(self, symbol: str, tf: str, f: Features, htf: dict[str, Features], now: datetime) -> None:
        last_open = _dt(int(f.open_time[-1]))
        async with self._session_factory()() as session:
            cur = await session.get(ForwardCursor, (symbol, tf))
            prev = cur.last_candle_open if cur else None
            if cur is None:
                session.add(ForwardCursor(symbol=symbol, timeframe=tf, last_candle_open=last_open))
            else:
                cur.last_candle_open = last_open
            await session.commit()
        if prev is None or last_open <= prev:
            return
        p = self.settings.current.strategy
        lo = int(np.searchsorted(f.open_time, int(prev.timestamp() * 1e9), side="right"))
        hi = len(f) - 1  # the latest candle is handled live
        for i in range(max(lo, len(f) - 200), hi):
            ev = evaluate_at(f, i, p, htf, context=False)
            if ev.setup is None:
                continue
            rows = [self._base_row(key, symbol, tf, f, i, ev.setup, now, "MISSED",
                                   "candle closed while the forward tracker was not running", "MISSED")
                    for key in self.variants]
            await self._insert_all(rows, symbol, tf, ev.setup.direction, now)

    def _base_row(self, key: str, symbol: str, tf: str, f: Features, i: int, st, now: datetime, decision: str,
                  reason: str, status: str) -> dict[str, Any]:
        v = self.variants[key]
        co = _dt(int(f.open_time[i]))
        return {"id": trade_id(v.id, symbol, tf, st.direction, co, st.strategy), "variant_id": v.id, "variant_key": key,
                "symbol": symbol, "timeframe": tf, "direction": st.direction, "strategy": st.strategy,
                "decision": decision, "decision_reason": reason, "portfolio_taken": False, "portfolio_reason": None,
                "status": status, "detected_at": now, "candle_open_time": co, "candle_close_time": _dt(int(f.close_time[i])),
                "reference_entry": st.entry_reference, "cost_flags": {}, "data": {"score": st.score,
                "mtf_confirmed": st.mtf_confirmed, "atr": st.atr, "signal_id": self._signal_id(symbol, tf, st, co)},
                "updated_at": now,
                "entry_fee": 0.0, "exit_fee": 0.0, "slippage_cost": 0.0, "spread_cost": 0.0, "funding_cost": 0.0}

    def _signal_id(self, symbol: str, tf: str, st, candle_open: datetime) -> str | None:
        try:
            return signal_id(self.market.exchange, symbol, tf, st.direction, candle_open, st.strategy_version, st.strategy_name)
        except AttributeError:  # no exchange known: the record simply has no link to a signal
            return None

    async def _insert(self, row: dict[str, Any], group_key: str | None = None) -> bool:
        async with self._session_factory()() as session:
            if await session.get(PaperTrade, row["id"]) is not None:
                return False
            session.add(PaperTrade(**row))
            if self.center is not None:  # committed together with the record (outbox pattern)
                for event in self._record_events(row, group_key):
                    await self.center.add(session, event)
            await session.commit()
        self.status["records"] += 1
        self.status["last_event_at"] = utcnow().isoformat()
        return True

    @staticmethod
    def _record_events(row: dict[str, Any], group_key: str | None) -> list:
        events = paper_record_events(row, group_key)
        code = row["data"].get("risk_code")
        if code and code != "ok":
            risk = risk_entry_event(row, code, group_key)
            if risk is not None:
                events.append(risk)
        if row["status"] == "UNFILLED" and row["data"].get("no_price"):
            events.append(missing_price_event(row, group_key))
        return events

    async def _insert_all(self, rows: list[dict[str, Any]], symbol: str, tf: str, direction: str,
                          now: datetime) -> list[dict[str, Any]]:
        """Records of one signal in A, B and C, plus the grouped summary. Their outcomes and results
        stay separate records; the group is only a shared view of them. Returns the new rows."""
        if not rows:
            return []
        key = strategy_group_key(symbol, tf, direction, rows[0]["candle_open_time"])
        new = [r for r in rows if await self._insert(r, key)]
        if new and self.center is not None:
            await self.center.emit(strategy_summary_event(symbol, tf, direction, rows[0]["candle_open_time"], key, rows,
                                                          rows[0]["data"].get("signal_id"), now))
        return new

    async def _record_signal(self, symbol: str, tf: str, f: Features, ev: Evaluation, data_ok: bool, now: datetime,
                             rules, corr) -> None:
        st = ev.setup
        i = ev.index
        pc, lim = self.vs.paper, self.vs.risk
        delay = (now - _dt(int(f.close_time[i]))).total_seconds()
        late = delay > pc.max_signal_delay_candles * tf_delta(tf).total_seconds()
        tick = self.market.state.ticker_view(symbol) if self.market is not None else {}
        # Quote timestamps come from the local clock, so their age must be measured with it too
        # (``now`` is the exchange-synchronised clock and may differ by seconds).
        q = Quote.from_ticker(tick, utcnow()) if tick.get("available") else Quote(None, None, None, None)
        records: list[dict[str, Any]] = []
        blocked: list[tuple[str, str]] = []
        for key in self.variants:
            row = self._base_row(key, symbol, tf, f, i, st, now, "ACCEPTED", "", "OPEN")
            records.append(row)
            row["data"].update({"detection_delay_s": round(delay, 3), "quote": {"bid": q.bid, "ask": q.ask, "last": q.last,
                                "age_s": q.age_s, "fresh": q.fresh(pc)}})
            if late:
                row.update(decision="MISSED", status="MISSED",
                           decision_reason=f"detected {delay:.0f}s after the candle close (limit "
                                           f"{pc.max_signal_delay_candles:g} candle)")
                continue
            plan = plan_for(key, st)
            row["decision_reason"] = plan.reason
            if not plan.accepted:
                row.update(decision="REJECTED", status="REJECTED")
                continue
            row["stop_loss"], row["take_profit"] = plan.stop, plan.target
            fill = entry_fill(st.direction, q, pc)
            if fill is None:
                row.update(status="UNFILLED", decision_reason="no live price available at detection")
                row["data"]["no_price"] = True
                continue
            long = st.direction == "LONG"
            if (long and not plan.stop < fill.price < plan.target) or (not long and not plan.target < fill.price < plan.stop):
                row.update(status="UNFILLED", decision_reason=f"live fill {fill.price:.8g} already beyond stop or target")
                continue
            acct = await self.account(key)
            sizing = size_position(st.direction, fill.price, plan.stop,
                                   SizingConfig(account_balance=max(acct.equity(), 1e-9), risk_pct=lim.risk_per_trade_pct,
                                                fee_rate_pct=pc.taker_fee_pct, slippage_pct=pc.slippage_pct,
                                                leverage=self.settings.current.risk.leverage), rules)
            if not sizing.feasible or sizing.quantity <= 0:
                row.update(status="UNFILLED", decision_reason="; ".join(x["message"] for x in sizing.issues)
                           or "position not feasible under exchange quantity rules")
                continue
            qty = sizing.quantity
            risk_amount = qty * abs(fill.price - plan.stop)
            ok, code, why = check_entry(acct, symbol, tf, st.direction, risk_amount, lim, now, data_ok, q.fresh(pc), corr)
            row.update(fill_price=fill.price, fill_time=now, quantity=qty, risk_amount=risk_amount,
                       entry_fee=pc.taker_fee_pct / 100 * fill.price * qty,
                       spread_cost=fill.spread_per_unit * qty, slippage_cost=fill.slippage_per_unit * qty,
                       portfolio_taken=ok, portfolio_reason=why if not ok else "taken",
                       cost_flags={"entry": "observed_bid_ask" if fill.source == "quote" else "estimated_from_last_price",
                                   "slippage": f"model {pc.slippage_pct}%", "quantity": "exchange_rules" if rules else
                                   "unrounded (no instrument rules)"})
            row["data"]["risk_code"] = code
            row["data"]["next_funding"] = tick.get("next_funding_time")
            row["data"]["tp_levels"] = _tp_levels(key, st, plan)
            if code in ("daily_loss", "max_drawdown"):
                blocked.append((key, code))
        # Only rows inserted just now: a re-evaluated candle must never reopen a closed trade.
        for row in await self._insert_all(records, symbol, tf, st.direction, now):
            if row["status"] == "OPEN":
                self.open[row["id"]] = row
        for key, _code in blocked:
            await self._notify_breakers(key, now)

    # ------------------------------------------------------------------ exits
    async def on_ticker(self, symbol: str, view: dict[str, Any]) -> None:
        if not self.open or not self.vs.enabled:
            return
        now = utcnow()
        pc = self.vs.paper
        q = Quote.from_ticker(view, now)
        for tid, t in list(self.open.items()):
            if t["symbol"] != symbol or t.get("fill_time") is None or now <= t["fill_time"]:
                continue
            self._accrue_funding(t, view, now)
            tp = trigger_price(t["direction"], q, pc)
            if tp is None:
                continue
            await self._check_levels(t, tp[0], now)
            reason = check_exit(t["direction"], tp[0], t["stop_loss"], t["take_profit"])
            if reason:
                try:
                    await self._close(tid, reason, tp[0], now, "ticker_" + tp[1])
                except Exception as exc:  # noqa: BLE001 - the trade stays open and is retried on the next tick
                    self.status["last_error"] = str(exc)
                    log.exception("paper trade could not be closed", extra={"trade_id": tid})

    async def _check_levels(self, t: dict[str, Any], price: float, now: datetime) -> None:
        """Announce take-profit levels below the exit target as they are reached. These change
        nothing: a paper trade exits in full at its target or its stop."""
        if self.center is None:
            return
        long = t["direction"] == "LONG"
        hit = t["data"].setdefault("levels_hit", [])
        for level in t["data"].get("tp_levels") or []:
            if level.get("closes_trade") or level["index"] in hit:
                continue
            if (price >= level["price"]) if long else (price <= level["price"]):
                hit.append(level["index"])
                await self.center.emit(paper_level_event(t, level, price, now))

    def _accrue_funding(self, t: dict[str, Any], view: dict[str, Any], now: datetime) -> None:
        nxt = t["data"].get("next_funding")
        rate = view.get("funding_rate")
        if not nxt or rate is None:
            return
        when = datetime.fromisoformat(nxt)
        if now < when:
            t["data"]["pending_rate"] = rate  # last predicted rate before settlement
            return
        r = t["data"].get("pending_rate", rate)
        mark = view.get("mark_price") or t["fill_price"]
        sign = 1 if t["direction"] == "LONG" else -1
        cost = t["quantity"] * mark * r * sign
        t["funding_cost"] = t.get("funding_cost", 0.0) + cost
        t["data"].setdefault("funding_events", []).append({"time": nxt, "rate": r, "cost": cost})
        t["data"]["next_funding"] = (when + FUNDING_INTERVAL).isoformat()
        t["cost_flags"]["funding"] = "estimated_from_predicted_rate"

    async def _reconcile(self, symbol: str, tf: str, f: Features, now: datetime) -> None:
        """Candle-based checks for open trades: touches the ticker missed (downtime) and the
        time exit after ``max_holding_bars`` candles of the trade's timeframe."""
        pc = self.vs.paper
        for tid, t in list(self.open.items()):
            if t["symbol"] != symbol or t["timeframe"] != tf or t.get("fill_time") is None:
                continue
            fill_ns = int(t["fill_time"].timestamp() * 1e9)
            after = np.nonzero(f.open_time >= fill_ns)[0]  # candles that opened after the fill
            checked = t["data"].get("checked_until_ns", 0)
            long = t["direction"] == "LONG"
            for k in after:
                if int(f.open_time[k]) <= checked:
                    continue
                hs = f.low[k] <= t["stop_loss"] if long else f.high[k] >= t["stop_loss"]
                ht = f.high[k] >= t["take_profit"] if long else f.low[k] <= t["take_profit"]
                t["data"]["checked_until_ns"] = int(f.open_time[k])
                if hs or ht:
                    gap = f.open[k] <= t["stop_loss"] if long else f.open[k] >= t["stop_loss"]
                    trig = float(f.open[k]) if (hs and gap) else (t["stop_loss"] if hs else t["take_profit"])
                    await self._close(tid, "STOP" if hs else "TARGET", trig, _dt(int(f.close_time[k])),
                                      "candle_reconstructed" + ("_ambiguous_stop_first" if hs and ht else ""))
                    break
            else:
                if len(after) >= pc.max_holding_bars:
                    k = after[pc.max_holding_bars - 1]
                    await self._close(tid, "TIME_EXIT", float(f.close[k]), _dt(int(f.close_time[k])), "candle_close")

    async def _close(self, tid: str, reason: str, trigger: float, when: datetime, source: str) -> None:
        t = self.open.pop(tid, None)  # popped first, so two price updates cannot close it twice
        if t is None:
            return
        try:
            await self._settle(tid, t, reason, trigger, when, source)
        except Exception:
            self.open[tid] = t  # still OPEN in the database: keep monitoring it rather than forget it
            raise
        self.status["last_event_at"] = utcnow().isoformat()
        if self.center is not None:
            await self.center.publish()
            await self._notify_breakers(t["variant_key"], when)

    async def _settle(self, tid: str, t: dict[str, Any], reason: str, trigger: float, when: datetime, source: str) -> None:
        pc = self.vs.paper
        fx = exit_fill(t["direction"], reason, trigger, t["take_profit"], pc)
        funding = t.get("funding_cost", 0.0)
        flags = dict(t.get("cost_flags") or {})
        actual = await self._actual_funding(t, when)
        if actual is not None:
            funding = actual
            flags["funding"] = "exchange_funding_history"
        elif not t["data"].get("funding_events"):
            flags.setdefault("funding", "none_charged")
        res = settle(t["direction"], t["quantity"], t["fill_price"], t["stop_loss"], fx.price, reason, funding, pc)
        flags["exit"] = source
        async with self._session_factory()() as session:
            await session.execute(update(PaperTrade).where(PaperTrade.id == tid).values(
                status="CLOSED", exit_time=when, exit_price=fx.price, exit_reason=reason, gross_pnl=res["gross_pnl"],
                net_pnl=res["net_pnl"], net_r=res["net_r"], exit_fee=res["exit_fee"], funding_cost=funding,
                slippage_cost=t.get("slippage_cost", 0.0) + fx.slippage_per_unit * t["quantity"], cost_flags=flags,
                data=t["data"], updated_at=utcnow()))
            if self.center is not None:
                closed = {**t, "status": "CLOSED", "exit_time": when, "exit_price": fx.price, "exit_reason": reason,
                          "gross_pnl": res["gross_pnl"], "net_pnl": res["net_pnl"], "net_r": res["net_r"],
                          "exit_fee": res["exit_fee"], "funding_cost": funding, "cost_flags": flags,
                          "slippage_cost": t.get("slippage_cost", 0.0) + fx.slippage_per_unit * t["quantity"]}
                await self.center.add(session, paper_exit_event(closed, _exit_tp_index(t)))
            await session.commit()

    async def _notify_breakers(self, key: str, now: datetime) -> None:
        """Announce a paper circuit breaker once per trip: once per UTC day for the daily loss limit,
        once per reset generation for the drawdown limit."""
        from app.forward.risk import breakers
        v = self.variants.get(key)
        if self.center is None or v is None:
            return
        try:
            acct = await self.account(key)
            br = breakers(acct, self.vs.risk, now)
            positions = [{"symbol": p.symbol, "timeframe": p.timeframe, "direction": p.direction,
                          "risk_amount": p.risk_amount} for p in acct.open]
            if br["daily_loss"]["tripped"]:
                await self.center.emit(breaker_event(key, v.id, v.version, "daily_loss", br["daily_loss"], now,
                                                     now.date().isoformat(), positions))
            if br["max_drawdown"]["tripped"]:
                gen = self.drawdown_reset[key].isoformat() if key in self.drawdown_reset else "initial"
                await self.center.emit(breaker_event(key, v.id, v.version, "max_drawdown", br["max_drawdown"], now, gen, positions))
        except Exception:  # noqa: BLE001
            log.exception("circuit breaker notification failed", extra={"variant": key})

    async def _actual_funding(self, t: dict[str, Any], when: datetime) -> float | None:
        if self.client is None or not hasattr(self.client, "get_funding_history"):
            return None
        if when - t["fill_time"] < timedelta(minutes=1):
            return 0.0
        try:
            rows = await self.client.get_funding_history(t["symbol"], t["fill_time"], when)
        except Exception:  # noqa: BLE001
            return None
        sign = 1 if t["direction"] == "LONG" else -1
        return sum(t["quantity"] * t["fill_price"] * r["rate"] * sign for r in rows
                   if t["fill_time"] < r["funding_time"] <= when)

    # ------------------------------------------------------------------ manual controls
    async def _load_resets(self) -> None:
        from app.db.models import PromotionDecision
        async with self._session_factory()() as session:
            rows = (await session.execute(select(PromotionDecision).where(
                PromotionDecision.decision == "DRAWDOWN_RESET").order_by(PromotionDecision.created_at))).scalars().all()
        ids = {v.id: k for k, v in self.variants.items()}
        for r in rows:
            if r.variant_id in ids:
                self.drawdown_reset[ids[r.variant_id]] = r.created_at

    async def reset_drawdown(self, key: str, note: str, reviewer: str) -> None:
        """Manual reset of the max-drawdown circuit breaker (recorded with a reason)."""
        from app.db.models import PromotionDecision
        await self._ready()
        now = utcnow()
        async with self._session_factory()() as session:
            session.add(PromotionDecision(variant_id=self.variants[key].id, decision="DRAWDOWN_RESET", readiness="n/a",
                                          assessment={}, note=note, reviewer=reviewer, created_at=now))
            await session.commit()
        self.drawdown_reset[key] = now
        if self.center is not None:
            v = self.variants[key]
            await self.center.emit(breaker_reset_event(key, v.id, v.version, note, reviewer, now))

    # ------------------------------------------------------------------ results
    async def rows(self, variant_id: str) -> list[dict[str, Any]]:
        async with self._session_factory()() as session:
            rs = (await session.execute(select(PaperTrade).where(PaperTrade.variant_id == variant_id)
                                        .order_by(PaperTrade.detected_at))).scalars().all()
        return [{c.name: getattr(r, c.name) for c in PaperTrade.__table__.columns} for r in rs]

    async def overview(self, corr=None) -> dict[str, Any]:
        from app.db.models import PromotionDecision
        from app.forward.report import assess, test_status, variant_report
        from app.forward.risk import breakers, exposure
        await self._ready()
        now = utcnow()
        gate = self.vs.gate
        tests = test_status()
        async with self._session_factory()() as session:
            periods = (await session.execute(select(StrategyVariant).order_by(StrategyVariant.period_start))).scalars().all()
            counts = {r[0]: r[1:] for r in (await session.execute(select(
                PaperTrade.variant_id, func.count(), func.sum(case((PaperTrade.status == "CLOSED", 1), else_=0)),
                func.avg(PaperTrade.net_r)).group_by(PaperTrade.variant_id))).all()}
            decisions = (await session.execute(select(PromotionDecision).order_by(PromotionDecision.created_at.desc())
                                               .limit(50))).scalars().all()
        out: dict[str, Any] = {"generated_at": now.isoformat(), "label": "FORWARD paper trading on live data "
                               "(only signals detected after each period started)", "variants": {},
                               "tests": tests, "gate": gate.model_dump(), "risk_limits": self.vs.risk.model_dump(),
                               "paper_costs": self.vs.paper.model_dump(), "tracker": self.status,
                               "periods": [{"id": p.id, "key": p.key, "name": p.name, "period_start": p.period_start.isoformat(),
                                            "period_end": p.period_end.isoformat() if p.period_end else None,
                                            "version": p.version, "active": p.active,
                                            "records": counts.get(p.id, (0, 0, None))[0],
                                            "closed_trades": counts.get(p.id, (0, 0, None))[1],
                                            "avg_net_r": counts.get(p.id, (0, 0, None))[2]} for p in periods],
                               "decisions": [{"variant_id": d.variant_id, "decision": d.decision, "readiness": d.readiness,
                                              "note": d.note, "reviewer": d.reviewer, "created_at": d.created_at.isoformat()}
                                             for d in decisions]}
        for key, v in self.variants.items():
            rows = await self.rows(v.id)
            vd = {"id": v.id, "key": key, "name": v.name, "version": v.version, "period_start": v.period_start,
                  "config": v.config}
            rep = variant_report(rows, vd, self.vs.paper.starting_equity, now, gate)
            acct = await self.account(key)
            br = breakers(acct, self.vs.risk, now)
            rep["risk"] = {"exposure": exposure(acct, corr), "breakers": br}
            rep["promotion"] = assess(rep, rows, gate, now, tests, br["max_drawdown"]["tripped"])
            rep["recent"] = [_public(r) for r in rows[-100:]][::-1]
            out["variants"][key] = rep
        return out

    async def record_decision(self, key: str, decision: str, note: str, reviewer: str, corr=None) -> dict[str, Any]:
        """Manual promotion review. APPROVED is refused unless the gate says READY_FOR_REVIEW,
        and even then nothing in the production engine changes."""
        from app.db.models import PromotionDecision
        ov = await self.overview(corr)
        a = ov["variants"][key]["promotion"]
        if decision == "APPROVED" and a["readiness"] != "READY_FOR_REVIEW":
            raise ValueError(f"cannot approve: readiness is {a['readiness']}")
        async with self._session_factory()() as session:
            session.add(PromotionDecision(variant_id=self.variants[key].id, decision=decision, readiness=a["readiness"],
                                          assessment=a, note=note, reviewer=reviewer, created_at=utcnow()))
            await session.commit()
        if self.center is not None:
            v = self.variants[key]
            await self.center.emit(validation_decision_event(key, v.id, v.version, decision, a["readiness"], note, reviewer, utcnow()))
        return {"recorded": True, "decision": decision, "readiness": a["readiness"],
                "production_changed": False, "note": "Recorded only. Production signals and enforcement are unchanged."}


def _public(r: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()}


def _row(r: PaperTrade) -> dict[str, Any]:
    return {c: getattr(r, c) for c in ("id", "variant_id", "variant_key", "symbol", "timeframe", "direction", "status",
                                         "fill_price", "fill_time", "stop_loss", "take_profit", "quantity", "risk_amount",
                                         "funding_cost", "slippage_cost", "reference_entry", "entry_fee", "spread_cost",
                                         "portfolio_taken", "portfolio_reason", "strategy", "decision", "decision_reason",
                                         "detected_at")} | {"cost_flags": dict(r.cost_flags or {}), "data": dict(r.data or {})}


def _tp_levels(key: str, st, plan) -> list[dict[str, Any]]:
    """Levels shown for a trade. Baseline uses the production targets (the primary one closes the
    trade); B and C have a single 2R target, so it is their TP1."""
    targets = (st.plan or {}).get("targets") or []
    if VARIANT_RULES[key]["stop"] != "production" or not targets:
        return [{"index": 1, "price": plan.target, "closes_trade": True}]
    primary = (st.plan or {}).get("primary_target_index", 0)
    return [{"index": k + 1, "price": tgt["price"], "closes_trade": k == primary} for k, tgt in enumerate(targets[:primary + 1])]


def _exit_tp_index(t: dict[str, Any]) -> int:
    closing = [lv["index"] for lv in t["data"].get("tp_levels") or [] if lv.get("closes_trade")]
    return closing[0] if closing else 1


def _finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)
