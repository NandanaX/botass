from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.risk.levels import compute_levels, realized_risk_reward
from app.strategy.engine import classify_trend, evaluate_at, htf_states_at
from app.strategy.features import compute_features
from app.strategy.params import StrategyParams
from tests.conftest import make_candles, phase1_params

P = phase1_params(mtf={"enabled": False})


def forced(candles: pd.DataFrame, direction: str, params: StrategyParams = P, **overrides):
    """Compute real features, then force the rule inputs at the last bar."""
    f = compute_features(candles, "15m", params)
    i = len(f) - 1
    long = direction == "LONG"
    f.ema_fast[i], f.ema_mid[i], f.ema_slow[i] = (103, 102, 101) if long else (97, 98, 99)
    f.rsi[i] = 60 if long else 40
    f.macd_hist[i] = 0.5 if long else -0.5
    f.close[i] = 100.0
    f.open[i] = 99.0 if long else 101.0
    f.high[i], f.low[i] = 100.5, 98.5
    f.prior_high[i] = 99.5 if long else 120
    f.prior_low[i] = 80 if long else 100.5
    f.atr[i] = 2.0
    for name, value in overrides.items():
        getattr(f, name)[i] = value
    return f, i


def test_long_signal_generated_with_correct_levels(candles):
    f, i = forced(candles, "LONG")
    ev = evaluate_at(f, i, P)
    assert ev.setup is not None and ev.setup.direction == "LONG"
    assert all(c.passed for c in ev.setup.conditions) and len(ev.setup.conditions) == 5
    # SL = 100 - 1.5 * 2 = 97 ; TP = 100 + 2 * 3 = 106
    assert ev.setup.entry_reference == 100.0
    assert ev.setup.stop_loss == pytest.approx(97.0)
    assert ev.setup.take_profit == pytest.approx(106.0)
    assert ev.setup.risk_reward == 2.0
    assert 0 <= ev.setup.score <= 100
    assert "Bullish EMA alignment" in ev.setup.explanation


def test_short_signal_generated_with_correct_levels(candles):
    f, i = forced(candles, "SHORT")
    ev = evaluate_at(f, i, P)
    assert ev.setup is not None and ev.setup.direction == "SHORT"
    assert ev.setup.stop_loss == pytest.approx(103.0)
    assert ev.setup.take_profit == pytest.approx(94.0)


@pytest.mark.parametrize("override", [
    {"ema_fast": 101.5},          # EMA20 not above EMA50
    {"ema_mid": 100.5},           # EMA50 not above EMA200
    {"rsi": 71.0},                # RSI above band
    {"rsi": 49.9},                # RSI below band
    {"macd_hist": -0.01},         # MACD histogram negative
    {"prior_high": 100.0},        # close not strictly above prior high
])
def test_long_requires_every_condition(candles, override):
    f, i = forced(candles, "LONG", **override)
    ev = evaluate_at(f, i, P)
    assert ev.setup is None
    assert ev.long_passed == 4


@pytest.mark.parametrize("override", [
    {"ema_fast": 98.5}, {"ema_mid": 99.5}, {"rsi": 29.0}, {"rsi": 50.1}, {"macd_hist": 0.01}, {"prior_low": 100.0},
])
def test_short_requires_every_condition(candles, override):
    f, i = forced(candles, "SHORT", **override)
    ev = evaluate_at(f, i, P)
    assert ev.setup is None
    assert ev.short_passed == 4


def test_configurable_atr_multiplier_and_rr(candles):
    params = phase1_params(mtf={"enabled": False}, stops={"atr_multiplier": 2.0},
                           targets={"r_multiples": [1.0, 3.0, 4.0], "primary_index": 1})
    f, i = forced(candles, "LONG", params)
    ev = evaluate_at(f, i, params)
    assert ev.setup.stop_loss == pytest.approx(96.0)
    assert ev.setup.take_profit == pytest.approx(112.0)


def test_not_ready_without_enough_history():
    small = make_candles(150)
    f = compute_features(small, "15m", P)
    ev = evaluate_at(f, len(f) - 1, P)
    assert not ev.ready and ev.setup is None and "insufficient history" in ev.not_ready_reason


def test_levels_math_long_and_short():
    lv = compute_levels("LONG", 68_500, 466.67, 1.5, 2.0)
    assert lv.stop_loss == pytest.approx(68_500 - 700, abs=0.01)
    assert lv.take_profit == pytest.approx(68_500 + 1400, abs=0.01)
    sv = compute_levels("SHORT", 68_500, 466.67, 1.5, 2.0)
    assert sv.stop_loss == pytest.approx(69_200, abs=0.01)
    assert sv.take_profit == pytest.approx(67_100, abs=0.01)
    assert realized_risk_reward("LONG", lv.entry, lv.stop_loss, lv.take_profit) == pytest.approx(2.0)
    assert realized_risk_reward("SHORT", sv.entry, sv.stop_loss, sv.take_profit) == pytest.approx(2.0)
    with pytest.raises(ValueError):
        compute_levels("LONG", 100, 0, 1.5, 2)
    with pytest.raises(ValueError):
        compute_levels("LONG", 1.0, 1.0, 1.5, 2)  # stop would be negative


def test_no_look_ahead_truncated_history_gives_identical_decisions():
    """Evaluating bar i with the full dataset must equal evaluating it when bar i is the last
    candle that exists, which proves no future candle influences the decision."""
    df = make_candles(700, seed=11, drift=0.0006)
    full = compute_features(df, "15m", P)
    for i in [260, 333, 450, 512, 699]:
        trunc = compute_features(df.iloc[: i + 1].reset_index(drop=True), "15m", P)
        a = evaluate_at(full, i, P).to_dict()
        b = evaluate_at(trunc, i, P).to_dict()
        assert a == b


def test_future_candle_changes_do_not_affect_past_evaluation():
    df = make_candles(600, seed=3)
    base = evaluate_at(compute_features(df, "15m", P), 400, P).to_dict()
    mutated = df.copy()
    mutated.loc[401:, ["open", "high", "low", "close"]] *= 3.0
    after = evaluate_at(compute_features(mutated, "15m", P), 400, P).to_dict()
    assert base == after


def test_strategy_finds_signals_on_trending_synthetic_data():
    df = make_candles(3000, seed=5, drift=0.0005, vol=0.004)
    f = compute_features(df, "15m", P)
    setups = [evaluate_at(f, i, P).setup for i in range(len(f))]
    setups = [s for s in setups if s is not None]
    assert setups, "expected at least one signal on a trending random walk"
    for s in setups:
        assert all(c.passed for c in s.conditions)
        if s.direction == "LONG":
            assert s.stop_loss < s.entry_reference < s.take_profit
        else:
            assert s.take_profit < s.entry_reference < s.stop_loss


def test_htf_alignment_uses_only_closed_higher_timeframe_candles():
    params = StrategyParams(mtf={"enabled": True, "timeframes": ["1h"]})
    h1 = make_candles(400, timeframe_minutes=60, seed=2)
    f1h = compute_features(h1, "1h", params)
    # Decision at 15m candle closing at 10:45 must use the 1h candle that closed at 10:00
    # (open 09:00), not the 10:00-11:00 candle that is still forming.
    t = pd.Timestamp("2026-01-10 10:45", tz="UTC").value
    states = htf_states_at({"1h": f1h}, t, "15m", params)
    assert states[0].candle_close_time == "2026-01-10T10:00:00Z"
    t_exact = pd.Timestamp("2026-01-10 11:00", tz="UTC").value
    assert htf_states_at({"1h": f1h}, t_exact, "15m", params)[0].candle_close_time == "2026-01-10T11:00:00Z"
    # Higher timeframes only: a 1h signal ignores a configured 1h "higher" timeframe.
    assert htf_states_at({"1h": f1h}, t, "1h", params) == []


def test_mtf_quality_and_filter_modes(candles):
    h4 = make_candles(400, timeframe_minutes=240, seed=9, drift=-0.003)  # strongly bearish HTF
    for mode, expect_signal in (("quality", True), ("filter", False)):
        params = phase1_params(mtf={"enabled": True, "timeframes": ["4h"], "mode": mode})
        f, i = forced(candles, "LONG", params)
        start = f.time_at(i) - pd.Timedelta(hours=4 * 399)
        h4 = h4.assign(open_time=[start + pd.Timedelta(hours=4 * k) for k in range(len(h4))])
        f4 = compute_features(h4, "4h", params)
        assert classify_trend(f4.ema_fast[-2], f4.ema_mid[-2], f4.ema_slow[-2]) == "BEARISH"
        ev = evaluate_at(f, i, params, {"4h": f4})
        if expect_signal:
            assert ev.setup is not None and ev.setup.mtf_confirmed is False
            htf = next(c for c in ev.setup.score_components if c.key == "higher_timeframe")
            assert htf.evaluated and htf.points == 0
        else:
            assert ev.setup is None and "filter mode" in ev.rejection


def test_score_excludes_unevaluated_components(candles):
    f, i = forced(candles, "LONG")
    ev = evaluate_at(f, i, P)
    comps = {c.key: c for c in ev.setup.score_components}
    assert not comps["higher_timeframe"].evaluated
    total_max = sum(c.max_points for c in comps.values() if c.evaluated)
    total = sum(c.points for c in comps.values() if c.evaluated)
    assert ev.setup.score == round(100 * total / total_max)
    assert all(c.reason for c in comps.values())


def test_params_validation():
    with pytest.raises(ValueError):
        StrategyParams(ema_fast=60, ema_mid=50)
    with pytest.raises(ValueError):
        StrategyParams(rsi_long_min=70, rsi_long_max=50)
    assert StrategyParams().version != StrategyParams(stops={"atr_multiplier": 2}).version
    assert np.isfinite(StrategyParams().warmup_bars)
