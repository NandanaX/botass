"""Additional causal indicators for the analysis engine.

All functions return arrays aligned with the input; ``nan`` until enough history exists.
Value ``i`` depends only on inputs ``<= i``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.core import FloatArray, _as_float, ema, rma, rsi, sma


def _sma_nan(values, period: int) -> FloatArray:
    """Rolling mean that is ``nan`` until ``period`` consecutive finite values exist
    (``core.sma`` assumes finite input from the first bar)."""
    return pd.Series(_as_float(values)).rolling(period, min_periods=period).mean().to_numpy()


def adx(high, low, close, period: int = 14) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Wilder's ADX with +DI / -DI."""
    h, lo, c = _as_float(high), _as_float(low), _as_float(close)
    n = len(c)
    up = np.full(n, np.nan)
    dn = np.full(n, np.nan)
    tr = np.full(n, np.nan)
    if n > 1:
        up_move = h[1:] - h[:-1]
        down_move = lo[:-1] - lo[1:]
        up[1:] = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        dn[1:] = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr[1:] = np.maximum(h[1:] - lo[1:], np.maximum(abs(h[1:] - c[:-1]), abs(lo[1:] - c[:-1])))
    atr_ = rma(tr, period)
    with np.errstate(invalid="ignore", divide="ignore"):
        pdi = 100 * rma(up, period) / atr_
        mdi = 100 * rma(dn, period) / atr_
        dx = 100 * abs(pdi - mdi) / (pdi + mdi)
    dx = np.where(np.isfinite(dx), dx, np.nan)
    return rma(dx, period), pdi, mdi


def supertrend(high, low, close, atr_values, multiplier: float = 3.0) -> tuple[FloatArray, np.ndarray]:
    """Supertrend line and direction (+1 up, -1 down, 0 undefined)."""
    h, lo, c = _as_float(high), _as_float(low), _as_float(close)
    a = _as_float(atr_values)
    n = len(c)
    line = np.full(n, np.nan)
    direction = np.zeros(n, dtype=np.int8)
    upper = lower = np.nan
    d = 0
    for i in range(n):
        if not np.isfinite(a[i]):
            continue
        mid = (h[i] + lo[i]) / 2
        bu, bl = mid + multiplier * a[i], mid - multiplier * a[i]
        if d == 0:
            upper, lower, d = bu, bl, 1 if c[i] >= mid else -1
        else:
            upper = bu if (bu < upper or c[i - 1] > upper) else upper
            lower = bl if (bl > lower or c[i - 1] < lower) else lower
            if d == 1 and c[i] < lower:
                d = -1
            elif d == -1 and c[i] > upper:
                d = 1
        direction[i] = d
        line[i] = lower if d == 1 else upper
    return line, direction


def stochastic(high, low, close, k_period: int = 14, k_smooth: int = 3, d_period: int = 3
               ) -> tuple[FloatArray, FloatArray]:
    h, lo, c = _as_float(high), _as_float(low), _as_float(close)
    n = len(c)
    raw = np.full(n, np.nan)
    for i in range(k_period - 1, n):
        hh, ll = h[i - k_period + 1: i + 1].max(), lo[i - k_period + 1: i + 1].min()
        raw[i] = 50.0 if hh == ll else 100 * (c[i] - ll) / (hh - ll)
    k = _sma_nan(raw, k_smooth)
    return k, _sma_nan(k, d_period)


def stoch_rsi(close, rsi_period: int = 14, stoch_period: int = 14, k_smooth: int = 3, d_period: int = 3
              ) -> tuple[FloatArray, FloatArray]:
    r = rsi(close, rsi_period)
    n = len(r)
    raw = np.full(n, np.nan)
    for i in range(n):
        w = r[max(0, i - stoch_period + 1): i + 1]
        if i - stoch_period + 1 < 0 or not np.isfinite(w).all():
            continue
        hi, lo = w.max(), w.min()
        raw[i] = 50.0 if hi == lo else 100 * (r[i] - lo) / (hi - lo)
    k = _sma_nan(raw, k_smooth)
    return k, _sma_nan(k, d_period)


def bb_width(upper, mid, lower) -> FloatArray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return (_as_float(upper) - _as_float(lower)) / _as_float(mid) * 100


def obv(close, volume) -> FloatArray:
    c, v = _as_float(close), _as_float(volume)
    out = np.zeros(len(c))
    for i in range(1, len(c)):
        out[i] = out[i - 1] + (v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0.0)
    return out


def session_vwap(high, low, close, volume, open_time_ns: np.ndarray, session_seconds: int) -> FloatArray:
    """VWAP of typical price, reset at each UTC session boundary (e.g. 86400 = daily)."""
    tp = (_as_float(high) + _as_float(low) + _as_float(close)) / 3
    v = _as_float(volume)
    out = np.full(len(tp), np.nan)
    sess = (open_time_ns // 1_000_000_000) // session_seconds
    pv = vv = 0.0
    for i in range(len(tp)):
        if i == 0 or sess[i] != sess[i - 1]:
            pv = vv = 0.0
        pv += tp[i] * v[i]
        vv += v[i]
        out[i] = pv / vv if vv > 0 else tp[i]
    return out


def vwap_session_seconds(timeframe: str) -> int | None:
    """Daily sessions for intraday charts, weekly for 4h/1d; VWAP is not used on 1w."""
    return {"5m": 86400, "15m": 86400, "30m": 86400, "1h": 86400, "4h": 7 * 86400, "1d": 7 * 86400}.get(timeframe)


def slope(values: FloatArray, i: int, lookback: int = 5) -> float:
    """Change per bar over ``lookback`` bars ending at ``i`` (nan if unavailable)."""
    if i - lookback < 0 or not (np.isfinite(values[i]) and np.isfinite(values[i - lookback])):
        return float("nan")
    return float((values[i] - values[i - lookback]) / lookback)


__all__ = ["adx", "bb_width", "ema", "obv", "session_vwap", "slope", "sma", "stoch_rsi", "stochastic", "supertrend",
           "vwap_session_seconds"]
