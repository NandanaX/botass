"""Take-profit candidates and how realistic they are against nearby structure.

TP1, TP2 and TP3 are configurable R multiples of the stop distance. They are candidates, not
recommendations, and a target is never moved further away to make the profit look bigger. Each is
reported in price, percent, ATR, gross R and estimated net R, and is compared with the nearest
opposing zone. Structural levels are listed the same way, but they are reference levels, not forecasts.

Net R uses the same convention as the historical simulator: net P&L per unit (adverse entry slippage,
entry and exit fees, target filled at its price) divided by the planned risk ``|entry - stop|``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.risk.sizing import round_price

DEFAULT_R = (1.0, 2.0, 3.0)
NEAR_OPPOSING_R = 0.25  # a target this close in front of the opposing zone may not fill either


@dataclass(frozen=True, slots=True)
class Costs:
    entry_fee_pct: float = 0.055
    exit_fee_pct: float = 0.055
    entry_slippage_pct: float = 0.02
    stop_slippage_pct: float = 0.05

    def entry_fill(self, direction: str, entry: float) -> float:
        s = self.entry_slippage_pct / 100
        return entry * (1 + s) if direction == "LONG" else entry * (1 - s)

    def net_r(self, direction: str, entry: float, stop: float, price: float) -> float:
        """Net result of an exit at ``price`` (limit fill) in R of the planned risk."""
        fill = self.entry_fill(direction, entry)
        move = price - fill if direction == "LONG" else fill - price
        fees = self.entry_fee_pct / 100 * fill + self.exit_fee_pct / 100 * price
        return (move - fees) / abs(entry - stop)


def distance_metrics(direction: str, entry: float, stop: float, atr: float, price: float, costs: Costs) -> dict[str, float]:
    risk = abs(entry - stop)
    move = abs(price - entry)
    return {"distance": move, "distance_pct": 100 * move / entry, "distance_atr": move / atr,
            "gross_r": move / risk, "net_r": costs.net_r(direction, entry, stop, price)}


def _feasibility(direction: str, price: float, risk: float, boundary: float | None) -> tuple[str, str | None]:
    if boundary is None:
        return "no_opposing_zone", "No opposing zone was found in range, so nothing here checks this target."
    long = direction == "LONG"
    side = "resistance" if long else "support"
    if (long and price >= boundary) or (not long and price <= boundary):
        return "blocked", f"Beyond the nearest {side} at {boundary:.6g}; price may react before reaching it."
    if abs(boundary - price) <= NEAR_OPPOSING_R * risk:
        return "near_opposing", f"Just in front of the nearest {side} at {boundary:.6g}."
    return "clear", f"In front of the nearest {side} at {boundary:.6g}."


def build_targets(direction: str, entry: float, stop: float, atr: float, r_multiples: tuple[float, ...] | list[float] = DEFAULT_R,
                  opposing_boundary: float | None = None, costs: Costs = Costs(), tick_size: float | None = None
                  ) -> list[dict[str, Any]]:
    long = direction == "LONG"
    risk = abs(entry - stop)
    if risk <= 0:
        raise ValueError("stop distance must be positive")
    out = []
    for k, m in enumerate(r_multiples, start=1):
        if m <= 0:
            raise ValueError("target multiples must be positive")
        raw = entry + m * risk if long else entry - m * risk
        price = round_price(raw, tick_size, "down" if long else "up")  # toward the entry, never further away
        if price <= 0:
            out.append({"label": f"TP{k}", "r_multiple": m, "price": None, "valid": False, "feasibility": "invalid",
                        "note": "The stop is too wide for this multiple: the target would not be a positive price."})
            continue
        feas, note = _feasibility(direction, price, risk, opposing_boundary)
        out.append({"label": f"TP{k}", "r_multiple": m, "price": price, "valid": True, "feasibility": feas, "note": note,
                    **distance_metrics(direction, entry, stop, atr, price, costs)})
    return out


def structural_targets(direction: str, entry: float, stop: float, atr: float, levels: list[tuple[str, str, float | None]],
                       costs: Costs = Costs()) -> list[dict[str, Any]]:
    """``levels``: ``(kind, label, price)``. Levels not on the profit side of the entry are dropped."""
    long = direction == "LONG"
    out = []
    for kind, label, price in levels:
        if price is None or price <= 0 or (long and price <= entry) or (not long and price >= entry):
            continue
        out.append({"kind": kind, "label": label, "price": price, **distance_metrics(direction, entry, stop, atr, price, costs)})
    out.sort(key=lambda x: x["distance"])
    return out
