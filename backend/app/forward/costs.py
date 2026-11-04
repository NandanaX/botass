"""Execution and cost model of paper trades (pure functions).

* **Entries** are market orders at detection time. A LONG buys at the **ask**, a SHORT sells
  at the **bid**, plus ``slippage_pct``. Without a fresh quote (older than
  ``max_quote_age_s``) the last trade price -/+ half of ``assumed_spread_pct`` is used and
  the fill is flagged ``estimated``.
* **Exits** trigger on the side a closing order would trade: LONG on the bid, SHORT on the
  ask. Stops and time exits are market orders and fill ``stop_slippage_pct`` worse than the
  triggering price (a stop is not guaranteed at its level; a gap through it fills at the
  gapped price). Take-profits are resting limit orders that fill at their price, maker fee.
* Spread and slippage are already inside the fill prices. They are reported separately for
  transparency (``spread_cost``, ``slippage_cost``) and never subtracted twice.
* ``net_r`` = net P&L / planned risk, where planned risk = quantity x |fill - stop|.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.forward.settings import PaperCosts


@dataclass(slots=True)
class Quote:
    bid: float | None
    ask: float | None
    last: float | None
    age_s: float | None  # age of bid/ask

    @classmethod
    def from_ticker(cls, t: dict[str, Any], now: datetime) -> Quote:
        qa = t.get("quote_at")
        age = None
        if qa:
            age = (now - datetime.fromisoformat(qa)).total_seconds()
        return cls(t.get("bid_price"), t.get("ask_price"), t.get("last_price"), age)

    def fresh(self, pc: PaperCosts) -> bool:
        return (self.bid is not None and self.ask is not None and self.age_s is not None
                and 0 <= self.age_s <= pc.max_quote_age_s and self.ask >= self.bid > 0)


@dataclass(slots=True)
class Fill:
    price: float
    spread_per_unit: float
    slippage_per_unit: float
    source: str  # quote | last_price_estimate


def _side_price(direction: str, opening: bool, q: Quote, pc: PaperCosts) -> tuple[float, float, str] | None:
    """(price, half-spread paid, source). Buying hits the ask, selling the bid."""
    buying = (direction == "LONG") == opening
    if q.fresh(pc):
        mid = (q.bid + q.ask) / 2  # type: ignore[operator]
        px = q.ask if buying else q.bid
        return float(px), abs(float(px) - mid), "quote"  # type: ignore[arg-type]
    if q.last is None or q.last <= 0:
        return None
    half = q.last * pc.assumed_spread_pct / 200
    return (q.last + half if buying else q.last - half), half, "last_price_estimate"


def entry_fill(direction: str, q: Quote, pc: PaperCosts) -> Fill | None:
    sp = _side_price(direction, True, q, pc)
    if sp is None:
        return None
    px, half, src = sp
    slip = px * pc.slippage_pct / 100
    return Fill(px + slip if direction == "LONG" else px - slip, half, slip, src)


def trigger_price(direction: str, q: Quote, pc: PaperCosts) -> tuple[float, str] | None:
    """Price a closing order would trade at: bid for a LONG, ask for a SHORT."""
    sp = _side_price(direction, False, q, pc)
    return None if sp is None else (sp[0], sp[2])


def check_exit(direction: str, price: float, stop: float, target: float) -> str | None:
    long = direction == "LONG"
    if (long and price <= stop) or (not long and price >= stop):
        return "STOP"
    if (long and price >= target) or (not long and price <= target):
        return "TARGET"
    return None


def exit_fill(direction: str, reason: str, trigger: float, target: float, pc: PaperCosts) -> Fill:
    """Fill for an exit triggered at ``trigger`` (already on the closing side)."""
    if reason == "TARGET":
        return Fill(target, 0.0, 0.0, "limit")
    slip = trigger * pc.stop_slippage_pct / 100
    return Fill(trigger - slip if direction == "LONG" else trigger + slip, 0.0, slip, "market")


def settle(direction: str, qty: float, fill: float, stop: float, exit_price: float, reason: str, funding: float,
           pc: PaperCosts) -> dict[str, float]:
    sign = 1 if direction == "LONG" else -1
    gross = sign * (exit_price - fill) * qty
    entry_fee = pc.taker_fee_pct / 100 * fill * qty
    exit_fee = (pc.maker_fee_pct if reason == "TARGET" else pc.taker_fee_pct) / 100 * exit_price * qty
    net = gross - entry_fee - exit_fee - funding
    risk = qty * abs(fill - stop)
    return {"gross_pnl": gross, "entry_fee": entry_fee, "exit_fee": exit_fee, "net_pnl": net,
            "net_r": net / risk if risk > 0 else 0.0, "risk_amount": risk}
