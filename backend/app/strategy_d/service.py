"""Forward shadow run of Strategy D. It has no effect on signals, alerts or A, B and C.

For each newly closed 15m candle it records D's decision, applies the standard paper risk limits to its
own paper account, and scores taken trades with the study's simulation and costs, so forward and replay
results can be compared. Funding isn't charged because candles don't carry it. Missed candles aren't
backfilled. Decisions reach the Notification Center labelled as research; Telegram stays off unless
``notifications.strategy_d_telegram`` is switched on. Nothing can promote D automatically.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from statistics import mean
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from app.db.models import StrategyDDecision
from app.forward.experiments import resolve, series_of
from app.forward.risk import AccountState, Position, breakers, check_entry, exposure
from app.market.service import MarketDataService
from app.planning.sizing import PositionInput, plan_position
from app.strategy.setups import htf_index
from app.strategy_d.confluence import score
from app.strategy_d.context import TfData, classify_at, nearest_levels, prepare, state_at_decision
from app.strategy_d.engine import decide
from app.strategy_d.params import CONTEXT_TIMEFRAMES, D_PARAMS, PRIMARY_TIMEFRAME, STRATEGY_ID, STRATEGY_NAME, DParams
from app.timeutil import utcnow

log = logging.getLogger(__name__)

LOAD_TIMEFRAMES = ("1d", "4h", "1h", "15m")
EVIDENCE = "Unvalidated"
LABEL = "RESEARCH SHADOW: new data only, no real orders. Not part of strategies A, B and C."
GRACE = timedelta(seconds=3)
LEVERAGE = 5.0  # only used to check that the position fits the margin; it doesn't change the risk


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:,.6g}"


def message_for(symbol: str, d: dict[str, Any], status: str, version: str) -> str:
    """The Notification Center text. Every price is calculated; nothing here is a placeholder."""
    s = d["states"]
    lines = ["Strategy D research shadow signal. No order was placed.", "Primary timeframe: 15m",
             f"1D: {s['1d']['bias'].title()}", f"4H: {s['4h']['bias'].title()}", f"1H: {s['1h']['bias'].title()}",
             f"15m: Confirmed {d['entry_label']}"]
    p = d.get("plan")
    if p:
        lines += [f"Entry: {_fmt(p['entry_reference'])}", f"Stop-loss: {_fmt(p['stop_loss'])}",
                  "TP1/TP2/TP3: " + " / ".join(_fmt(t["price"]) for t in p["targets"])]
    if d.get("risk_amount") is not None:
        lines += [f"Risk: {d['risk_amount']:,.2f} USDT", f"Position size: {_fmt(d.get('quantity'))}"]
    c = d["confluence"]
    lines += [f"Higher-timeframe alignment: score {c['score']:.0f} of {c['out_of']:.0f} (rule agreement, not a probability)",
              f"Status: {status}", d["reason"], f"Version: {version}"]
    return "\n".join(lines)


class StrategyDService:
    def __init__(self, market: MarketDataService, settings_store, session_factory, center=None, params: DParams = D_PARAMS) -> None:
        self.market = market
        self.settings = settings_store
        self._session_factory = session_factory
        self.center = center
        self.p = params
        self._frames: dict[tuple[str, str], tuple[str, TfData]] = {}
        self.latest: dict[str, dict[str, Any]] = {}
        # Counted so that an empty table can be told apart from a tracker that isn't running.
        self.status: dict[str, Any] = {"candles_checked": 0, "setups_seen": 0, "last_candle_at": None, "last_error": None}

    async def _load(self, symbol: str, now: datetime) -> dict[str, TfData]:
        out: dict[str, TfData] = {}
        for tf in LOAD_TIMEFRAMES:
            series = await self.market.get_closed_candles(symbol, tf, now)
            if series.df.empty:
                continue
            last = series.df["open_time"].iloc[-1].isoformat()
            cached = self._frames.get((symbol, tf))
            if cached is None or cached[0] != last:
                cached = (last, await asyncio.to_thread(prepare, series.df, tf, self.p))
                self._frames[(symbol, tf)] = cached
            out[tf] = cached[1]
        return out

    async def on_candle(self, symbol: str, tf: str, data_ok: bool, now: datetime, corr) -> None:
        """Called by the scanner for each newly closed candle. Failures never reach the scanner."""
        if tf != PRIMARY_TIMEFRAME:
            return
        try:
            self.status["candles_checked"] += 1
            self.status["last_candle_at"] = now.isoformat()
            data = await self._load(symbol, now - GRACE)
            if PRIMARY_TIMEFRAME not in data:
                return
            f = data[PRIMARY_TIMEFRAME].f
            i = len(f) - 1
            rules = None
            try:
                rules = await self.market.get_instrument_rules(symbol)
            except Exception:  # noqa: BLE001 - plans are just not rounded to the exchange's tick
                log.warning("instrument rules unavailable for strategy D", extra={"symbol": symbol})
            rows = await asyncio.to_thread(decide, data, i, self.p, symbol, rules.tick_size if rules else None)
            self.status["setups_seen"] += len(rows)
            for row in rows:
                await self._record(symbol, f, i, row, data_ok, now, corr, rules)
            await self._reconcile(symbol, f)
            await self._remember(symbol, data, i, rows)
            self.status["last_error"] = None
        except Exception as exc:  # noqa: BLE001 - a research strategy must never interrupt scanning
            self.status["last_error"] = str(exc)
            log.exception("strategy D failed", extra={"symbol": symbol})

    async def _remember(self, symbol: str, data: dict[str, TfData], i: int, rows: list[dict[str, Any]]) -> None:
        decision_ns = int(data[PRIMARY_TIMEFRAME].f.close_time[i])
        best = next((r for r in rows if r["decision"] == "QUALIFIED"), rows[0] if rows else None)
        self.latest[symbol] = {
            "symbol": symbol, "decision_time": datetime.fromtimestamp(decision_ns / 1e9, UTC).isoformat(),
            "biases": {tf: state_at_decision(data.get(tf), tf, decision_ns, self.p).bias for tf in (*CONTEXT_TIMEFRAMES, PRIMARY_TIMEFRAME)},
            "decision": best["decision"] if best else "NO_TRADE", "direction": best["direction"] if best else None,
            "reason": best["reason"] if best else "No confirmed 15m setup on this candle."}

    async def _account(self, session) -> tuple[AccountState, list[StrategyDDecision]]:
        rows = (await session.execute(select(StrategyDDecision).where(StrategyDDecision.strategy_version == self.p.version,
                                                                       StrategyDDecision.taken.is_(True)))).scalars().all()
        realized = [(r.exit_time, r.net_r * r.risk_amount) for r in rows if r.status == "CLOSED" and r.net_r is not None and r.risk_amount is not None and r.exit_time]
        opened = [Position(r.symbol, r.timeframe, r.direction, r.risk_amount or 0.0, r.notional or 0.0) for r in rows if r.status in ("PENDING", "OPEN")]
        return AccountState(self.p.starting_equity, realized, opened), rows

    async def _record(self, symbol: str, f: Any, i: int, row: dict[str, Any], data_ok: bool, now: datetime, corr, rules) -> None:
        signal_ns = int(f.open_time[i])
        version = self.p.version
        tid = hashlib.sha256(f"{symbol}|{row['direction']}|{row['entry_type']}|{signal_ns}|{version}".encode()).hexdigest()[:40]
        async with self._session_factory()() as session:
            if await session.get(StrategyDDecision, tid) is not None:
                return  # a re-evaluated candle must not create or reopen a decision
            taken = False
            if row["decision"] == "QUALIFIED":
                await self._apply_limits(session, symbol, f, i, row, data_ok, now, corr, rules)
                taken = row["decision"] == "QUALIFIED"
            plan = row.get("plan")
            session.add(StrategyDDecision(
                id=tid, strategy_version=version, symbol=symbol, timeframe=PRIMARY_TIMEFRAME, direction=row["direction"], entry_type=row["entry_type"],
                signal_ns=signal_ns, decision_at=now, decision=row["decision"], reason_code=row["reason_code"], reason=row["reason"],
                score=row["confluence"]["score"], data=row, taken=taken, status="PENDING" if taken else "NONE",
                stop_loss=plan["stop_loss"] if plan else None, legs=[[t["price"], t["weight"]] for t in plan["targets"]] if plan else None,
                atr_pct=100 * float(f.atr[i]) / float(f.close[i]), max_bars=self.p.max_bars,
                risk_amount=row.get("risk_amount") if taken else None, quantity=row.get("quantity") if taken else None,
                notional=row.get("notional") if taken else None))
            await session.commit()
        if row["decision"] in ("QUALIFIED", "WATCHLIST"):
            await self._notify(symbol, row, tid, now)

    async def _apply_limits(self, session, symbol: str, f: Any, i: int, row: dict[str, Any], data_ok: bool, now: datetime, corr, rules) -> None:
        """The reused paper risk engine and sizing, plus duplicate and late checks. Turns a rejection into REJECTED."""
        def reject(code: str, why: str) -> None:
            row.update(decision="REJECTED", reason_code=code, reason=why)

        close_s = int(f.close_time[i]) / 1e9
        if now.timestamp() - close_s > self.p.max_detection_delay_candles * 900:
            return reject("late_detection", "Detected too long after the candle closed, so it isn't entered.")
        cutoff = int(f.open_time[i]) - self.p.cooldown_bars * 900 * 10**9
        recent = (await session.execute(select(StrategyDDecision.id).where(
            StrategyDDecision.strategy_version == self.p.version, StrategyDDecision.symbol == symbol, StrategyDDecision.direction == row["direction"],
            StrategyDDecision.taken.is_(True), StrategyDDecision.signal_ns >= cutoff))).first()
        if recent is not None:
            return reject("duplicate", f"A {row['direction']} on {symbol} was taken within the last {self.p.cooldown_bars} candles.")
        state, _ = await self._account(session)
        risk = state.equity() * self.p.risk.risk_per_trade_pct / 100
        ok, code, why = check_entry(state, symbol, PRIMARY_TIMEFRAME, row["direction"], risk, self.p.risk, now, data_ok, True, corr)
        if not ok:
            return reject(code, f"Portfolio limit: {why}")
        plan = row["plan"]
        sized = plan_position(PositionInput(symbol=symbol, direction=row["direction"], equity=state.equity(), risk_pct=self.p.risk.risk_per_trade_pct,
                                            entry=plan["entry_reference"], stop=plan["stop_loss"], leverage=LEVERAGE), rules)
        if not sized["feasible"]:
            return reject("sizing", "; ".join(x["message"] for x in sized["issues"]) or "The position rounds down to nothing.")
        row.update(risk_amount=sized["planned_loss"], quantity=sized["quantity"], notional=sized["notional"])
        return None

    async def _notify(self, symbol: str, row: dict[str, Any], tid: str, now: datetime) -> None:
        if self.center is None:
            return
        qualified = row["decision"] == "QUALIFIED"
        allowed = self.settings.current.notifications.strategy_d_telegram
        await self.center.emit({
            "event_id": f"strategy-d:{tid}", "event_type": "STRATEGY_D_QUALIFIED" if qualified else "STRATEGY_D_WATCHLIST",
            "title": f"{symbol} — {row['direction']} Candidate (Strategy D research)", "message": message_for(symbol, row, row["decision"], self.p.version),
            "symbol": symbol, "timeframe": PRIMARY_TIMEFRAME, "strategy_id": STRATEGY_ID, "strategy_version": self.p.version, "occurred_at": now,
            "telegram": True if allowed and qualified else False,
            "payload": {"direction": row["direction"], "decision": row["decision"], "score": row["confluence"]["score"], "research": True}})

    async def _reconcile(self, symbol: str, f: Any) -> None:
        s = series_of(f, PRIMARY_TIMEFRAME)
        async with self._session_factory()() as session:
            rows = (await session.execute(select(StrategyDDecision).where(
                StrategyDDecision.symbol == symbol, StrategyDDecision.status.in_(("PENDING", "OPEN"))))).scalars().all()
            for t in rows:
                view = SimpleNamespace(direction=t.direction, signal_ns=t.signal_ns, stop_loss=t.stop_loss, legs=t.legs, limit_price=None, through=None,
                                       max_bars=t.max_bars, atr_pct=t.atr_pct, status=t.status)
                for k, v in (resolve(view, s) or {}).items():
                    if k != "note":
                        setattr(t, k, v)
            await session.commit()

    async def report(self) -> dict[str, Any]:
        async with self._session_factory()() as session:
            state, _ = await self._account(session)
            rows = (await session.execute(select(StrategyDDecision).where(StrategyDDecision.strategy_version == self.p.version))).scalars().all()
        closed = [r for r in rows if r.status == "CLOSED" and r.net_r is not None]
        values = [r.net_r for r in closed]
        wins, losses = [x for x in values if x > 0], [x for x in values if x <= 0]
        count = lambda attr: {k: sum(getattr(r, attr) == k for r in rows) for k in sorted({getattr(r, attr) for r in rows})}  # noqa: E731
        side = {d: {"closed": len(v := [r.net_r for r in closed if r.direction == d]), "avg_net_r": mean(v) if v else None} for d in ("LONG", "SHORT")}
        needed = self.settings.current.validation.gate.min_closed_trades
        return {"strategy_id": STRATEGY_ID, "name": STRATEGY_NAME, "version": self.p.version, "mode": "shadow", "evidence": EVIDENCE, "label": LABEL,
                "primary_timeframe": PRIMARY_TIMEFRAME, "context_timeframes": list(CONTEXT_TIMEFRAMES), "params": {k: v for k, v in self.p.model_dump(mode="json").items() if k != "base"},
                "telegram_enabled": self.settings.current.notifications.strategy_d_telegram,
                "decisions": count("decision"), "reasons": count("reason_code"), "taken": sum(r.taken for r in rows), "open": len(state.open), "closed": len(closed),
                "closed_needed": needed, "enough_data": len(closed) >= needed, "win_rate_pct": 100 * len(wins) / len(values) if values else None,
                "avg_net_r": mean(values) if values else None, "total_net_r": sum(values) if values else None,
                "profit_factor": sum(wins) / -sum(losses) if wins and losses and sum(losses) else None, "by_direction": side,
                "account": exposure(state), "breakers": breakers(state, self.p.risk, utcnow()), "status": self.status,
                "latest": sorted(self.latest.values(), key=lambda x: x["symbol"]),
                "notes": ["Costs match the historical study: fee 0.055% per side, entry slippage 0.02%, stop slippage 0.05%. Funding isn't charged.",
                          "The alignment score measures rule agreement across timeframes. It isn't a probability of success."]}

    async def decisions(self, symbol: str | None, decision: str | None, limit: int) -> list[dict[str, Any]]:
        q = select(StrategyDDecision).where(StrategyDDecision.strategy_version == self.p.version).order_by(StrategyDDecision.signal_ns.desc()).limit(limit)
        if symbol:
            q = q.where(StrategyDDecision.symbol == symbol)
        if decision:
            q = q.where(StrategyDDecision.decision == decision)
        async with self._session_factory()() as session:
            rows = (await session.execute(q)).scalars().all()
        return [{"id": r.id, "symbol": r.symbol, "timeframe": r.timeframe, "direction": r.direction, "entry_type": r.entry_type, "strategy_version": r.strategy_version,
                 "signal_time": datetime.fromtimestamp(r.signal_ns / 1e9, UTC).isoformat(), "decision_at": r.decision_at.isoformat(), "decision": r.decision,
                 "reason_code": r.reason_code, "reason": r.reason, "score": r.score, "status": r.status, "taken": r.taken, "exit_reason": r.exit_reason,
                 "gross_r": r.gross_r, "net_r": r.net_r, "risk_amount": r.risk_amount, "quantity": r.quantity, "data": r.data} for r in rows]

    async def context(self, symbol: str, at: datetime | None = None, direction: str | None = None) -> dict[str, Any]:
        """The higher-timeframe picture for any 15m candle, as known when it closed."""
        data = await self._load(symbol, self.market.now() - GRACE)
        d15 = data.get(PRIMARY_TIMEFRAME)
        if d15 is None or not len(d15.f):
            return {"symbol": symbol, "available": False, "reason": "No 15m candles are loaded for this symbol."}
        decision_ns = int(at.timestamp() * 1e9) if at else int(d15.f.close_time[-1])
        i = htf_index(d15.f, decision_ns)
        if i < 0:
            return {"symbol": symbol, "available": False, "reason": "The candle history doesn't reach this time."}
        decision_ns = int(d15.f.close_time[i])
        states = {tf: state_at_decision(data.get(tf), tf, decision_ns, self.p).to_dict() for tf in CONTEXT_TIMEFRAMES}
        states[PRIMARY_TIMEFRAME] = classify_at(d15, i, self.p).to_dict()
        entry = float(d15.f.close[i])
        levels = await asyncio.to_thread(nearest_levels, {tf: data[tf] for tf in ("1h", "4h") if tf in data}, symbol, entry, decision_ns, self.p)
        out: dict[str, Any] = {"symbol": symbol, "available": True, "decision_time": datetime.fromtimestamp(decision_ns / 1e9, UTC).isoformat(), "price": entry,
                               "states": states, "levels": levels, "version": self.p.version, "label": "Technical context. Not a probability of success."}
        if direction in ("LONG", "SHORT"):
            biases = {tf: states[tf]["bias"] for tf in CONTEXT_TIMEFRAMES}
            out["direction"] = direction
            out["alignment"] = score(direction, biases, True, self.p)
            out["conflicts"] = [f"{tf.upper()} is {b.lower().replace('_', ' ')}" for tf, b in biases.items() if b != ("BULLISH" if direction == "LONG" else "BEARISH")]
        async with self._session_factory()() as session:
            found = (await session.execute(select(StrategyDDecision).where(
                StrategyDDecision.strategy_version == self.p.version, StrategyDDecision.symbol == symbol, StrategyDDecision.signal_ns == int(d15.f.open_time[i])))).scalars().all()
        out["d_decisions"] = [{"direction": r.direction, "decision": r.decision, "reason": r.reason, "score": r.score, "entry_type": r.entry_type} for r in found]
        return out
