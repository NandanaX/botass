"""Stops, targets, position sizing with exchange rules, partial exits and their use in
backtests, alerts and settings."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from app.alerts.telegram import format_signal_message
from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.execution import simulate_partial_exits
from app.indicators.zones import Zone
from app.risk.calculator import RiskInput, calculate_risk
from app.risk.sizing import InstrumentRules, SizingConfig, floor_qty, round_price, size_position
from app.risk.stops import compute_stop
from app.risk.targets import compute_targets, find_opposing_zone
from app.settings_store import RiskDefaults, RuntimeSettings
from app.strategy.params import StrategyParams
from tests.conftest import make_candles

BTC = InstrumentRules(symbol="BTCUSDT", tick_size=0.1, qty_step=0.001, min_qty=0.001, max_qty=1500,
                      max_market_qty=150, min_notional=5, max_leverage=100, maintenance_margin_rate=0.005,
                      base_coin="BTC")


# ---------------------------------------------------------------- stops

def test_atr_stop_long_and_short():
    s = compute_stop("LONG", 100, 2, "atr", None, atr_multiplier=1.5)
    assert s.price == pytest.approx(97) and s.distance == pytest.approx(3) and s.method == "atr"
    s = compute_stop("SHORT", 100, 2, "atr", None, atr_multiplier=1.5)
    assert s.price == pytest.approx(103)


def test_structure_and_hybrid_stops():
    s = compute_stop("LONG", 100, 2, "structure", 96.0, tick_size=0.1)
    assert s.price == pytest.approx(95.9) and s.structural_level == 96.0
    h = compute_stop("LONG", 100, 2, "hybrid", 96.0, hybrid_buffer_atr=0.25)
    assert h.price == pytest.approx(95.5)
    hs = compute_stop("SHORT", 100, 2, "hybrid", 104.0, hybrid_buffer_atr=0.25)
    assert hs.price == pytest.approx(104.5) and hs.price > 100


def test_structural_stop_is_widened_never_tightened():
    s = compute_stop("LONG", 100, 2, "structure", 99.8, min_stop_atr=0.5)
    assert s.widened and s.price == pytest.approx(99.0)  # widened to 0.5 ATR
    wide = compute_stop("LONG", 100, 2, "structure", 90.0, min_stop_atr=0.5)
    assert not wide.widened and wide.price == pytest.approx(90.0)  # far stop is kept, not moved closer


def test_structure_on_the_wrong_side_falls_back_to_atr():
    s = compute_stop("LONG", 100, 2, "hybrid", 101.0, atr_multiplier=1.5)
    assert s.method == "atr" and s.price == pytest.approx(97) and "ATR method was used" in s.reasoning


def test_stop_rounding_moves_away_from_entry():
    assert compute_stop("LONG", 100, 1, "atr", None, atr_multiplier=1.234, tick_size=0.1).price == pytest.approx(98.7)
    assert compute_stop("SHORT", 100, 1, "atr", None, atr_multiplier=1.234, tick_size=0.1).price == pytest.approx(101.3)


# ---------------------------------------------------------------- targets

def _zone(lower, upper, ztype, tf="4h", status="ACTIVE"):
    return Zone(id="z", timeframe=tf, lower=lower, upper=upper, zone_type=ztype, origin="swing_highs", status=status,
                tests=3, rejections=2, crossings=0, first_test_index=0, last_test_index=1, formed_index=1,
                last_break_index=None, strength=70, price_inside=False)


def test_targets_are_r_multiples_long_and_short():
    t = compute_targets("LONG", 100, 3, [1, 2, 3], [30, 40, 30])
    assert [x.price for x in t] == pytest.approx([103, 106, 109]) and not any(x.blocked for x in t)
    t = compute_targets("SHORT", 100, 3, [1, 2, 3], [30, 40, 30])
    assert [x.price for x in t] == pytest.approx([97, 94, 91])


def test_targets_flag_opposing_zone_and_round_toward_entry():
    opp = find_opposing_zone("LONG", 100, 3, [_zone(104.5, 105.5, "RESISTANCE"), _zone(120, 121, "RESISTANCE"),
                                              _zone(102, 103, "RESISTANCE", status="INVALIDATED")])
    assert opp is not None and opp.boundary == 104.5 and opp.room_r == pytest.approx(1.5)
    t = compute_targets("LONG", 100, 3, [1, 2, 3], [30, 40, 30], opp)
    assert [x.blocked for x in t] == [False, True, True] and "4h resistance" in t[1].note
    r = compute_targets("LONG", 100, 1.234, [1], [100], tick_size=0.1)
    assert r[0].price == pytest.approx(101.2)  # 101.234 rounded down (toward entry)
    assert find_opposing_zone("SHORT", 100, 3, [_zone(104, 105, "RESISTANCE")]) is None


def test_target_params_validation():
    with pytest.raises(ValidationError):
        StrategyParams(targets={"allocation_pct": [30, 40, 20]})  # sums to 90
    with pytest.raises(ValidationError):
        StrategyParams(targets={"r_multiples": [2, 1, 3]})
    with pytest.raises(ValidationError):
        StrategyParams(targets={"r_multiples": [1, 2], "allocation_pct": [50, 50], "primary_index": 2})


# ---------------------------------------------------------------- sizing

def test_sizing_rounds_down_to_qty_step_and_stays_within_budget():
    cfg = SizingConfig(account_balance=10_000, risk_pct=1, fee_rate_pct=0.055, slippage_pct=0.02, leverage=5)
    r = size_position("LONG", 68_000, 67_300, cfg, BTC)
    entry_fill, stop_fill = 68_000 * 1.0002, 67_300 * 0.9998
    per_unit = (entry_fill - stop_fill) + 0.00055 * (entry_fill + stop_fill)
    assert r.loss_per_unit == pytest.approx(per_unit)
    assert r.quantity == pytest.approx(floor_qty(100 / per_unit, 0.001))
    assert round(r.quantity / 0.001, 9) == int(round(r.quantity / 0.001))
    assert r.estimated_loss <= r.risk_budget + 1e-9 and r.feasible
    assert r.notional == pytest.approx(r.quantity * 68_000)
    assert r.initial_margin == pytest.approx(r.notional / 5)
    assert r.price_risk == pytest.approx(r.quantity * 700)
    assert r.liquidation_price == pytest.approx(68_000 * (1 - 1 / 5 + 0.005))


def test_sizing_exchange_minimums_margin_and_liquidation():
    tiny = SizingConfig(account_balance=50, risk_pct=0.5, leverage=5)
    r = size_position("LONG", 68_000, 60_000, tiny, BTC)
    assert not r.feasible and "below_min_qty" in [i["code"] for i in r.issues]
    lev = SizingConfig(account_balance=10_000, risk_pct=1, leverage=50)
    r = size_position("LONG", 100, 95, lev)  # 5% stop, liquidation at ~2.5% at 50x
    assert "liquidation_before_stop" in [i["code"] for i in r.issues]
    assert r.max_safe_leverage == pytest.approx(1 / (0.05 + 0.005))
    margin = SizingConfig(account_balance=1_000, risk_pct=2, leverage=1)
    r = size_position("SHORT", 100, 100.2, margin)  # tight stop needs ~10x notional
    assert "insufficient_margin" in [i["code"] for i in r.issues]


def test_sizing_caps_at_max_market_qty_and_reports_targets():
    big = SizingConfig(account_balance=1e8, risk_pct=1, leverage=10)
    r = size_position("LONG", 68_000, 67_900, big, BTC)
    assert r.quantity == pytest.approx(150) and r.warnings
    t = size_position("SHORT", 100, 103, SizingConfig(fee_rate_pct=0, slippage_pct=0),
                      targets=[("TP1", 97, 1, 30), ("TP2", 94, 2, 40), ("TP3", 91, 3, 30)])
    assert [x.net_rr for x in t.targets] == pytest.approx([1, 2, 3])
    assert t.weighted_net_profit == pytest.approx(sum(x.net_profit * a / 100 for x, a in zip(t.targets, [30, 40, 30], strict=True)))


def test_price_rounding_helpers():
    assert round_price(68000.04, 0.1) == pytest.approx(68000.0)
    assert round_price(68000.04, 0.1, "up") == pytest.approx(68000.1)
    assert floor_qty(0.3, 0.1) == pytest.approx(0.3)  # float noise does not drop a step
    assert floor_qty(0.0149, 0.001) == pytest.approx(0.014)


def test_risk_calculator_with_rules_and_targets():
    out = calculate_risk(RiskInput(account_balance=10_000, risk_pct=1, direction="LONG", entry_price=68_000,
                                   stop_loss=67_300, take_profits=[68_700, 69_400, 70_100], slippage_pct=0.02,
                                   leverage=5), BTC)
    assert out.feasible and out.leverage == 5
    assert out.estimated_loss_at_stop_net <= out.max_loss + 1e-9
    assert [t["label"] for t in out.targets] == ["TP1", "TP2", "TP3"]
    assert out.targets[1]["net_rr"] < 2  # costs reduce the realised R:R
    off_tick = calculate_risk(RiskInput(account_balance=10_000, direction="LONG", entry_price=68_000.05,
                                        stop_loss=67_300), BTC)
    assert any("tick size" in w for w in off_tick.warnings)


# ---------------------------------------------------------------- partial exits

def _bars(rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def test_partial_exits_scale_out_and_conservative_ambiguity():
    targets = [(103, 0.3), (106, 0.4), (109, 0.3)]
    o, h, lo, c = _bars([(100, 103.5, 99.5, 103), (103, 106.5, 102, 106), (106, 109.5, 105, 109)])
    r = simulate_partial_exits("LONG", o, h, lo, c, 0, 100, 97, targets, None, False)
    assert [f.reason for f in r.fills] == ["TP1", "TP2", "TP3"] and r.targets_hit == 3
    assert sum(f.fraction for f in r.fills) == pytest.approx(1)
    # TP1 fills, then a candle touches both the stop and TP2: the remainder exits at the stop.
    o, h, lo, c = _bars([(100, 103.5, 99.5, 103), (103, 106.5, 96.5, 100)])
    r = simulate_partial_exits("LONG", o, h, lo, c, 0, 100, 97, targets, None, False)
    assert [(f.reason, f.fraction) for f in r.fills] == [("TP1", 0.3), ("AMBIGUOUS_STOP", pytest.approx(0.7))]


def test_partial_exits_breakeven_and_gaps():
    targets = [(103, 0.5), (106, 0.5)]
    o, h, lo, c = _bars([(100, 103.5, 99.5, 103), (103, 103.2, 99.0, 99.5)])
    r = simulate_partial_exits("LONG", o, h, lo, c, 0, 100, 97, targets, None, False, move_stop_to_breakeven=True)
    assert r.fills[-1].reason == "BREAKEVEN_STOP" and r.fills[-1].price == 100
    o, h, lo, c = _bars([(100, 101, 99, 100.5), (107, 108, 106.5, 107.5)])
    r = simulate_partial_exits("LONG", o, h, lo, c, 0, 100, 97, targets, None, False)
    assert [f.reason for f in r.fills] == ["TP1_GAP", "TP2_GAP"] and all(f.price == 107 for f in r.fills)
    o, h, lo, c = _bars([(100, 101, 99, 100.5), (95, 96, 94, 95)])
    r = simulate_partial_exits("SHORT", o, h, lo, c, 0, 100, 103, [(97, 1.0)], None, False)
    assert r.fills[0].reason == "TP1_GAP" and r.fills[0].price == 95


# ---------------------------------------------------------------- backtest integration

def _cfg(**strategy):
    params = StrategyParams.model_validate({"mtf": {"enabled": False}, "zones": {"htf_timeframes": []}, **strategy})
    return BacktestConfig(symbol="BTCUSDT", timeframe="15m", start=datetime(2026, 1, 4, tzinfo=UTC),
                          end=datetime(2026, 3, 1, tzinfo=UTC), strategy=params, fee_rate_pct=0.055,
                          slippage_pct=0.02)


@pytest.fixture(scope="module")
def trend_df():
    return make_candles(6000, seed=3, drift=0.0003, vol=0.004)


def test_backtest_uses_phase2_plans_rules_and_partial_exits(trend_df):
    single = run_backtest(_cfg(), trend_df, rules=BTC.__class__(**{**BTC.to_dict(), "tick_size": 0.0001,
                                                                     "qty_step": 0.01, "min_qty": 0.01,
                                                                     "min_notional": 1, "max_market_qty": None}))
    assert single.trades, "expected trades on a trending synthetic series"
    for t in single.trades:
        assert t["strategy"] in ("breakout", "pullback")
        assert len(t["targets"]) == 3 and t["stop_method"] in ("atr", "structure", "hybrid")
        assert round(t["quantity"] / 0.01, 6) == round(t["quantity"] / 0.01)  # qty step respected
        assert sum(f["fraction"] for f in t["fills"]) == pytest.approx(1)
    assert single.metrics["rejected_setup_reasons"]
    partial = run_backtest(_cfg(targets={"partial_exits": True}), trend_df)
    assert any(len(t["fills"]) > 1 for t in partial.trades)
    for t in partial.trades:
        assert sum(f["fraction"] for f in t["fills"]) == pytest.approx(1)
        # Entry fee once on the full size + exit fees on each fill.
        fees = 0.00055 * t["quantity"] * (t["entry_price"] + sum(f["price"] * f["fraction"] for f in t["fills"]))
        assert t["fees"] == pytest.approx(fees)
    assert any("Partial exits" in a for a in partial.assumptions)


def test_backtest_phase2_has_no_future_leakage(trend_df):
    cfg = _cfg()
    base = run_backtest(cfg, trend_df)
    cut = pd.Timestamp("2026-02-10", tz="UTC")
    altered = trend_df.copy()
    future = altered["open_time"] > cut
    altered.loc[future, ["open", "high", "low", "close"]] *= 1.3
    alt = run_backtest(cfg, altered)
    closed_before = [t for t in base.trades if pd.Timestamp(t["exit_time"]) + pd.Timedelta(minutes=15) <= cut]
    assert closed_before
    assert alt.trades[: len(closed_before)] == closed_before


# ---------------------------------------------------------------- alerts

def test_telegram_message_uses_stored_plan_values():
    sizing = size_position("LONG", 68_000, 67_300, SizingConfig(), BTC,
                           [("TP1", 68_700, 1, 30), ("TP2", 69_400, 2, 40), ("TP3", 70_100, 3, 30)])
    signal = {
        "symbol": "BTCUSDT", "direction": "LONG", "timeframe": "15m", "entry_reference": 68_000.0,
        "stop_loss": 67_300.0, "take_profit": 69_400.0, "risk_reward": 2.0, "score": 77,
        "setup_status": "QUALIFIED", "explanation": "Bullish EMA alignment & confirmed breakout",
        "candle_close_time": "2026-11-03 10:15",
        "plan": {
            "strategy": "breakout", "primary_target_index": 1, "net_risk_reward": 1.9,
            "stop": {"price": 67_300.0, "method": "hybrid"},
            "targets": [{"label": "TP1", "price": 68_700.0, "r_multiple": 1.0, "blocked": False},
                        {"label": "TP2", "price": 69_400.0, "r_multiple": 2.0, "blocked": False},
                        {"label": "TP3", "price": 70_100.0, "r_multiple": 3.0, "blocked": True}],
            "zones": [{"zone_type": "SUPPORT", "lower": 67_400.0, "upper": 67_600.0, "status": "ACTIVE",
                       "timeframe": "15m", "tests": 3}],
            "htf_zones": {"4h": [{"zone_type": "RESISTANCE", "lower": 69_500.0, "upper": 69_800.0,
                                  "status": "ACTIVE", "timeframe": "4h", "tests": 4}]},
            "sizing": sizing.to_dict(),
        },
    }
    msg = format_signal_message(signal)
    assert "CRYPTO TRADING SETUP" in msg and "<b>LONG BTCUSDT</b> · 15m · Breakout" in msg
    assert "Entry: $68,000.00" in msg and "Stop Loss: $67,300.00 (hybrid)" in msg
    assert "Support: $67,400.00-$67,600.00 (15m, 3 tests)" in msg
    assert "Resistance: $69,500.00-$69,800.00 (4h, 4 tests)" in msg
    assert "TP1: $68,700.00 (1R)" in msg and "TP2: $69,400.00 (2R) · primary" in msg
    assert "TP3: $70,100.00 (3R) · ⚠️ beyond opposing zone" in msg
    assert f"Leverage: {sizing.leverage:g}x (isolated)" in msg
    assert f"Est. Liquidation: ${sizing.liquidation_price:,.2f}" in msg
    assert "Risk/Reward: 1:2 at TP2" in msg
    assert f"Suggested Quantity: {sizing.quantity:g} BTC" in msg
    assert f"Position Notional: ${sizing.notional:,.2f}" in msg
    assert "Status: <b>QUALIFIED</b>" in msg and "&amp;" in msg
    assert "no order has been placed" in msg


# ---------------------------------------------------------------- settings

def test_risk_settings_validation_prevents_unsafe_combinations():
    with pytest.raises(ValidationError):
        RiskDefaults(leverage=30, max_leverage=20)
    with pytest.raises(ValidationError):
        RiskDefaults(risk_per_trade_pct=6, leverage=15, max_leverage=20)
    with pytest.raises(ValidationError):
        RiskDefaults(risk_per_trade_pct=25)
    s = RuntimeSettings()
    assert s.risk.sizing().risk_pct == 1.0
    assert {"1h", "4h"} <= set(s.required_timeframes)  # zone timeframes are fetched by the scanner
