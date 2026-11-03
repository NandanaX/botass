"""Technical indicators.

Every function here is *causal*: the value at index ``i`` depends only on inputs at
indices ``<= i``. That property is what lets the live scanner and the backtester share
the same precomputed feature arrays without look-ahead bias (verified in tests).

Conventions follow the widely used definitions (TradingView / Wilder):

* EMA is seeded with the SMA of the first ``period`` valid values.
* RSI and ATR use Wilder's smoothing (RMA, alpha = 1/period), also SMA-seeded.
* Bollinger Bands use the population standard deviation (ddof=0).

Warm-up values are ``NaN``.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


def _as_float(values: npt.ArrayLike) -> FloatArray:
    return np.asarray(values, dtype=np.float64)


def _first_valid(values: FloatArray) -> int:
    valid = np.flatnonzero(~np.isnan(values))
    return int(valid[0]) if valid.size else len(values)


def _seeded_recursive(values: FloatArray, period: int, alpha: float) -> FloatArray:
    if period < 1:
        raise ValueError("period must be >= 1")
    out = np.full(values.shape, np.nan)
    start = _first_valid(values)
    seed_end = start + period  # exclusive
    if seed_end > len(values):
        return out
    window = values[start:seed_end]
    if np.isnan(window).any():
        raise ValueError("input contains gaps (NaN) after the first valid value")
    prev = float(window.mean())
    out[seed_end - 1] = prev
    for i in range(seed_end, len(values)):
        prev = alpha * values[i] + (1.0 - alpha) * prev
        out[i] = prev
    return out


def sma(values: npt.ArrayLike, period: int) -> FloatArray:
    x = _as_float(values)
    out = np.full(x.shape, np.nan)
    if period < 1:
        raise ValueError("period must be >= 1")
    if len(x) < period:
        return out
    csum = np.cumsum(np.insert(x, 0, 0.0))
    out[period - 1 :] = (csum[period:] - csum[:-period]) / period
    return out


def ema(values: npt.ArrayLike, period: int) -> FloatArray:
    return _seeded_recursive(_as_float(values), period, 2.0 / (period + 1.0))


def rma(values: npt.ArrayLike, period: int) -> FloatArray:
    """Wilder's moving average."""
    return _seeded_recursive(_as_float(values), period, 1.0 / period)


def rsi(close: npt.ArrayLike, period: int = 14) -> FloatArray:
    c = _as_float(close)
    out = np.full(c.shape, np.nan)
    if len(c) <= period:
        return out
    delta = np.diff(c)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = rma(gains, period)
    avg_loss = rma(losses, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        values = 100.0 - 100.0 / (1.0 + rs)
    values = np.where((avg_loss == 0) & (avg_gain > 0), 100.0, values)
    values = np.where((avg_loss == 0) & (avg_gain == 0), 50.0, values)
    values = np.where(np.isnan(avg_gain), np.nan, values)
    out[1:] = values
    return out


def macd(
    close: npt.ArrayLike, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Return (macd_line, signal_line, histogram)."""
    if fast >= slow:
        raise ValueError("MACD fast period must be shorter than slow period")
    c = _as_float(close)
    line = ema(c, fast) - ema(c, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def true_range(high: npt.ArrayLike, low: npt.ArrayLike, close: npt.ArrayLike) -> FloatArray:
    h, lo, c = _as_float(high), _as_float(low), _as_float(close)
    prev_close = np.roll(c, 1)
    tr = np.maximum.reduce([h - lo, np.abs(h - prev_close), np.abs(lo - prev_close)])
    if len(tr):
        tr[0] = h[0] - lo[0]
    return tr


def atr(high: npt.ArrayLike, low: npt.ArrayLike, close: npt.ArrayLike, period: int = 14) -> FloatArray:
    return rma(true_range(high, low, close), period)


def bollinger(
    close: npt.ArrayLike, period: int = 20, num_std: float = 2.0
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Return (upper, middle, lower)."""
    c = _as_float(close)
    mid = sma(c, period)
    std = np.full(c.shape, np.nan)
    if len(c) >= period:
        windows = np.lib.stride_tricks.sliding_window_view(c, period)
        std[period - 1 :] = windows.std(axis=1, ddof=0)
    return mid + num_std * std, mid, mid - num_std * std


def rolling_max_prior(values: npt.ArrayLike, lookback: int) -> FloatArray:
    """Highest value of the ``lookback`` bars *before* index i (excludes bar i)."""
    x = _as_float(values)
    out = np.full(x.shape, np.nan)
    if len(x) <= lookback:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(x, lookback)
    out[lookback:] = windows.max(axis=1)[:-1]
    return out


def rolling_min_prior(values: npt.ArrayLike, lookback: int) -> FloatArray:
    """Lowest value of the ``lookback`` bars *before* index i (excludes bar i)."""
    x = _as_float(values)
    out = np.full(x.shape, np.nan)
    if len(x) <= lookback:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(x, lookback)
    out[lookback:] = windows.min(axis=1)[:-1]
    return out


def relative_volume(volume: npt.ArrayLike, period: int = 20) -> tuple[FloatArray, FloatArray]:
    """Return (volume_ma_prior, relative_volume).

    The baseline is the mean volume of the ``period`` bars before the current bar, so a
    volume spike on the current bar does not inflate its own baseline.
    """
    v = _as_float(volume)
    base = np.full(v.shape, np.nan)
    ma = sma(v, period)
    base[1:] = ma[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        rvol = np.where(base > 0, v / base, np.nan)
    return base, rvol
