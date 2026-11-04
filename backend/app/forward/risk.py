"""Portfolio risk engine of a paper account (pure functions).

Measures: aggregate open risk, directional exposure, concurrent positions, rolling
correlation, equity drawdown, concentration by coin and timeframe. Safeguards (risk
controls, not signal improvements; see ``RiskLimits``):

1. data integrity: no entry without integral data / a fresh quote;
2. max drawdown circuit breaker: equity below peak by ``max_drawdown_pct`` halts new
   positions until a manual reset;
3. daily loss circuit breaker: realized loss today >= ``daily_loss_limit_pct``;
4. one open position per coin (repeated signals are not independent trades);
5. aggregate open risk + this trade's risk <= ``max_open_risk_pct``;
6. correlated exposure: same-direction open positions with correlation >= threshold.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.forward.settings import RiskLimits


@dataclass(slots=True)
class Position:
    symbol: str
    timeframe: str
    direction: str
    risk_amount: float
    notional: float


@dataclass(slots=True)
class AccountState:
    starting_equity: float
    realized: list[tuple[datetime, float]]  # (exit time, net pnl) of taken, closed trades
    open: list[Position]
    drawdown_reset_at: datetime | None = None

    def equity(self) -> float:
        return self.starting_equity + sum(p for _, p in self.realized)

    def peak(self) -> float:
        """Running equity peak; after a manual reset the peak restarts from the equity then."""
        rows = sorted(self.realized)
        r = self.drawdown_reset_at
        eq = self.starting_equity + sum(p for t, p in rows if r is not None and t < r)
        peak = eq
        for t, p in rows:
            if r is not None and t < r:
                continue
            eq += p
            peak = max(peak, eq)
        return peak

    def drawdown_pct(self) -> float:
        pk = self.peak()
        return 100 * (pk - self.equity()) / pk if pk > 0 else 0.0

    def realized_today(self, now: datetime) -> float:
        day = now.date()
        return sum(p for t, p in self.realized if t.date() == day)


def check_entry(state: AccountState, symbol: str, timeframe: str, direction: str, trade_risk: float,
                limits: RiskLimits, now: datetime, data_ok: bool, quote_fresh: bool,
                corr: Callable[[str, str], float] | None) -> tuple[bool, str, str]:
    """Returns (allowed, code, message). Codes: ok, data_integrity, max_drawdown, daily_loss,
    symbol_open, open_risk, correlated."""
    eq = state.equity()
    if not data_ok:
        return False, "data_integrity", "data-integrity breaker: candle data gaps or stale market data"
    if limits.require_fresh_quote and not quote_fresh:
        return False, "data_integrity", "data-integrity breaker: no fresh bid/ask quote at detection"
    dd = state.drawdown_pct()
    if dd >= limits.max_drawdown_pct:
        return False, "max_drawdown", f"max-drawdown breaker: equity {dd:.1f}% below peak (limit {limits.max_drawdown_pct:g}%)"
    today = state.realized_today(now)
    if eq > 0 and -today / eq * 100 >= limits.daily_loss_limit_pct:
        return False, "daily_loss", f"daily-loss breaker: {today:,.2f} USDT today (limit {limits.daily_loss_limit_pct:g}%)"
    if any(p.symbol == symbol for p in state.open):
        return False, "symbol_open", f"{symbol} already has an open paper position"
    open_risk = sum(p.risk_amount for p in state.open)
    if eq > 0 and (open_risk + trade_risk) / eq * 100 > limits.max_open_risk_pct + 1e-9:
        return False, "open_risk", (f"open risk would be {(open_risk + trade_risk) / eq * 100:.2f}% of equity "
                       f"(limit {limits.max_open_risk_pct:g}%)")
    if limits.max_correlated_positions and corr is not None:
        hits = []
        for p in state.open:
            if p.direction != direction:
                continue
            c = corr(symbol, p.symbol)
            if math.isfinite(c) and c >= limits.corr_threshold:
                hits.append(f"{p.symbol} ({c:.2f})")
        if len(hits) >= limits.max_correlated_positions:
            return False, "correlated", f"correlated exposure: already {direction} in {', '.join(hits)}"
    return True, "ok", "within risk limits"


def exposure(state: AccountState, corr: Callable[[str, str], float] | None = None) -> dict[str, Any]:
    eq = state.equity()
    long_n = sum(p.notional for p in state.open if p.direction == "LONG")
    short_n = sum(p.notional for p in state.open if p.direction == "SHORT")
    pairs = []
    if corr is not None:
        for a in range(len(state.open)):
            for b in range(a + 1, len(state.open)):
                c = corr(state.open[a].symbol, state.open[b].symbol)
                if math.isfinite(c):
                    pairs.append({"a": state.open[a].symbol, "b": state.open[b].symbol, "corr": round(c, 3)})
    return {
        "equity": round(eq, 2),
        "drawdown_pct": round(state.drawdown_pct(), 3),
        "open_positions": len(state.open),
        "open_risk": round(sum(p.risk_amount for p in state.open), 2),
        "open_risk_pct": round(100 * sum(p.risk_amount for p in state.open) / eq, 3) if eq > 0 else None,
        "long_notional": round(long_n, 2), "short_notional": round(short_n, 2),
        "net_directional_notional": round(long_n - short_n, 2),
        "by_coin": dict(Counter(p.symbol for p in state.open)),
        "by_timeframe": dict(Counter(p.timeframe for p in state.open)),
        "correlations": pairs,
    }


def breakers(state: AccountState, limits: RiskLimits, now: datetime) -> dict[str, Any]:
    eq = state.equity()
    today = state.realized_today(now)
    dd = state.drawdown_pct()
    return {
        "max_drawdown": {"tripped": dd >= limits.max_drawdown_pct, "value_pct": round(dd, 3),
                         "limit_pct": limits.max_drawdown_pct, "reset_required": dd >= limits.max_drawdown_pct},
        "daily_loss": {"tripped": eq > 0 and -today / eq * 100 >= limits.daily_loss_limit_pct,
                       "value": round(today, 2), "limit_pct": limits.daily_loss_limit_pct},
    }
