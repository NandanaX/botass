"""Stop-loss placement: ATR, market-structure and hybrid methods.

* ``atr``: ``entry -/+ k x ATR``.
* ``structure``: just beyond the structural level that invalidates the idea (one tick
  below the support boundary for a LONG, one tick above the resistance boundary for a
  SHORT).
* ``hybrid``: the structural level plus an ATR buffer (``level -/+ b x ATR``).

A structural stop is never moved *closer* to make the trade look better. If it is closer
than ``min_stop_atr x ATR`` it is *widened* to that distance (noise protection). If it is
further than ``max_stop_atr x ATR`` the setup is rejected by the qualification step.
Stops are rounded to the tick size away from the entry, so rounding never tightens them.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.risk.sizing import round_price

Direction = Literal["LONG", "SHORT"]


@dataclass(slots=True)
class StopResult:
    method: str
    price: float
    distance: float
    distance_atr: float
    structural_level: float | None
    widened: bool
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_stop(
    direction: Direction,
    entry: float,
    atr: float,
    method: Literal["atr", "structure", "hybrid"],
    structural_level: float | None,
    atr_multiplier: float = 1.5,
    hybrid_buffer_atr: float = 0.25,
    min_stop_atr: float = 0.5,
    tick_size: float | None = None,
    level_label: str = "structural level",
) -> StopResult:
    if entry <= 0 or not atr > 0:
        raise ValueError("entry and ATR must be positive")
    long = direction == "LONG"
    sign = -1 if long else 1  # stop side relative to entry
    tick = tick_size or 0.0
    used = method
    note = ""
    if method != "atr" and (structural_level is None or not math.isfinite(structural_level)
                            or (long and structural_level >= entry) or (not long and structural_level <= entry)):
        used = "atr"
        note = f" No valid {level_label} on the stop side of the entry, so the ATR method was used instead."
    if used == "atr":
        raw = entry + sign * atr_multiplier * atr
        reasoning = f"ATR stop: entry {'-' if long else '+'} {atr_multiplier:g} x ATR ({atr:.6g})."
    elif used == "structure":
        assert structural_level is not None
        raw = structural_level + sign * tick
        reasoning = (f"Structure stop: just {'below' if long else 'above'} the {level_label} "
                     f"{structural_level:.6g}; a close beyond it invalidates the setup.")
    else:
        assert structural_level is not None
        raw = structural_level + sign * hybrid_buffer_atr * atr
        reasoning = (f"Hybrid stop: {level_label} {structural_level:.6g} {'-' if long else '+'} "
                     f"{hybrid_buffer_atr:g} x ATR buffer ({hybrid_buffer_atr * atr:.6g}).")
    widened = False
    if abs(entry - raw) < min_stop_atr * atr:
        raw = entry + sign * min_stop_atr * atr
        widened = True
        reasoning += f" Widened to the minimum {min_stop_atr:g} x ATR distance to stay outside normal noise."
    price = round_price(raw, tick_size, "down" if long else "up")
    if price <= 0:
        raise ValueError("computed stop is not a positive price")
    distance = abs(entry - price)
    return StopResult(used, price, distance, distance / atr, structural_level if used != "atr" else None, widened,
                      reasoning + note)
