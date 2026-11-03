"""Price-action and market-structure detection without look-ahead.

A swing high at bar ``j`` with strength ``k`` requires ``k`` bars on each side, so it can
only be *known* at bar ``j + k``. All per-bar outputs below expose a pivot only from its
confirmation bar onwards; that is what prevents future candles from leaking into a
decision made at bar ``i``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.indicators.core import FloatArray


@dataclass(frozen=True, slots=True)
class Pivot:
    index: int  # bar of the extreme
    confirmed_at: int  # first bar at which the pivot is known
    price: float
    kind: str  # "high" | "low"


def find_pivots(high: FloatArray, low: FloatArray, strength: int = 3) -> list[Pivot]:
    """Swing highs/lows: strictly greater (less) than the ``strength`` bars to the left and
    greater-or-equal (less-or-equal) to the ``strength`` bars to the right."""
    n = len(high)
    pivots: list[Pivot] = []
    k = strength
    for j in range(k, n - k):
        h = high[j]
        if h > high[j - k : j].max() and h >= high[j + 1 : j + k + 1].max():
            pivots.append(Pivot(j, j + k, float(h), "high"))
        lo = low[j]
        if lo < low[j - k : j].min() and lo <= low[j + 1 : j + k + 1].min():
            pivots.append(Pivot(j, j + k, float(lo), "low"))
    pivots.sort(key=lambda p: (p.confirmed_at, p.index))
    return pivots


@dataclass(slots=True)
class StructureArrays:
    last_swing_high: FloatArray
    prev_swing_high: FloatArray
    last_swing_low: FloatArray
    prev_swing_low: FloatArray
    # +1 bullish (HH & HL), -1 bearish (LH & LL), 0 mixed/undetermined
    structure: np.ndarray


def structure_arrays(high: FloatArray, low: FloatArray, strength: int = 3) -> StructureArrays:
    n = len(high)
    last_h = np.full(n, np.nan)
    prev_h = np.full(n, np.nan)
    last_l = np.full(n, np.nan)
    prev_l = np.full(n, np.nan)
    pivots = find_pivots(high, low, strength)
    cur_lh = cur_ph = cur_ll = cur_pl = np.nan
    p = 0
    for i in range(n):
        while p < len(pivots) and pivots[p].confirmed_at <= i:
            pv = pivots[p]
            if pv.kind == "high":
                cur_ph, cur_lh = cur_lh, pv.price
            else:
                cur_pl, cur_ll = cur_ll, pv.price
            p += 1
        last_h[i], prev_h[i], last_l[i], prev_l[i] = cur_lh, cur_ph, cur_ll, cur_pl
    with np.errstate(invalid="ignore"):
        hh = last_h > prev_h
        hl = last_l > prev_l
        lh = last_h < prev_h
        ll = last_l < prev_l
    struct = np.zeros(n, dtype=np.int8)
    struct[hh & hl] = 1
    struct[lh & ll] = -1
    return StructureArrays(last_h, prev_h, last_l, prev_l, struct)


def structure_label(value: int, last_h: float, prev_h: float, last_l: float, prev_l: float) -> str:
    if np.isnan(prev_h) or np.isnan(prev_l):
        return "insufficient swings"
    hi = "HH" if last_h > prev_h else "LH" if last_h < prev_h else "EH"
    lo = "HL" if last_l > prev_l else "LL" if last_l < prev_l else "EL"
    return f"{hi}/{lo}"


@dataclass(frozen=True, slots=True)
class Level:
    price: float
    kind: str  # "support" | "resistance"
    touches: int
    last_index: int


def support_resistance(
    high: FloatArray,
    low: FloatArray,
    close: FloatArray,
    atr_value: float,
    at_index: int,
    strength: int = 3,
    lookback: int = 300,
    tolerance_atr: float = 0.5,
    max_levels: int = 3,
) -> list[Level]:
    """Cluster confirmed swing points (known at ``at_index``) into horizontal levels.

    Levels below the close of ``at_index`` are supports; levels above are resistances.
    """
    if at_index < 0 or not np.isfinite(atr_value) or atr_value <= 0:
        return []
    start = max(0, at_index - lookback)
    h = high[: at_index + 1]
    lo = low[: at_index + 1]
    pivots = [p for p in find_pivots(h, lo, strength) if p.confirmed_at <= at_index and p.index >= start]
    tol = tolerance_atr * atr_value
    clusters: list[list[Pivot]] = []
    for pv in sorted(pivots, key=lambda p: p.price):
        if clusters and abs(pv.price - np.mean([c.price for c in clusters[-1]])) <= tol:
            clusters[-1].append(pv)
        else:
            clusters.append([pv])
    price = float(close[at_index])
    levels = [
        Level(
            price=float(np.mean([c.price for c in cl])),
            kind="support" if np.mean([c.price for c in cl]) < price else "resistance",
            touches=len(cl),
            last_index=max(c.index for c in cl),
        )
        for cl in clusters
    ]
    supports = sorted((lv for lv in levels if lv.kind == "support"), key=lambda lv: price - lv.price)
    resistances = sorted((lv for lv in levels if lv.kind == "resistance"), key=lambda lv: lv.price - price)
    return supports[:max_levels] + resistances[:max_levels]
