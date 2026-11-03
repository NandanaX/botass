from __future__ import annotations

import math

import numpy as np
import pytest

from app.indicators import core
from app.indicators.structure import find_pivots, structure_arrays


def naive_ema(values, period):
    out = [math.nan] * len(values)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def test_sma_basic():
    out = core.sma([1, 2, 3, 4, 5], 3)
    assert np.isnan(out[:2]).all()
    assert out[2:].tolist() == [2.0, 3.0, 4.0]


def test_ema_matches_reference_and_is_sma_seeded():
    values = [22.27, 22.19, 22.08, 22.17, 22.18, 22.13, 22.23, 22.43, 22.24, 22.29, 22.15, 22.39, 22.38, 22.61,
              23.36, 24.05, 23.75, 23.83, 23.95, 23.63]
    out = core.ema(values, 10)
    ref = naive_ema(values, 10)
    assert np.isnan(out[:9]).all()
    assert out[9] == pytest.approx(22.221, abs=1e-3)  # SMA of first 10 values
    np.testing.assert_allclose(out[9:], ref[9:], rtol=1e-12)
    # Known value from the classic StockCharts EMA(10) example.
    assert out[10] == pytest.approx(22.208, abs=1e-3)


def test_ema_constant_series():
    out = core.ema([5.0] * 50, 20)
    np.testing.assert_allclose(out[19:], 5.0)


def test_rsi_wilder_reference_values():
    # Wilder RSI(14) worked example (StockCharts "RSI" ChartSchool article).
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28,
              46.28, 46.00, 46.03, 46.41, 46.22, 45.64]
    out = core.rsi(closes, 14)
    assert np.isnan(out[:14]).all()
    # The article rounds averages to 2 decimals (0.24/0.10 -> 70.53); unrounded Wilder math gives 70.464.
    assert out[14] == pytest.approx(70.53, abs=0.1)
    assert out[15] == pytest.approx(66.32, abs=0.1)
    assert out[16] == pytest.approx(66.55, abs=0.1)
    # Exact values from an independent loop implementation of Wilder smoothing.
    np.testing.assert_allclose(out[14:17], [70.464135, 66.249619, 66.480942], atol=1e-5)


def test_rsi_extremes():
    up = core.rsi(np.arange(1, 40, dtype=float), 14)
    assert up[-1] == 100.0
    down = core.rsi(np.arange(40, 1, -1, dtype=float), 14)
    assert down[-1] == pytest.approx(0.0)
    flat = core.rsi([10.0] * 30, 14)
    assert flat[-1] == 50.0


def test_macd_composition(candles):
    c = candles["close"].to_numpy()
    line, sig, hist = core.macd(c, 12, 26, 9)
    np.testing.assert_allclose(line, core.ema(c, 12) - core.ema(c, 26), equal_nan=True)
    first = 25  # first valid MACD line value
    assert np.isnan(line[:first]).all() and not np.isnan(line[first])
    ref_sig = naive_ema(list(line[first:]), 9)
    np.testing.assert_allclose(sig[first + 8:], ref_sig[8:], rtol=1e-10)
    np.testing.assert_allclose(hist, line - sig, equal_nan=True)


def test_macd_constant_series_is_zero():
    line, sig, hist = core.macd([100.0] * 80)
    assert abs(line[-1]) < 1e-12 and abs(sig[-1]) < 1e-12 and abs(hist[-1]) < 1e-12


def test_atr_hand_computed():
    high = [10, 11, 12, 11, 13]
    low = [9, 9.5, 10, 9, 11]
    close = [9.5, 10.5, 11, 10, 12.5]
    # TR: [1, 1.5, 2, 2(=max(2, |11-11|, |9-11|)), 3(=max(2, |13-10|, |11-10|))]
    tr = core.true_range(high, low, close)
    np.testing.assert_allclose(tr, [1, 1.5, 2, 2, 3])
    out = core.atr(high, low, close, 3)
    assert np.isnan(out[:2]).all()
    assert out[2] == pytest.approx(1.5)
    assert out[3] == pytest.approx((1.5 * 2 + 2) / 3)
    assert out[4] == pytest.approx((out[3] * 2 + 3) / 3)


def test_bollinger_population_std():
    vals = [2, 4, 4, 4, 5, 5, 7, 9]
    up, mid, lo = core.bollinger(vals, 8, 2.0)
    assert mid[-1] == pytest.approx(5.0)
    assert up[-1] == pytest.approx(9.0)  # population std = 2
    assert lo[-1] == pytest.approx(1.0)


def test_breakout_window_excludes_current_bar():
    highs = [1, 2, 3, 10, 4]
    out = core.rolling_max_prior(highs, 3)
    assert np.isnan(out[:3]).all()
    assert out[3] == 3  # bar 3 itself (10) is excluded
    assert out[4] == 10
    lows = [5, 4, 3, 1, 2]
    assert core.rolling_min_prior(lows, 3)[3] == 3


def test_relative_volume_uses_prior_bars():
    vol = [10, 10, 10, 30]
    base, rvol = core.relative_volume(vol, 3)
    assert base[3] == pytest.approx(10)
    assert rvol[3] == pytest.approx(3.0)


def test_pivots_are_confirmed_only_after_right_side_bars():
    high = np.array([1, 2, 3, 9, 3, 2, 1, 2, 3, 4], dtype=float)
    low = high - 0.5
    pivots = [p for p in find_pivots(high, low, 3) if p.kind == "high"]
    assert len(pivots) == 1 and pivots[0].index == 3 and pivots[0].confirmed_at == 6
    s = structure_arrays(high, low, 3)
    assert np.isnan(s.last_swing_high[5])  # not yet known at bar 5
    assert s.last_swing_high[6] == 9


def test_structure_higher_highs_higher_lows():
    # Zig-zag with rising swing points.
    pattern = [10, 12, 14, 16, 14, 12, 13, 15, 17, 19, 17, 15, 16, 18, 20, 22, 20, 18, 19, 21, 23, 25, 23, 21, 22]
    high = np.array(pattern, dtype=float) + 0.5
    low = np.array(pattern, dtype=float) - 0.5
    s = structure_arrays(high, low, 2)
    assert s.structure[-1] == 1
    assert s.last_swing_high[-1] > s.prev_swing_high[-1]
    assert s.last_swing_low[-1] > s.prev_swing_low[-1]
