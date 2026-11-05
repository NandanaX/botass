"""The six stop-loss research variants. Each is a pure function of decision-time information.

LONG stops sit below the invalidation level and SHORT stops above it. Rounding to the tick size
always moves a stop away from the entry, so rounding never tightens it. A method that can't place a
sensible stop reports that instead of quietly using a different method.

* ``A_original``: the production stop, as the signal stored it.
* ``B_atr_2_5``, ``C_atr_3_0``, ``D_atr_3_5``: a fixed multiple of ATR, ignoring structure.
* ``E_structural``: beyond the last confirmed swing (else the setup's zone) plus a 0.5 ATR buffer.
* ``F_hybrid``: the widest of the structural stop (0.25 ATR buffer), the 80th percentile of recent
  3-bar adverse moves, and 1.5 ATR.

E and F are refused beyond ``MAX_STOP_ATR``, the production limit. A structural stop that far away is
not tightened to fit, because that would place it inside the structure it is meant to protect.
The multipliers and buffers were fixed before any result was seen; they aren't chosen from results.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from app.risk.sizing import round_price
from app.risk.stops import compute_stop

MAX_STOP_ATR = 4.0
MIN_STOP_ATR = 0.5
E_BUFFER_ATR = 0.5
F_BUFFER_ATR = 0.25
F_FLOOR_ATR = 1.5

STOP_METHODS: dict[str, str] = {
    "A_original": "Original production stop",
    "B_atr_2_5": "2.5 ATR",
    "C_atr_3_0": "3.0 ATR",
    "D_atr_3_5": "3.5 ATR",
    "E_structural": "Structural + 0.5 ATR buffer",
    "F_hybrid": "Hybrid volatility and structure",
}
_ATR_MULT = {"B_atr_2_5": 2.5, "C_atr_3_0": 3.0, "D_atr_3_5": 3.5}


@dataclass(slots=True)
class StopContext:
    direction: str
    entry: float
    atr: float
    original_stop: float | None = None
    swing_level: float | None = None  # last confirmed swing on the stop side
    zone_level: float | None = None  # the setup's structural level
    noise_distance: float | None = None  # 80th percentile of recent 3-bar adverse moves
    tick_size: float | None = None


@dataclass(slots=True)
class StopPlan:
    method: str
    label: str
    valid: bool
    price: float | None
    distance: float | None
    distance_pct: float | None
    distance_atr: float | None
    level: float | None
    justification: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def on_stop_side(direction: str, entry: float, level: float | None) -> bool:
    """A usable invalidation level: finite and on the losing side of the entry."""
    if level is None or not math.isfinite(level) or level <= 0:
        return False
    return level < entry if direction == "LONG" else level > entry


def structural_level(c: StopContext) -> tuple[float | None, str]:
    if on_stop_side(c.direction, c.entry, c.swing_level):
        return c.swing_level, "last confirmed swing " + ("low" if c.direction == "LONG" else "high")
    if on_stop_side(c.direction, c.entry, c.zone_level):
        return c.zone_level, "setup's structural zone boundary"
    return None, ""


def _refused(method: str, why: str) -> StopPlan:
    return StopPlan(method, STOP_METHODS[method], False, None, None, None, None, None, why)


def _plan(method: str, c: StopContext, price: float, level: float | None, why: str) -> StopPlan:
    distance = abs(c.entry - price)
    if price <= 0 or distance <= 0:
        return _refused(method, "The stop would not be a valid price.")
    return StopPlan(method, STOP_METHODS[method], True, price, distance, 100 * distance / c.entry, distance / c.atr,
                    level, why)


def plan_stop(method: str, c: StopContext) -> StopPlan:
    """Stop price for one method. ``valid=False`` means the method refuses this trade."""
    if method not in STOP_METHODS:
        raise ValueError(f"unknown stop method {method}")
    if c.entry <= 0 or not c.atr > 0:
        raise ValueError("entry and ATR must be positive")
    long = c.direction == "LONG"
    if method == "A_original":
        if not on_stop_side(c.direction, c.entry, c.original_stop):
            return _refused(method, "This setup has no usable original stop.")
        return _plan(method, c, float(c.original_stop), None,  # type: ignore[arg-type]
                     "The stop the production signal uses: its zone boundary plus a 0.25 ATR buffer, at least 0.5 ATR away.")
    if method in _ATR_MULT:
        k = _ATR_MULT[method]
        st = compute_stop(c.direction, c.entry, c.atr, "atr", None, k, tick_size=c.tick_size)  # type: ignore[arg-type]
        return _plan(method, c, st.price, None, f"{k:g} x ATR from the entry. It ignores structure.")
    level, label = structural_level(c)
    if level is None:
        return _refused(method, "No confirmed structure on the stop side of the entry.")
    if method == "E_structural":
        st = compute_stop(c.direction, c.entry, c.atr, "hybrid", level, hybrid_buffer_atr=E_BUFFER_ATR,  # type: ignore[arg-type]
                          min_stop_atr=MIN_STOP_ATR, tick_size=c.tick_size, level_label=label)
        distance = st.distance
        why = f"{E_BUFFER_ATR:g} ATR {'below' if long else 'above'} the {label} ({level:.6g}), where the idea is invalidated."
    else:
        structural = abs(c.entry - level) + F_BUFFER_ATR * c.atr
        noise = c.noise_distance if c.noise_distance is not None and math.isfinite(c.noise_distance) else 0.0
        distance = max(structural, noise, F_FLOOR_ATR * c.atr)
        raw = c.entry - distance if long else c.entry + distance
        price = round_price(raw, c.tick_size, "down" if long else "up")
        driver = ("the structure" if distance == structural else "recent volatility" if distance == noise
                  else f"the {F_FLOOR_ATR:g} ATR floor")
        st = None
        why = (f"The widest of the {label} ({level:.6g}) plus {F_BUFFER_ATR:g} ATR, recent 3-bar adverse moves, and "
               f"{F_FLOOR_ATR:g} ATR. Set by {driver}.")
    if distance / c.atr > MAX_STOP_ATR:
        return _refused(method, f"The stop would be {distance / c.atr:.2f} ATR away, beyond the {MAX_STOP_ATR:g} ATR limit. "
                                "It isn't tightened to fit, because that would put it inside the structure.")
    return _plan(method, c, st.price if st is not None else price, level, why)


def plan_all(c: StopContext) -> dict[str, StopPlan]:
    return {m: plan_stop(m, c) for m in STOP_METHODS}
