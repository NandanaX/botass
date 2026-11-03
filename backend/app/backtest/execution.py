"""Execution model shared by the backtester and the live-signal outcome evaluator.

OHLC candles do not reveal the order of prices inside a bar, so these rules apply:

* A stop or target counts as touched when the bar's low/high reaches its level.
* If the stop and the target are both touched within the same bar, the stop is assumed
  to have been hit first (conservative). The exit reason is ``AMBIGUOUS_STOP``.
* If a bar *opens* beyond the stop (a gap), the exit fills at that open, which is
  worse than the stop. A gap beyond the target fills at that open (limit order behaviour).
* Stop, time and end-of-data exits are market orders and pay adverse slippage. Target
  exits are treated as resting limit orders and pay no slippage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

Direction = Literal["LONG", "SHORT"]
ExitReason = Literal["TARGET", "TARGET_GAP", "STOP", "STOP_GAP", "AMBIGUOUS_STOP", "TIME_EXIT", "END_OF_DATA",
                     "OPEN"]

MARKET_EXITS = {"STOP", "STOP_GAP", "AMBIGUOUS_STOP", "TIME_EXIT", "END_OF_DATA"}
MARKET_EXITS_ALL = MARKET_EXITS | {"BREAKEVEN_STOP"}  # partial-exit reasons that are market orders


@dataclass(frozen=True, slots=True)
class ExitResult:
    exit_index: int
    exit_price: float  # before slippage
    reason: ExitReason
    bars_held: int


def apply_slippage(price: float, direction: Direction, side: Literal["entry", "exit"], slippage_frac: float) -> float:
    """Adverse slippage: buying fills higher, selling fills lower."""
    buying = (direction == "LONG") == (side == "entry")
    return price * (1 + slippage_frac) if buying else price * (1 - slippage_frac)


def simulate_exit(
    direction: Direction,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    start_index: int,
    stop: float,
    target: float,
    max_bars: int | None,
    check_gap_on_first_bar: bool,
    final_if_unresolved: Literal["END_OF_DATA", "OPEN"] = "END_OF_DATA",
) -> ExitResult:
    """Walk bars from ``start_index`` (the first bar during which the position is open)
    until the stop or target is touched, ``max_bars`` elapse, or data runs out."""
    n = len(close)
    if start_index >= n:
        return ExitResult(n - 1, float(close[-1]), final_if_unresolved, 0)
    last = n - 1 if not max_bars else min(n - 1, start_index + max_bars - 1)
    long = direction == "LONG"
    for k in range(start_index, last + 1):
        if k > start_index or check_gap_on_first_bar:
            o = open_[k]
            if (long and o <= stop) or (not long and o >= stop):
                return ExitResult(k, float(o), "STOP_GAP", k - start_index + 1)
            if (long and o >= target) or (not long and o <= target):
                return ExitResult(k, float(o), "TARGET_GAP", k - start_index + 1)
        hit_stop = low[k] <= stop if long else high[k] >= stop
        hit_target = high[k] >= target if long else low[k] <= target
        if hit_stop and hit_target:
            return ExitResult(k, float(stop), "AMBIGUOUS_STOP", k - start_index + 1)
        if hit_stop:
            return ExitResult(k, float(stop), "STOP", k - start_index + 1)
        if hit_target:
            return ExitResult(k, float(target), "TARGET", k - start_index + 1)
    held = last - start_index + 1
    if max_bars and held >= max_bars:
        return ExitResult(last, float(close[last]), "TIME_EXIT", held)
    return ExitResult(last, float(close[last]), final_if_unresolved, held)


@dataclass(frozen=True, slots=True)
class PartialFill:
    index: int
    price: float  # before slippage
    fraction: float  # of the original position
    reason: str  # TP1..TPn, TPn_GAP, STOP, STOP_GAP, AMBIGUOUS_STOP, BREAKEVEN_STOP, TIME_EXIT, END_OF_DATA, OPEN


@dataclass(frozen=True, slots=True)
class PartialExitResult:
    fills: list[PartialFill]
    exit_index: int  # bar of the last fill
    bars_held: int
    targets_hit: int
    final_reason: str


def simulate_partial_exits(
    direction: Direction,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    start_index: int,
    entry: float,
    stop: float,
    targets: list[tuple[float, float]],
    max_bars: int | None,
    check_gap_on_first_bar: bool,
    move_stop_to_breakeven: bool = False,
    final_if_unresolved: Literal["END_OF_DATA", "OPEN"] = "END_OF_DATA",
) -> PartialExitResult:
    """Scale out at several targets. ``targets``: ``(price, fraction)`` ordered from the
    nearest to the furthest; fractions sum to 1 (zero-fraction targets are skipped).

    Same conservative rules as :func:`simulate_exit`, applied to the remaining position:
    if the stop and any pending target are touched in the same candle, the stop is assumed
    first for everything still open. A gap through targets fills them at the open. With
    ``move_stop_to_breakeven`` the stop moves to ``entry`` from the candle after TP1 fills.
    """
    long = direction == "LONG"
    pending = [(k, p, frac) for k, (p, frac) in enumerate(targets) if frac > 0]
    remaining = sum(frac for _, _, frac in pending)
    fills: list[PartialFill] = []
    n = len(close)
    cur_stop = stop
    be_active = False
    if start_index >= n:
        return PartialExitResult([PartialFill(n - 1, float(close[-1]), remaining, final_if_unresolved)], n - 1, 0, 0,
                                 final_if_unresolved)
    last = n - 1 if not max_bars else min(n - 1, start_index + max_bars - 1)
    hit_count = 0
    for k in range(start_index, last + 1):
        stop_reason = "BREAKEVEN_STOP" if be_active else "STOP"
        if k > start_index or check_gap_on_first_bar:
            o = open_[k]
            if (long and o <= cur_stop) or (not long and o >= cur_stop):
                fills.append(PartialFill(k, float(o), remaining, "STOP_GAP"))
                return PartialExitResult(fills, k, k - start_index + 1, hit_count, "STOP_GAP")
            while pending and ((long and o >= pending[0][1]) or (not long and o <= pending[0][1])):
                idx, _, frac = pending.pop(0)
                fills.append(PartialFill(k, float(o), frac, f"TP{idx + 1}_GAP"))
                remaining -= frac
                hit_count += 1
            if not pending:
                return PartialExitResult(fills, k, k - start_index + 1, hit_count, fills[-1].reason)
        hit_stop = low[k] <= cur_stop if long else high[k] >= cur_stop
        touched = [t for t in pending if (high[k] >= t[1] if long else low[k] <= t[1])]
        if hit_stop:
            reason = "AMBIGUOUS_STOP" if touched else stop_reason
            fills.append(PartialFill(k, float(cur_stop), remaining, reason))
            return PartialExitResult(fills, k, k - start_index + 1, hit_count, reason)
        for t in touched:
            pending.remove(t)
            fills.append(PartialFill(k, float(t[1]), t[2], f"TP{t[0] + 1}"))
            remaining -= t[2]
            hit_count += 1
        if not pending:
            return PartialExitResult(fills, k, k - start_index + 1, hit_count, fills[-1].reason)
        if move_stop_to_breakeven and hit_count >= 1 and not be_active:
            cur_stop, be_active = entry, True
    held = last - start_index + 1
    reason = "TIME_EXIT" if max_bars and held >= max_bars else final_if_unresolved
    if reason == "OPEN":
        return PartialExitResult(fills, last, held, hit_count, "OPEN")
    fills.append(PartialFill(last, float(close[last]), remaining, reason))
    return PartialExitResult(fills, last, held, hit_count, reason)
