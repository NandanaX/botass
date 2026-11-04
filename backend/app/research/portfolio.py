"""Cross-asset portfolio risk filter for hypothetical signals.

Signals are processed in decision-time order. A signal is accepted only if, *at its
decision time*, every enabled limit holds. Only information known then is used:

* open positions = accepted signals whose exit candle has not closed yet;
* realized results = accepted signals already closed (daily loss, losing streak);
* correlations = rolling correlation of 1h log returns over the previous
  ``corr_window_hours`` closed hourly candles (they change over time; there is no fixed
  list of "correlated coins").

Limits (``0`` disables a limit):

* ``one_per_symbol``: a symbol with an open position cannot open another one (repeated
  signals from the same move are not independent trades).
* ``max_positions`` and ``max_total_risk_r`` (every position risks 1R).
* ``max_same_direction``: open positions in the same direction.
* ``max_correlated``: open same-direction positions whose correlation with the candidate
  is ``>= corr_threshold``.
* ``max_daily_loss_r``: no new positions for the rest of the UTC day once realized net R
  that day is ``<= -max_daily_loss_r``.
* ``max_consecutive_losses`` with ``cooldown_hours``: after that many consecutive losing
  trades, pause new positions for the cooldown.

When several signals arrive at the same decision time they are ranked first (``rank_key``)
and accepted greedily, so the better-ranked one takes the risk budget.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

ENTRY_RANK = {"EARLY": 0, "CONFIRMED": 1, "EXTENDED": 2, "EXHAUSTION_RISK": 3, "INVALID": 4}


class PortfolioParams(BaseModel):
    one_per_symbol: bool = True
    max_positions: int = Field(3, ge=0, le=100)
    max_total_risk_r: float = Field(3.0, ge=0, le=100)
    max_same_direction: int = Field(2, ge=0, le=100)
    max_correlated: int = Field(1, ge=0, le=100)
    corr_threshold: float = Field(0.7, ge=0, le=1)
    corr_window_hours: int = Field(168, ge=24, le=5000)
    max_daily_loss_r: float = Field(3.0, ge=0, le=100)
    max_consecutive_losses: int = Field(4, ge=0, le=100)
    cooldown_hours: int = Field(24, ge=0, le=1000)


@dataclass
class CorrelationBook:
    """Rolling pairwise correlations of hourly log returns, looked up causally."""

    times: np.ndarray  # close time (ns) of the last hourly candle in each window
    symbols: list[str]
    mats: np.ndarray  # (T, S, S)

    @classmethod
    def build(cls, closes_1h: dict[str, pd.DataFrame], window: int) -> CorrelationBook:
        frames = []
        for s, df in closes_1h.items():
            frames.append(pd.Series(df["close"].to_numpy(float), index=df["open_time"], name=s))
        px = pd.concat(frames, axis=1).sort_index()
        rets = np.log(px).diff()
        symbols = list(px.columns)
        arr = rets.to_numpy(float)
        T, S = arr.shape
        mats = np.full((T, S, S), np.nan)
        # Rolling correlation via cumulative sums (pairwise complete windows only).
        for t in range(window, T):
            w = arr[t - window + 1:t + 1]
            ok = ~np.isnan(w).any(axis=1)
            if ok.sum() >= window * 0.8:
                mats[t] = np.corrcoef(w[ok].T)
        close_ns = px.index.astype("int64").to_numpy() + 3_600_000_000_000
        return cls(close_ns, symbols, mats)

    def corr(self, a: str, b: str, at_ns: int) -> float:
        if a == b:
            return 1.0
        if a not in self.symbols or b not in self.symbols:
            return float("nan")
        t = int(np.searchsorted(self.times, at_ns, side="right")) - 1
        if t < 0:
            return float("nan")
        return float(self.mats[t, self.symbols.index(a), self.symbols.index(b)])


def rank_key(t: dict[str, Any]) -> tuple:
    room = t.get("room_r")
    return (ENTRY_RANK.get(t.get("entry_class", "CONFIRMED"), 1), -(room if room is not None else 10.0),
            -(t.get("score") or 0))


def run_portfolio(trades: list[dict[str, Any]], pp: PortfolioParams, corr: CorrelationBook | None
                  ) -> tuple[list[dict[str, Any]], Counter[str]]:
    """``trades``: filled hypothetical trades with ``symbol``, ``direction``, ``decision_ns``,
    ``exit_close_ns`` and ``net_r``. Returns (accepted trades, rejection reason counts)."""
    by_time: dict[int, list[dict[str, Any]]] = {}
    for t in trades:
        by_time.setdefault(t["decision_ns"], []).append(t)
    accepted: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    open_pos: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []
    cooldown_until = -1
    for now in sorted(by_time):
        still = []
        for p in open_pos:
            (closed if p["exit_close_ns"] <= now else still).append(p)
        open_pos = still
        closed.sort(key=lambda x: x["exit_close_ns"])
        day = pd.Timestamp(now, tz="UTC").floor("D").value
        day_r = sum(c["net_r"] for c in closed if c["exit_close_ns"] >= day)
        streak = 0
        for c in reversed(closed):
            if c["net_r"] > 0:
                break
            streak += 1
        if pp.max_consecutive_losses and streak >= pp.max_consecutive_losses and cooldown_until < now:
            last_exit = closed[-1]["exit_close_ns"]
            if now - last_exit < pp.cooldown_hours * 3_600_000_000_000:
                cooldown_until = last_exit + pp.cooldown_hours * 3_600_000_000_000
        for t in sorted(by_time[now], key=rank_key):
            reason = None
            if pp.max_daily_loss_r and day_r <= -pp.max_daily_loss_r:
                reason = "daily_loss_limit"
            elif pp.max_consecutive_losses and now < cooldown_until:
                reason = "losing_streak_cooldown"
            elif pp.one_per_symbol and any(p["symbol"] == t["symbol"] for p in open_pos):
                reason = "symbol_already_open"
            elif pp.max_positions and len(open_pos) >= pp.max_positions:
                reason = "max_positions"
            elif pp.max_total_risk_r and len(open_pos) + 1 > pp.max_total_risk_r:
                reason = "max_total_risk"
            else:
                same = [p for p in open_pos if p["direction"] == t["direction"]]
                if pp.max_same_direction and len(same) >= pp.max_same_direction:
                    reason = "max_same_direction"
                elif pp.max_correlated and corr is not None:
                    n_corr = sum(1 for p in same if corr.corr(p["symbol"], t["symbol"], now) >= pp.corr_threshold)
                    if n_corr >= pp.max_correlated:
                        reason = "correlated_exposure"
            if reason:
                reasons[reason] += 1
                continue
            accepted.append(t)
            open_pos.append(t)
    return accepted, reasons


def independent_baseline(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One position at a time per symbol/timeframe (the backtester's rule)."""
    last_exit: dict[tuple[str, str], int] = {}
    out = []
    for t in sorted(trades, key=lambda x: x["decision_ns"]):
        k = (t["symbol"], t["timeframe"])
        if last_exit.get(k, -1) > t["decision_ns"]:
            continue
        out.append(t)
        last_exit[k] = t["exit_close_ns"]
    return out
