"""Per-timeframe market context as known at a decision time.

Each timeframe is classified on its own into one bias, from three separate kinds of evidence: trend
(EMA alignment and slope), structure (confirmed swings and BOS/CHoCH) and strength (ADX). RSI and MACD
momentum never change the bias. Momentum against an intact trend marks a pullback, not a reversal.

Only candles closed at the decision time are read: ``htf_index`` picks the last closed candle, swings
count only once confirmed, and a timeframe whose latest closed candle is missing or stale reports
INSUFFICIENT_DATA instead of a guess.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.analysis.frame import build_frame
from app.analysis.indicators import adx as adx_fn
from app.analysis.params import AnalysisParams
from app.analysis.structure import all_structure_events
from app.strategy.features import Features, compute_features
from app.strategy.setups import htf_index, zones_at
from app.strategy_d.params import DParams
from app.timeutil import tf_delta

BIASES = ("BULLISH", "BEARISH", "SIDEWAYS", "CONFLICTED", "INSUFFICIENT_DATA")


@dataclass(slots=True)
class TfData:
    """Candle features plus the arrays only D needs. ``break_dir[i]``: direction of the last BOS or
    CHoCH whose candle closed at or before ``i``. ``break_kind[i]``: its kind."""

    f: Features
    adx: np.ndarray
    break_dir: np.ndarray
    break_kind: np.ndarray


def prepare(df: pd.DataFrame, tf: str, p: DParams) -> TfData:
    f = compute_features(df, tf, p.base)
    a, _, _ = adx_fn(f.high, f.low, f.close, p.adx_period)
    n = len(f)
    direction, kind = np.zeros(n, dtype=np.int8), np.full(n, "", dtype=object)
    if n:
        for e in all_structure_events(build_frame(df, tf, p.base, AnalysisParams(), f)):
            direction[e.index:] = 1 if e.direction == "BULLISH" else -1
            kind[e.index:] = e.kind
    return TfData(f, a, direction, kind)


@dataclass(slots=True)
class TfState:
    timeframe: str
    bias: str
    trend: int = 0
    structure: int = 0
    momentum: int = 0
    adx: float | None = None
    ema_slope_atr: float | None = None
    rsi: float | None = None
    macd_hist: float | None = None
    atr_pct: float | None = None
    last_break: str | None = None
    last_break_dir: int = 0
    pullback: bool = False
    candle_close_ns: int | None = None
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(x: Any) -> float | None:
    x = float(x)
    return x if math.isfinite(x) else None


def classify_at(d: TfData, j: int, p: DParams) -> TfState:
    """The bias of one timeframe at its closed candle ``j``."""
    f = d.f
    tf = f.timeframe
    if j < p.min_bars or j >= len(f):
        return TfState(tf, "INSUFFICIENT_DATA", why=f"needs {p.min_bars} closed candles")
    atr, adx = float(f.atr[j]), float(d.adx[j])
    if not (math.isfinite(atr) and atr > 0 and math.isfinite(adx) and math.isfinite(float(f.ema_slow[j]))):
        return TfState(tf, "INSUFFICIENT_DATA", why="indicators are not ready")
    slope = (float(f.ema_mid[j]) - float(f.ema_mid[j - p.slope_bars])) / p.slope_bars / atr
    trend = 1 if f.ema_fast[j] > f.ema_mid[j] > f.ema_slow[j] and slope > 0 else -1 if f.ema_fast[j] < f.ema_mid[j] < f.ema_slow[j] and slope < 0 else 0
    swings = int(f.structure.structure[j])
    structure = swings if swings else int(d.break_dir[j])
    rsi, hist = float(f.rsi[j]), float(f.macd_hist[j])
    momentum = 1 if rsi > 50 and hist > 0 else -1 if rsi < 50 and hist < 0 else 0
    strong = adx >= p.adx_min
    if trend == 0:
        bias, why = ("CONFLICTED", "EMAs are mixed but ADX shows a directional market") if strong else ("SIDEWAYS", "EMAs are mixed and ADX is low")
    elif structure == -trend:
        bias, why = "CONFLICTED", "the EMA trend and the swing structure disagree"
    elif structure == 0 and not strong:
        bias, why = "SIDEWAYS", "an EMA trend without structure or ADX support"
    else:
        bias, why = ("BULLISH", "EMA trend and structure agree upward") if trend > 0 else ("BEARISH", "EMA trend and structure agree downward")
    pullback = bias in ("BULLISH", "BEARISH") and momentum == -trend
    if pullback:
        why += "; momentum against it reads as a pullback"
    return TfState(tf, bias, trend, structure, momentum, adx, slope, _finite(rsi), _finite(hist), 100 * atr / float(f.close[j]),
                   str(d.break_kind[j]) or None, int(d.break_dir[j]), pullback, int(f.close_time[j]), why)


def state_at_decision(d: TfData | None, tf: str, decision_ns: int, p: DParams) -> TfState:
    """The latest confirmed candle of ``tf`` at ``decision_ns``; an old one counts as missing."""
    if d is None or not len(d.f):
        return TfState(tf, "INSUFFICIENT_DATA", why="no candles loaded")
    j = htf_index(d.f, decision_ns)
    if j < 0:
        return TfState(tf, "INSUFFICIENT_DATA", why="no candle closed before this time")
    if decision_ns - int(d.f.close_time[j]) >= tf_delta(tf).total_seconds() * 1e9:
        return TfState(tf, "INSUFFICIENT_DATA", why="the latest closed candle is missing")
    return classify_at(d, j, p)


def nearest_levels(data: dict[str, TfData], symbol: str | None, entry: float, decision_ns: int, p: DParams) -> dict[str, dict[str, float | None]]:
    """Nearest support and resistance per timeframe from zones known at the decision time. ``resistance``
    is the lower edge above the entry and ``support`` the upper edge below it."""
    out: dict[str, dict[str, float | None]] = {}
    for tf, d in data.items():
        j = htf_index(d.f, decision_ns)
        res, sup = None, None
        if j >= p.min_bars:
            for z in zones_at(d.f, j, p.base, symbol, None):
                if z.status == "INVALIDATED":
                    continue
                if z.lower > entry and (res is None or z.lower < res):
                    res = float(z.lower)
                if z.upper < entry and (sup is None or z.upper > sup):
                    sup = float(z.upper)
        out[tf] = {"support": sup, "resistance": res}
    return out
