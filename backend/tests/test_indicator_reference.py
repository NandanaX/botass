"""Indicators against values worked out by hand and against separate pandas textbook formulas, plus
warm-up, alignment and no-look-ahead checks. The `ta` library cross-check is in APPLICATION_QA_AUDIT.md."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analysis.indicators import adx
from app.indicators import core
from app.strategy.engine import evaluate_at
from app.strategy.features import compute_features
from app.strategy.params import StrategyParams
from tests.conftest import make_candles

CLOSE = np.array([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
                  46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57])


def test_sma_known_values_and_warm_up():
    v = core.sma([1, 2, 3, 4, 5, 6], 3)
    assert np.isnan(v[:2]).all()
    assert v[2:].tolist() == pytest.approx([2, 3, 4, 5])


def test_ema_known_values_seeded_with_the_sma():
    v = core.ema([2, 4, 6, 8, 12], 3)  # alpha 0.5; seed = mean(2,4,6) = 4
    assert np.isnan(v[:2]).all()
    assert v[2:].tolist() == pytest.approx([4, 6, 9])


def test_rsi_all_gains_all_losses_and_flat():
    assert core.rsi(np.arange(1.0, 40.0), 14)[-1] == pytest.approx(100)
    assert core.rsi(np.arange(40.0, 1.0, -1.0), 14)[-1] == pytest.approx(0)
    assert np.isnan(core.rsi(np.arange(1.0, 15.0), 14)).all()  # needs period + 1 prices


def test_rsi_matches_hand_written_wilder_formula():
    d = np.diff(CLOSE)
    gain, loss = np.clip(d, 0, None), np.clip(-d, 0, None)
    ag, al = gain[:14].mean(), loss[:14].mean()
    expected = [100 - 100 / (1 + ag / al)]
    for g, lo in zip(gain[14:], loss[14:], strict=True):
        ag, al = (ag * 13 + g) / 14, (al * 13 + lo) / 14
        expected.append(100 - 100 / (1 + ag / al))
    got = core.rsi(CLOSE, 14)
    assert got[14:].tolist() == pytest.approx(expected)
    assert got[14] == pytest.approx(70.46, abs=0.1)  # worked example from Wilder's method


def test_macd_is_the_difference_of_two_emas_and_histogram_its_gap():
    c = make_candles(300)["close"].to_numpy()
    m, s, h = core.macd(c, 12, 26, 9)
    assert np.nanmax(np.abs(m - (core.ema(c, 12) - core.ema(c, 26)))) == pytest.approx(0)
    assert np.nanmax(np.abs(h - (m - s))) == pytest.approx(0)


def test_true_range_and_atr_known_values():
    h, lo, c = np.array([10.0, 12, 11, 15]), np.array([9.0, 10, 9, 12]), np.array([9.5, 11, 10, 14])
    assert core.true_range(h, lo, c)[1:].tolist() == pytest.approx([2.5, 2, 5])  # uses the previous close across gaps
    # Wilder smoothing with period 2: seed = mean(first two true ranges), then (prev + tr) / 2
    tr = core.true_range(h, lo, c)
    seed = np.nanmean(tr[:2])
    a = core.atr(h, lo, c, 2)
    assert a[1] == pytest.approx(seed) and a[2] == pytest.approx((seed + 2) / 2) and a[3] == pytest.approx(((seed + 2) / 2 + 5) / 2)


def test_bollinger_uses_population_standard_deviation():
    c = make_candles(100)["close"]
    up, mid, dn = core.bollinger(c.to_numpy(), 20, 2.0)
    m, sd = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    assert np.nanmax(np.abs(mid - m.to_numpy())) == pytest.approx(0, abs=1e-9)
    assert np.nanmax(np.abs(up - (m + 2 * sd).to_numpy())) == pytest.approx(0, abs=1e-9)
    assert np.nanmax(np.abs(dn - (m - 2 * sd).to_numpy())) == pytest.approx(0, abs=1e-9)
    assert np.isnan(up[:19]).all()


def test_adx_is_bounded_and_high_only_in_a_strong_trend():
    up = make_candles(400, drift=0.004, vol=0.001)
    flat = make_candles(400, drift=0.0, vol=0.004, seed=2)
    strong = adx(*(up[k].to_numpy() for k in ("high", "low", "close")), 14)[0]
    weak = adx(*(flat[k].to_numpy() for k in ("high", "low", "close")), 14)[0]
    assert np.nanmin(strong) >= 0 and np.nanmax(strong) <= 100
    assert np.nanmean(strong[-100:]) > 40 > np.nanmean(weak[-100:])


def test_indicators_keep_their_length_and_alignment():
    df = make_candles(400)
    f = compute_features(df, "15m", StrategyParams())
    assert len(f.ema_fast) == len(f.rsi) == len(f.macd_hist) == len(f.atr) == len(df)
    assert f.close.tolist() == pytest.approx(df["close"].tolist())


@pytest.mark.parametrize("name", ["ema_fast", "ema_slow", "sma_long", "rsi", "macd_hist", "atr", "bb_upper", "volume_ma", "rvol", "prior_high", "prior_low"])
def test_no_indicator_changes_when_future_candles_arrive(name):
    p = StrategyParams()
    df = make_candles(700, seed=9)
    full = getattr(compute_features(df, "15m", p), name)
    part = getattr(compute_features(df.iloc[:500], "15m", p), name)
    assert np.allclose(part, full[:500], equal_nan=True)


def test_rolling_extremes_exclude_the_current_candle():
    v = np.array([1.0, 5, 2, 9, 3])
    assert core.rolling_max_prior(v, 2)[2:].tolist() == [5, 5, 9]
    assert core.rolling_min_prior(v, 2)[2:].tolist() == [1, 2, 2]


def test_decisions_do_not_change_when_future_candles_arrive():
    p = StrategyParams(mtf={"enabled": False})
    df = make_candles(900, seed=11, drift=0.0003, vol=0.006)
    full = compute_features(df, "15m", p)
    part = compute_features(df.iloc[:750], "15m", p)
    for i in range(p.warmup_bars, 750, 7):
        a, b = evaluate_at(full, i, p, {}, context=False), evaluate_at(part, i, p, {}, context=False)
        assert [(s.direction, s.status, s.entry_reference, s.stop_loss, s.take_profit) for s in a.setups] == \
               [(s.direction, s.status, s.entry_reference, s.stop_loss, s.take_profit) for s in b.setups]


def test_candle_gaps_and_unclosed_candles_are_handled():
    from datetime import UTC, datetime, timedelta

    from app.market.validation import filter_closed, find_gaps, validate_candles
    t = datetime(2026, 1, 1, tzinfo=UTC)
    good = {"open_time": t, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1}
    assert filter_closed([good], "15m", t + timedelta(minutes=14)) == []  # still forming
    assert filter_closed([good], "15m", t + timedelta(minutes=15)) == [good]
    assert find_gaps([t, t + timedelta(minutes=15), t + timedelta(minutes=60)], "15m") == [(t + timedelta(minutes=30), t + timedelta(minutes=45))]
    dup = {**good, "close": 1.6}
    assert validate_candles([good, dup], "15m")[0]["close"] == 1.6  # last value for a duplicate open time wins, once
    assert validate_candles([{**good, "high": 0.1}], "15m") == []
    assert validate_candles([{**good, "open_time": t + timedelta(minutes=7)}], "15m") == []  # not on the timeframe grid
    assert validate_candles([{**good, "open_time": t.replace(tzinfo=None)}], "15m") == []  # no time zone


def test_warm_up_is_reported_rather_than_guessed():
    p = StrategyParams()
    f = compute_features(make_candles(150), "15m", p)
    ev = evaluate_at(f, 149, p, {})
    assert not ev.ready and "insufficient history" in ev.not_ready_reason and ev.setups == []


def test_sri_lanka_conversion_is_exact():
    t = pd.Timestamp("2026-11-04T07:30:00Z").tz_convert("Asia/Colombo")
    assert t.strftime("%Y-%m-%d %H:%M %z") == "2026-11-04 13:00 +0530"
