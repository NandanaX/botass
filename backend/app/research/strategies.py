"""Research-only strategy families, evaluated in shadow mode next to the production
breakout and pullback strategies. They never send alerts.

All rules read bars ``<= i`` only and produce a hypothetical entry at the next open.
Each returns ``(direction, stop, target, structural_level, note)`` or ``None``.

* ``breakout_retest``: a close beyond the prior ``lookback``-bar high (low) happened 3-12
  bars ago; price has come back to that level (low within 0.2 ATR of it) and closed back
  beyond it with a directional candle. Stop: level -/+ 0.5 ATR (min 0.5 ATR from entry).
* ``range_reversal``: regime is SIDEWAYS or LOW_VOL_CONSOLIDATION; the candle wicks
  through the prior 48-bar low (high) and closes back inside in the upper (lower) 40%% of
  its range with RSI < 45 (> 55). Stop: beyond the wick + 0.25 ATR (min 0.5 ATR). Target:
  the middle of the 48-bar range, only if it is at least 1.2R away.
* ``momentum_continuation``: regime is STRONG_BULL (STRONG_BEAR); the close sets a new
  20-bar high (low) with relative volume >= 1.5, body >= 50%% of the range, ADX rising and
  close within 2 ATR of EMA(fast). Stop: 1.5 ATR. Target: 2R.

Production strategies use the trend definition EMA fast > mid > slow; these families
use the regime classifier instead, which is what makes them comparable per regime.
"""

from __future__ import annotations

import math

import numpy as np

from app.research.regime import RegimeArrays
from app.strategy.features import Features

RESEARCH_STRATEGIES = ["breakout_retest", "range_reversal", "momentum_continuation"]


def _rmax(a: np.ndarray, i: int, n: int) -> float:
    return float(np.max(a[i - n:i])) if i >= n else math.nan


def _rmin(a: np.ndarray, i: int, n: int) -> float:
    return float(np.min(a[i - n:i])) if i >= n else math.nan


def breakout_retest(f: Features, ra: RegimeArrays, i: int, lookback: int = 20):
    atr = float(f.atr[i])
    if not atr > 0 or i < lookback + 13:
        return None
    c, o, h, lo = f.close[i], f.open[i], f.high[i], f.low[i]
    for direction in ("LONG", "SHORT"):
        long = direction == "LONG"
        for b in range(i - 12, i - 2):
            level = f.prior_high[b] if long else f.prior_low[b]
            if not math.isfinite(level):
                continue
            broke = f.close[b] > level + 0.1 * atr if long else f.close[b] < level - 0.1 * atr
            if not broke:
                continue
            # No close back through the level between the break and now.
            seg = f.close[b + 1:i]
            if len(seg) and (np.min(seg) < level if long else np.max(seg) > level):
                continue
            touched = lo <= level + 0.2 * atr if long else h >= level - 0.2 * atr
            held = (c > level and c > o) if long else (c < level and c < o)
            if touched and held:
                stop = level - 0.5 * atr if long else level + 0.5 * atr
                if abs(c - stop) < 0.5 * atr:
                    stop = c - 0.5 * atr if long else c + 0.5 * atr
                risk = abs(c - stop)
                target = c + 2 * risk if long else c - 2 * risk
                return direction, float(stop), float(target), float(level), f"retest of level broken {i - b} bars ago"
    return None


def range_reversal(f: Features, ra: RegimeArrays, i: int, window: int = 48):
    if ra.label[i] not in ("SIDEWAYS", "LOW_VOL_CONSOLIDATION"):
        return None
    atr = float(f.atr[i])
    hi_r, lo_r = _rmax(f.high, i, window), _rmin(f.low, i, window)
    if not (atr > 0 and math.isfinite(hi_r) and math.isfinite(lo_r)):
        return None
    c, h, lo = f.close[i], f.high[i], f.low[i]
    rng = h - lo
    if rng <= 0:
        return None
    mid = (hi_r + lo_r) / 2
    if lo < lo_r < c and (c - lo) / rng >= 0.6 and f.rsi[i] < 45:
        stop = min(lo - 0.25 * atr, c - 0.5 * atr)
        risk = c - stop
        if mid - c >= 1.2 * risk:
            return "LONG", float(stop), float(mid), float(lo_r), "swept the range low and closed back inside"
    if h > hi_r > c and (h - c) / rng >= 0.6 and f.rsi[i] > 55:
        stop = max(h + 0.25 * atr, c + 0.5 * atr)
        risk = stop - c
        if c - mid >= 1.2 * risk:
            return "SHORT", float(stop), float(mid), float(hi_r), "swept the range high and closed back inside"
    return None


def momentum_continuation(f: Features, ra: RegimeArrays, i: int, lookback: int = 20):
    lab = ra.label[i]
    if lab not in ("STRONG_BULL", "STRONG_BEAR") or i < 1:
        return None
    atr = float(f.atr[i])
    long = lab == "STRONG_BULL"
    c, o, h, lo = f.close[i], f.open[i], f.high[i], f.low[i]
    rng = h - lo
    if not atr > 0 or rng <= 0:
        return None
    new_ext = c > f.prior_high[i] if long else c < f.prior_low[i]
    body = (c - o) / rng if long else (o - c) / rng
    adx_up = math.isfinite(ra.adx[i - 1]) and ra.adx[i] > ra.adx[i - 1]
    near = abs(ra.ext_fast[i]) <= 2.0
    if new_ext and body >= 0.5 and f.rvol[i] >= 1.5 and adx_up and near:
        stop = c - 1.5 * atr if long else c + 1.5 * atr
        target = c + 3.0 * atr if long else c - 3.0 * atr
        return ("LONG" if long else "SHORT"), float(stop), float(target), None, "new 20-bar extreme on volume"
    return None


GENERATORS = {"breakout_retest": breakout_retest, "range_reversal": range_reversal,
              "momentum_continuation": momentum_continuation}
