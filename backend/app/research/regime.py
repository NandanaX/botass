"""Market regime classifier (causal, configurable, rule-based).

Every value at bar ``i`` uses bars ``<= i`` only. Measures:

* ``adx``: Wilder ADX (trend strength, direction-agnostic).
* ``slope``: EMA(mid) change over ``slope_lookback`` bars, in ATR per bar (trend direction).
* ``er``: Kaufman efficiency ratio over ``er_period`` bars (trend persistence, 0..1).
* ``atr_rank``: percentile of ATR%% among the previous ``rank_window`` bars (volatility level).
* ``atr_ratio``: ATR / ATR ``expansion_lookback`` bars ago (volatility expansion).
* ``ext``: (close - EMA(fast)) / ATR (recent extension).
* ``run``: signed count of consecutive closes in one direction.

Labels are checked in this order (first match wins):

1. ``INSUFFICIENT_DATA``: any measure missing.
2. ``HIGH_VOL_EXPANSION``: ``atr_rank >= high_vol_rank`` and ``atr_ratio >= expansion_ratio``.
3. ``EXHAUSTION``: ``|ext| >= exhaustion_ext_atr`` or ``|run| >= exhaustion_run``.
4. ``STRONG_BULL``/``STRONG_BEAR``: ``|slope| >= slope_min``, close on the slope side of
   EMA(mid), ``adx >= adx_strong`` and ``er >= er_trend``.
5. ``WEAK_BULL``/``WEAK_BEAR``: slope and close agree, and ``adx >= adx_weak`` or ``er >= er_weak``.
6. ``LOW_VOL_CONSOLIDATION``: ``atr_rank <= low_vol_rank`` and ``adx < adx_weak``.
7. ``SIDEWAYS``: ``adx < adx_weak`` and ``|slope| < slope_min``.
8. ``CONFLICTED``: anything else (signals disagree).

The thresholds are definitions, not fitted parameters; whether a strategy works in a regime
is decided by the experiment framework, not here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from app.analysis.indicators import adx as adx_fn
from app.strategy.features import Features

REGIMES = ["STRONG_BULL", "WEAK_BULL", "STRONG_BEAR", "WEAK_BEAR", "SIDEWAYS", "HIGH_VOL_EXPANSION",
           "LOW_VOL_CONSOLIDATION", "EXHAUSTION", "CONFLICTED", "INSUFFICIENT_DATA"]

REGIME_LABELS = {
    "STRONG_BULL": "Strong bullish trend", "WEAK_BULL": "Weak bullish trend",
    "STRONG_BEAR": "Strong bearish trend", "WEAK_BEAR": "Weak bearish trend",
    "SIDEWAYS": "Sideways / ranging", "HIGH_VOL_EXPANSION": "High-volatility expansion",
    "LOW_VOL_CONSOLIDATION": "Low-volatility consolidation", "EXHAUSTION": "Potential trend exhaustion",
    "CONFLICTED": "Conflicting / uncertain", "INSUFFICIENT_DATA": "Insufficient data",
}


class RegimeParams(BaseModel):
    adx_period: int = Field(14, ge=2, le=100)
    adx_strong: float = Field(25, ge=0, le=100)
    adx_weak: float = Field(18, ge=0, le=100)
    slope_lookback: int = Field(10, ge=1, le=200)
    slope_min: float = Field(0.03, ge=0, le=5, description="EMA(mid) slope in ATR per bar")
    er_period: int = Field(20, ge=2, le=500)
    er_trend: float = Field(0.3, ge=0, le=1)
    er_weak: float = Field(0.2, ge=0, le=1)
    rank_window: int = Field(500, ge=20, le=10_000)
    high_vol_rank: float = Field(0.9, ge=0, le=1)
    low_vol_rank: float = Field(0.15, ge=0, le=1)
    expansion_lookback: int = Field(20, ge=1, le=500)
    expansion_ratio: float = Field(1.3, ge=1, le=10)
    exhaustion_ext_atr: float = Field(3.0, ge=0, le=20)
    exhaustion_run: int = Field(7, ge=2, le=50)


@dataclass(slots=True)
class RegimeArrays:
    adx: np.ndarray
    slope: np.ndarray
    er: np.ndarray
    atr_rank: np.ndarray
    atr_ratio: np.ndarray
    ext_fast: np.ndarray
    ext_mid: np.ndarray
    run: np.ndarray
    label: np.ndarray  # object array of regime codes

    def at(self, i: int) -> dict[str, Any]:
        def num(x: float) -> float | None:
            return None if not math.isfinite(x) else round(float(x), 4)
        code = str(self.label[i])
        return {"regime": code, "label": REGIME_LABELS[code], "adx": num(self.adx[i]), "slope_atr": num(self.slope[i]),
                "efficiency": num(self.er[i]), "atr_rank": num(self.atr_rank[i]), "atr_ratio": num(self.atr_ratio[i]),
                "ext_fast_atr": num(self.ext_fast[i]), "ext_mid_atr": num(self.ext_mid[i]), "run": int(self.run[i])}


def signed_run(close: np.ndarray) -> np.ndarray:
    """+k after k consecutive higher closes, -k after k consecutive lower closes."""
    out = np.zeros(len(close), dtype=np.int64)
    for k in range(1, len(close)):
        d = close[k] - close[k - 1]
        if d > 0:
            out[k] = out[k - 1] + 1 if out[k - 1] > 0 else 1
        elif d < 0:
            out[k] = out[k - 1] - 1 if out[k - 1] < 0 else -1
    return out


def efficiency_ratio(close: np.ndarray, period: int) -> np.ndarray:
    s = pd.Series(close)
    change = (s - s.shift(period)).abs()
    vol = s.diff().abs().rolling(period).sum()
    return (change / vol.replace(0, np.nan)).to_numpy(float)


def regime_arrays(f: Features, rp: RegimeParams | None = None) -> RegimeArrays:
    rp = rp or RegimeParams()
    n = len(f)
    a, _, _ = adx_fn(f.high, f.low, f.close, rp.adx_period)
    atr = np.where(f.atr > 0, f.atr, np.nan)
    slope = (f.ema_mid - np.roll(f.ema_mid, rp.slope_lookback)) / rp.slope_lookback / atr
    slope[: rp.slope_lookback] = np.nan
    er = efficiency_ratio(f.close, rp.er_period)
    atr_pct = pd.Series(atr / f.close)
    rank = atr_pct.rolling(rp.rank_window, min_periods=min(rp.rank_window, 100)).rank(pct=True).to_numpy(float)
    ratio = atr / np.roll(atr, rp.expansion_lookback)
    ratio[: rp.expansion_lookback] = np.nan
    ext_f = (f.close - f.ema_fast) / atr
    ext_m = (f.close - f.ema_mid) / atr
    run = signed_run(f.close)
    label = np.empty(n, dtype=object)
    for i in range(n):
        vals = (a[i], slope[i], er[i], rank[i], ratio[i], ext_f[i])
        if not all(math.isfinite(v) for v in vals):
            label[i] = "INSUFFICIENT_DATA"
            continue
        if rank[i] >= rp.high_vol_rank and ratio[i] >= rp.expansion_ratio:
            label[i] = "HIGH_VOL_EXPANSION"
        elif abs(ext_f[i]) >= rp.exhaustion_ext_atr or abs(run[i]) >= rp.exhaustion_run:
            label[i] = "EXHAUSTION"
        else:
            up = slope[i] >= rp.slope_min and f.close[i] > f.ema_mid[i]
            dn = slope[i] <= -rp.slope_min and f.close[i] < f.ema_mid[i]
            if (up or dn) and a[i] >= rp.adx_strong and er[i] >= rp.er_trend:
                label[i] = "STRONG_BULL" if up else "STRONG_BEAR"
            elif (up or dn) and (a[i] >= rp.adx_weak or er[i] >= rp.er_weak):
                label[i] = "WEAK_BULL" if up else "WEAK_BEAR"
            elif rank[i] <= rp.low_vol_rank and a[i] < rp.adx_weak:
                label[i] = "LOW_VOL_CONSOLIDATION"
            elif a[i] < rp.adx_weak and abs(slope[i]) < rp.slope_min:
                label[i] = "SIDEWAYS"
            else:
                label[i] = "CONFLICTED"
    return RegimeArrays(a, slope, er, rank, ratio, ext_f, ext_m, run, label)


def regime_alignment(regime: str, direction: str) -> str:
    """WITH (trend regime in the trade direction), AGAINST, or NEUTRAL."""
    if regime in ("STRONG_BULL", "WEAK_BULL"):
        return "WITH" if direction == "LONG" else "AGAINST"
    if regime in ("STRONG_BEAR", "WEAK_BEAR"):
        return "WITH" if direction == "SHORT" else "AGAINST"
    return "NEUTRAL"
