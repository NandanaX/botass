"""Forward paper tests of the two Phase 9 candidates. They have no effect on signals, alerts or A, B and C.

Each test reads the same QUALIFIED signals as the live strategies, from now on only, and keeps its own
table: no notifications, no paper account and no route to promotion. Every result stays Unvalidated.

* **Breakout retest:** a limit at the broken zone's near edge. Paper trading can't show that a real
  limit order would have filled, so it runs twice: filling on a touch, and only once price trades
  0.1 ATR past the limit. The gap between the two is the uncertainty the study found.
* **2.5 ATR + HTF with 30/40/30 exits:** locked strategy C's entry rule and stop with partial exits,
  next to a control that uses C's single 2R exit in this same simulation, so only the exit differs.

Outcomes come from ``p9_exits``, the study's simulation and cost model, on closed candles, so they can
be compared with the replay. It re-reads the candles each time, so downtime only delays a result.
Funding isn't charged because candles don't carry it. Signals missed while the app was off aren't
backfilled.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from statistics import mean
from typing import Any

import numpy as np
from sqlalchemy import select

from app.db.models import ForwardExperimentTrade
from app.forward.variants import plan_for
from app.research.p9_exits import RETEST_WAIT_BARS, find_retest_fill, retest_plan, simulate_legs
from app.research.robustness import Scenario, Series
from app.strategy.engine import Evaluation
from app.strategy.features import Features
from app.timeutil import tf_delta

log = logging.getLogger(__name__)

COSTS = Scenario("forward", funding=False)
THROUGH_ATR = 0.1
EVIDENCE = "Unvalidated"
LABEL = "PAPER TEST: new data only, no alerts, no real orders. Not part of strategies A, B and C."
FINISHED = ("CLOSED", "NOT_FILLED", "UNFILLED", "EXPIRED")

TRACKS: dict[str, dict[str, Any]] = {
    "retest_touch": {"test": "retest", "label": "Retest: fills on a touch", "control": None},
    "retest_through": {"test": "retest", "label": "Retest: price must trade 0.1 ATR past the limit", "control": None},
    "htf_2r_control": {"test": "exits", "label": "2.5 ATR + HTF, single 2R exit (control)", "control": None},
    "htf_30_40_30": {"test": "exits", "label": "2.5 ATR + HTF, 30% at 1R, 40% at 2R, 30% at 3R", "control": "htf_2r_control"},
}


def series_of(f: Any, tf: str) -> Series:
    empty = np.array([])
    return Series(f.open, f.high, f.low, f.close, f.open_time, int(tf_delta(tf).total_seconds() * 1e9), empty, empty)


def plans_for(st: Any) -> list[dict[str, Any]]:
    """The tracks one QUALIFIED setup enters, using decision-time information only."""
    out: list[dict[str, Any]] = []
    long = st.direction == "LONG"
    if st.strategy == "breakout":
        zone = (st.plan or {}).get("zone") or {}
        plan = retest_plan(st.direction, zone.get("upper") if long else zone.get("lower"), st.stop_loss, float(st.atr))
        if plan is not None:
            limit, stop, target = plan
            legs = [[target, 1.0]]
            out.append({"track": "retest_touch", "stop_loss": stop, "legs": legs, "limit_price": limit, "through": 0.0})
            out.append({"track": "retest_through", "stop_loss": stop, "legs": legs, "limit_price": limit, "through": THROUGH_ATR * float(st.atr)})
    c = plan_for("C", st)  # read only: the locked rule itself, so these can't drift away from it
    if c.accepted and c.stop is not None:
        entry, sign = float(st.entry_reference), 1.0 if long else -1.0
        risk = abs(entry - c.stop)
        if entry - sign * risk > 0 and entry + sign * 3 * risk > 0:
            out.append({"track": "htf_2r_control", "stop_loss": c.stop, "legs": [[entry + sign * 2 * risk, 1.0]], "limit_price": None, "through": None})
            out.append({"track": "htf_30_40_30", "stop_loss": c.stop, "limit_price": None, "through": None,
                        "legs": [[entry + sign * risk, 0.3], [entry + sign * 2 * risk, 0.4], [entry + sign * 3 * risk, 0.3]]})
    return out


def resolve(t: Any, s: Series) -> dict[str, Any] | None:
    """New field values for a record from the closed candles in ``s``, or None if nothing has changed."""
    long = t.direction == "LONG"
    if not len(s.t) or t.signal_ns < s.t[0]:
        return {"status": "EXPIRED", "note": "the candle history no longer reaches this signal"}
    i = int(np.searchsorted(s.t, t.signal_ns))
    if i >= len(s.t) or s.t[i] != t.signal_ns:
        return None
    legs = [(float(p), float(w)) for p, w in t.legs]
    limit_entry = None
    if t.limit_price is not None:
        limit_entry = find_retest_fill(s, i, t.direction, t.limit_price, t.stop_loss, legs[-1][0], RETEST_WAIT_BARS, t.through or 0.0)
        if limit_entry is None:
            return ({"status": "NOT_FILLED", "note": f"no fill within {RETEST_WAIT_BARS} candles, or the stop or target traded first"}
                    if len(s.t) >= i + 1 + RETEST_WAIT_BARS else None)
        e = limit_entry[0]
    else:
        e = i + 1
        if e >= len(s.t):
            return None
        raw = float(s.o[e])
        if (long and not t.stop_loss < raw < legs[0][0]) or (not long and not legs[0][0] < raw < t.stop_loss):
            return {"status": "UNFILLED", "note": "the next open was already beyond the stop or the first target"}
    r = simulate_legs(s, i, t.direction, t.stop_loss, legs, t.max_bars, COSTS, t.atr_pct, limit_entry=limit_entry)
    fill_time = datetime.fromtimestamp(int(s.t[e]) / 1e9, UTC)
    if r is None:  # still running. Only the entry is known so far
        price = float(limit_entry[1]) if limit_entry else float(s.o[e]) * (1 + (1 if long else -1) * COSTS.entry_slip_pct / 100)
        return None if t.status == "OPEN" else {"status": "OPEN", "fill_price": price, "fill_time": fill_time}
    return {"status": "CLOSED", "fill_price": r["entry_fill"], "fill_time": fill_time, "exit_reason": r["reason"],
            "exit_time": datetime.fromtimestamp(r["exit_close_ns"] / 1e9, UTC), "targets_hit": r["targets_hit"],
            "gross_r": r["gross_r"], "net_r": r["net_r"]}


class ForwardExperiments:
    def __init__(self, settings_store, session_factory) -> None:
        self.settings = settings_store
        self._session_factory = session_factory
        # Counted so that an empty table can be told apart from a tracker that isn't running.
        self.status: dict[str, Any] = {"candles_checked": 0, "signals_seen": 0, "last_candle_at": None, "last_error": None}

    async def on_evaluation(self, symbol: str, tf: str, f: Features, ev: Evaluation, data_ok: bool, now: datetime) -> None:
        """Called by the scanner for each newly closed candle. Failures never reach the scanner."""
        try:
            self.status["candles_checked"] += 1
            self.status["last_candle_at"] = now.isoformat()
            if data_ok and ev.setup is not None and ev.setup.status == "QUALIFIED":
                self.status["signals_seen"] += 1
                await self._record(symbol, tf, f, ev.setup, now)
            await self._reconcile(symbol, tf, f)
            self.status["last_error"] = None
        except Exception as exc:  # noqa: BLE001 - a research tracker must never interrupt scanning
            self.status["last_error"] = str(exc)
            log.exception("forward experiments failed", extra={"symbol": symbol, "timeframe": tf})

    async def _record(self, symbol: str, tf: str, f: Any, st: Any, now: datetime) -> None:
        i = len(f.open) - 1
        signal_ns = int(f.open_time[i])
        key = hashlib.sha256(f"{symbol}|{tf}|{st.direction}|{st.strategy}|{signal_ns}|{st.strategy_version}".encode()).hexdigest()[:40]
        late = (now.timestamp() - int(f.close_time[i]) / 1e9) > self.settings.current.validation.paper.max_signal_delay_candles * tf_delta(tf).total_seconds()
        atr_pct = 100 * float(st.atr) / float(st.entry_reference)
        max_bars = self.settings.current.evaluation.max_bars
        async with self._session_factory()() as session:
            for p in plans_for(st):
                tid = hashlib.sha256(f"{p['track']}|{key}".encode()).hexdigest()[:40]
                if await session.get(ForwardExperimentTrade, tid) is not None:
                    continue  # a re-evaluated candle must not create or reopen a record
                session.add(ForwardExperimentTrade(
                    id=tid, signal_key=key, symbol=symbol, timeframe=tf, direction=st.direction, strategy=st.strategy,
                    strategy_version=st.strategy_version, detected_at=now, signal_ns=signal_ns, atr_pct=atr_pct, max_bars=max_bars,
                    status="UNFILLED" if late else "PENDING", note="detected too long after the candle closed" if late else None, **p))
            await session.commit()

    async def _reconcile(self, symbol: str, tf: str, f: Any) -> None:
        s = series_of(f, tf)
        async with self._session_factory()() as session:
            rows = (await session.execute(select(ForwardExperimentTrade).where(
                ForwardExperimentTrade.symbol == symbol, ForwardExperimentTrade.timeframe == tf,
                ForwardExperimentTrade.status.in_(("PENDING", "OPEN"))))).scalars().all()
            for t in rows:
                for k, v in (resolve(t, s) or {}).items():
                    setattr(t, k, v)
            await session.commit()

    async def report(self) -> dict[str, Any]:
        version = self.settings.current.strategy.version
        async with self._session_factory()() as session:
            every = (await session.execute(select(ForwardExperimentTrade).order_by(ForwardExperimentTrade.detected_at))).scalars().all()
        rows = [r for r in every if r.strategy_version == version]  # one result set, one strategy configuration
        needed = self.settings.current.validation.gate.min_closed_trades
        closed = {k: {r.signal_key: r.net_r for r in rows if r.track == k and r.status == "CLOSED" and r.net_r is not None} for k in TRACKS}
        tracks = []
        for key, spec in TRACKS.items():
            mine = [r for r in rows if r.track == key]
            v = list(closed[key].values())
            count = lambda *st, m=mine: sum(r.status in st for r in m)  # noqa: E731
            filled = count("OPEN", "CLOSED")
            paired = None
            if spec["control"]:
                both = closed[key].keys() & closed[spec["control"]].keys()
                if both:
                    paired = {"n": len(both), "avg_difference_r": mean(closed[key][b] - closed[spec["control"]][b] for b in both)}
            tracks.append({"key": key, "test": spec["test"], "label": spec["label"], "evidence": EVIDENCE, "signals": len(mine),
                           "waiting": count("PENDING"), "open": count("OPEN"), "closed": len(v), "not_filled": count("NOT_FILLED"),
                           "unfilled": count("UNFILLED"), "expired": count("EXPIRED"),
                           "fill_rate_pct": 100 * filled / (filled + count("NOT_FILLED")) if spec["test"] == "retest" and filled + count("NOT_FILLED") else None,
                           "win_rate_pct": 100 * sum(x > 0 for x in v) / len(v) if v else None,
                           "avg_net_r": mean(v) if v else None, "total_net_r": sum(v) if v else None,
                           "closed_needed": needed, "enough_data": len(v) >= needed, "vs_control": paired})
        return {"label": LABEL, "evidence": EVIDENCE, "strategy_version": version, "other_version_records": len(every) - len(rows),
                "started_at": rows[0].detected_at.isoformat() if rows else None, "tracks": tracks, "status": self.status,
                "notes": ["Costs match the historical study: fee 0.055% per side, entry slippage 0.02%, stop slippage 0.05%. Funding isn't charged.",
                          "Paper trading can't prove a real limit order would fill. The two retest rows bracket that uncertainty."]}
