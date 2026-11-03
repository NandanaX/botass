from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Direction = Literal["LONG", "SHORT"]


@dataclass(frozen=True, slots=True)
class TradeLevels:
    entry: float
    stop_loss: float
    take_profit: float
    stop_distance: float
    risk_reward: float


def compute_levels(
    direction: Direction, entry: float, atr: float, atr_multiplier: float, risk_reward: float
) -> TradeLevels:
    """ATR-based stop and fixed-R target.

    LONG:  stop = entry - k*ATR,  target = entry + RR * (entry - stop)
    SHORT: stop = entry + k*ATR,  target = entry - RR * (stop - entry)
    """
    if entry <= 0 or not atr > 0 or atr_multiplier <= 0 or risk_reward <= 0:
        raise ValueError("entry, atr, atr_multiplier and risk_reward must be positive")
    distance = atr_multiplier * atr
    if direction == "LONG":
        stop = entry - distance
        target = entry + risk_reward * distance
    elif direction == "SHORT":
        stop = entry + distance
        target = entry - risk_reward * distance
    else:  # pragma: no cover - guarded by typing
        raise ValueError(f"invalid direction {direction}")
    if stop <= 0 or target <= 0:
        raise ValueError("computed stop or target is not a positive price")
    return TradeLevels(entry, stop, target, distance, risk_reward)


def realized_risk_reward(direction: Direction, entry: float, stop: float, target: float) -> float:
    """Reward/risk for arbitrary levels, validated for the trade direction."""
    if direction == "LONG":
        if not stop < entry < target:
            raise ValueError("LONG requires stop < entry < target")
        return (target - entry) / (entry - stop)
    if not target < entry < stop:
        raise ValueError("SHORT requires target < entry < stop")
    return (entry - target) / (stop - entry)
