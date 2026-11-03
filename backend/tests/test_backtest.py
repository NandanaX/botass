from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.execution import apply_slippage, simulate_exit
from app.backtest.metrics import compute_metrics, max_drawdown
from app.strategy.params import StrategyParams
from tests.conftest import make_candles

P = StrategyParams(mtf={"enabled": False})


def arr(*v):
    return np.array(v, dtype=float)


def test_ambiguous_bar_assumes_stop_first():
    # LONG, stop 95, target 110: bar touches both.
    r = simulate_exit("LONG", arr(100), arr(111), arr(94), arr(105), 0, 95, 110, None, False)
    assert r.reason == "AMBIGUOUS_STOP" and r.exit_price == 95


def test_target_and_stop_detection_long_short():
    o, h, lo, c = arr(100, 101, 104), arr(102, 103, 111), arr(99, 98, 103), arr(101, 102, 110)
    assert simulate_exit("LONG", o, h, lo, c, 0, 95, 110, None, False).reason == "TARGET"
    r = simulate_exit("SHORT", arr(100, 100), arr(101, 106), arr(99, 99), arr(100, 105), 0, 105, 90, None, False)
    assert r.reason == "STOP" and r.exit_price == 105 and r.exit_index == 1


def test_gap_through_stop_fills_at_open():
    r = simulate_exit("LONG", arr(100, 90), arr(101, 92), arr(99, 89), arr(100, 91), 0, 95, 110, None, False)
    assert r.reason == "STOP_GAP" and r.exit_price == 90


def test_time_exit_and_end_of_data():
    o = h = lo = c = arr(100, 100, 100, 100)
    assert simulate_exit("LONG", o, h + 1, lo - 1, c, 0, 95, 110, 2, False).reason == "TIME_EXIT"
    r = simulate_exit("LONG", o, h + 1, lo - 1, c, 0, 95, 110, None, False)
    assert r.reason == "END_OF_DATA" and r.exit_index == 3


def test_slippage_is_adverse():
    assert apply_slippage(100, "LONG", "entry", 0.001) == pytest.approx(100.1)
    assert apply_slippage(100, "LONG", "exit", 0.001) == pytest.approx(99.9)
    assert apply_slippage(100, "SHORT", "entry", 0.001) == pytest.approx(99.9)
    assert apply_slippage(100, "SHORT", "exit", 0.001) == pytest.approx(100.1)


def test_metrics_and_drawdown():
    assert max_drawdown([100, 120, 90, 130, 117]) == (30, pytest.approx(25.0))
    trades = [
        {"net_pnl": 200, "r_multiple": 2, "fees": 1, "funding": 0, "direction": "LONG", "exit_reason": "TARGET",
         "bars_held": 3},
        {"net_pnl": -100, "r_multiple": -1, "fees": 1, "funding": 0, "direction": "SHORT", "exit_reason": "STOP",
         "bars_held": 2},
    ]
    curve = [{"equity": 1000}, {"equity": 1200}, {"equity": 1100}]
    m = compute_metrics(trades, 1000, curve)
    assert m["win_rate_pct"] == 50
    assert m["profit_factor"] == 2
    assert m["expectancy"] == 50
    assert m["avg_r_multiple"] == 0.5
    assert m["net_return_pct"] == pytest.approx(10)
    assert m["max_drawdown"] == 100


def _cfg(df: pd.DataFrame, **kw) -> BacktestConfig:
    return BacktestConfig(symbol="BTCUSDT", timeframe="15m", start=df["open_time"].iloc[260].to_pydatetime(),
                          end=df["open_time"].iloc[-1].to_pydatetime(), strategy=P, include_funding=False, **kw)


def test_backtest_trade_accounting_next_open_fees_and_slippage():
    df = make_candles(3000, seed=5, drift=0.0005)
    cfg = _cfg(df, fee_rate_pct=0.05, slippage_pct=0.02)
    res = run_backtest(cfg, df)
    assert res.trades, "expected trades on trending synthetic data"
    times = df["open_time"]
    for t in res.trades:
        sig_idx = int(np.flatnonzero(times == pd.Timestamp(t["signal_time"]))[0])
        entry_idx = int(np.flatnonzero(times == pd.Timestamp(t["entry_time"]))[0])
        assert entry_idx == sig_idx + 1  # next candle open
        raw_open = df["open"].iloc[entry_idx]
        expected_entry = raw_open * (1.0002 if t["direction"] == "LONG" else 0.9998)
        assert t["entry_price"] == pytest.approx(expected_entry)
        expected_fees = 0.0005 * t["quantity"] * (t["entry_price"] + t["exit_price"])
        assert t["fees"] == pytest.approx(expected_fees)
        sign = 1 if t["direction"] == "LONG" else -1
        assert t["gross_pnl"] == pytest.approx(sign * t["quantity"] * (t["exit_price"] - t["entry_price"]))
        assert t["net_pnl"] == pytest.approx(t["gross_pnl"] - t["fees"] - t["funding"])
        if t["exit_reason"] in ("STOP", "AMBIGUOUS_STOP"):
            # Loss at the stop, including fees and slippage, is close to the 1% risk budget.
            assert t["r_multiple"] == pytest.approx(-1, abs=0.05)
    final = res.equity_curve[-1]["equity"]
    assert final == pytest.approx(cfg.initial_equity + sum(t["net_pnl"] for t in res.trades))
    # Positions never overlap.
    for a, b in zip(res.trades, res.trades[1:], strict=False):
        assert pd.Timestamp(b["entry_time"]) > pd.Timestamp(a["exit_time"])


def test_backtest_costs_reduce_returns():
    df = make_candles(3000, seed=5, drift=0.0005)
    free = run_backtest(_cfg(df, fee_rate_pct=0, slippage_pct=0), df)
    costly = run_backtest(_cfg(df, fee_rate_pct=0.1, slippage_pct=0.05), df)
    assert costly.metrics["net_return_pct"] < free.metrics["net_return_pct"]
    assert costly.metrics["total_fees"] > 0 and free.metrics["total_fees"] == 0


def test_backtest_has_no_future_leakage():
    """Trades that closed before candle k are identical when everything after k is replaced."""
    df = make_candles(3000, seed=5, drift=0.0005)
    k = 2000
    base = run_backtest(_cfg(df), df)
    mutated = df.copy()
    mutated.loc[k:, ["open", "high", "low", "close"]] = mutated.loc[k:, ["open", "high", "low", "close"]] * 0.5
    other = run_backtest(_cfg(mutated), mutated)
    cutoff = df["open_time"].iloc[k]
    before_a = [t for t in base.trades if pd.Timestamp(t["exit_time"]) < cutoff]
    before_b = [t for t in other.trades if pd.Timestamp(t["exit_time"]) < cutoff]
    assert before_a and before_a == before_b


def test_funding_costs_applied_by_direction():
    df = make_candles(3000, seed=5, drift=0.0005)
    times = pd.date_range(df["open_time"].iloc[0], df["open_time"].iloc[-1], freq="8h", tz=UTC)
    funding = [{"funding_time": t.to_pydatetime(), "rate": 0.001} for t in times]
    cfg = _cfg(df).model_copy(update={"include_funding": True})
    res = run_backtest(cfg, df, funding=funding)
    assert res.metrics["funding_included"]
    paid = [t for t in res.trades if t["funding"] != 0]
    assert paid
    for t in paid:
        assert (t["funding"] > 0) == (t["direction"] == "LONG")  # longs pay positive funding


def test_config_validation():
    with pytest.raises(ValueError):
        BacktestConfig(symbol="BTCUSDT", timeframe="15m", start=datetime(2026, 2, 1, tzinfo=UTC),
                       end=datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError):
        BacktestConfig(symbol="BTCUSDT", timeframe="7m", start=datetime(2026, 1, 1, tzinfo=UTC),
                       end=datetime(2026, 2, 1, tzinfo=UTC))
    with pytest.raises(ValueError):
        BacktestConfig(symbol="btc", timeframe="15m", start=datetime(2026, 1, 1, tzinfo=UTC),
                       end=datetime(2026, 2, 1, tzinfo=UTC))
