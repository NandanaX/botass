"""Phase 9: stops, targets, sizing, entry gate, multi-leg replay, duplicates and isolation from live strategies."""

from __future__ import annotations

import math

import numpy as np
import pytest
from pydantic import ValidationError

from app.forward.settings import ValidationSettings
from app.forward.variants import VARIANT_RULES, variant_config, variant_version
from app.planning.context import build_context, major_swing_beyond, measured_move
from app.planning.gate import GateInput, GateParams, classify_candidate, correlated_count
from app.planning.sizing import PositionInput, TargetIn, decimals_of, plan_position
from app.planning.stops import MAX_STOP_ATR, STOP_METHODS, StopContext, plan_all, plan_stop
from app.planning.targets import Costs, build_targets, structural_targets
from app.research.p9_exits import find_retest_fill, simulate_legs
from app.research.p9_study import duplicates, legs_for, plan_rank, walk_forward_choice
from app.research.regime import regime_arrays
from app.research.robustness import Scenario, Series, simulate
from app.risk.sizing import InstrumentRules
from app.strategy.engine import evaluate_at
from app.strategy.features import compute_features
from app.strategy.params import StrategyParams
from tests.conftest import make_candles

RULES = InstrumentRules("BTCUSDT", tick_size=0.1, qty_step=0.001, min_qty=0.001, max_qty=100, max_market_qty=50, min_notional=5,
                        max_leverage=100, maintenance_margin_rate=0.005, base_coin="BTC")


def ctx(direction="LONG", **kw) -> StopContext:
    entry = kw.pop("entry", 100.0)
    atr = kw.pop("atr", 2.0)
    sign = -1 if direction == "LONG" else 1
    base = dict(original_stop=entry + sign * 1.5, swing_level=entry + sign * 3.0, zone_level=entry + sign * 1.0, noise_distance=2.5)
    base.update(kw)
    return StopContext(direction, entry, atr, **base)


# ------------------------------------------------------------------------------------ stops

@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_every_stop_method_is_on_the_losing_side(direction):
    for plan in plan_all(ctx(direction)).values():
        assert plan.valid, plan.justification
        assert (plan.price < 100) if direction == "LONG" else (plan.price > 100)
        assert plan.distance == pytest.approx(abs(100 - plan.price))
        assert plan.distance_pct == pytest.approx(plan.distance)  # entry is 100
        assert plan.distance_atr == pytest.approx(plan.distance / 2)


@pytest.mark.parametrize("direction,sign", [("LONG", -1), ("SHORT", 1)])
def test_atr_stops_use_the_exact_multiple(direction, sign):
    plans = plan_all(ctx(direction))
    assert plans["B_atr_2_5"].price == pytest.approx(100 + sign * 5.0)
    assert plans["C_atr_3_0"].price == pytest.approx(100 + sign * 6.0)
    assert plans["D_atr_3_5"].price == pytest.approx(100 + sign * 7.0)


@pytest.mark.parametrize("direction,sign", [("LONG", -1), ("SHORT", 1)])
def test_structural_stop_goes_beyond_the_swing_with_a_buffer(direction, sign):
    p = plan_stop("E_structural", ctx(direction))
    assert p.price == pytest.approx(100 + sign * (3.0 + 0.5 * 2))
    assert p.level == pytest.approx(100 + sign * 3.0)


def test_structural_stop_falls_back_to_the_zone_when_the_swing_is_on_the_wrong_side():
    p = plan_stop("E_structural", ctx("LONG", swing_level=105.0))
    assert p.level == pytest.approx(99.0)
    assert p.price == pytest.approx(99.0 - 1.0)


def test_structural_stop_is_refused_without_structure():
    p = plan_stop("E_structural", ctx("SHORT", swing_level=None, zone_level=None))
    assert not p.valid and p.price is None
    assert not plan_stop("F_hybrid", ctx("LONG", swing_level=None, zone_level=None)).valid


def test_far_structure_is_refused_rather_than_tightened():
    far = ctx("LONG", swing_level=100 - 2.0 * (MAX_STOP_ATR + 1))
    for m in ("E_structural", "F_hybrid"):
        p = plan_stop(m, far)
        assert not p.valid and "tightened" in p.justification


@pytest.mark.parametrize("noise,expected", [(1.0, 3.5), (5.0, 5.0), (None, 3.5)])
def test_hybrid_takes_the_widest_of_structure_noise_and_floor(noise, expected):
    p = plan_stop("F_hybrid", ctx("LONG", noise_distance=noise))
    assert p.distance == pytest.approx(expected)  # structure 3.0 + 0.25 x 2 = 3.5; floor 1.5 x 2 = 3.0


def test_hybrid_floor_applies_when_structure_is_close():
    p = plan_stop("F_hybrid", ctx("SHORT", swing_level=100.5, noise_distance=0.1))
    assert p.distance == pytest.approx(3.0)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_tick_rounding_never_tightens_a_stop(direction):
    plain = plan_stop("B_atr_2_5", ctx(direction, atr=2.03))
    rounded = plan_stop("B_atr_2_5", ctx(direction, atr=2.03, tick_size=0.1))
    assert rounded.distance >= plain.distance - 1e-9
    assert rounded.price == pytest.approx(round(rounded.price, 1))


def test_original_stop_must_be_on_the_losing_side():
    assert not plan_stop("A_original", ctx("LONG", original_stop=101.0)).valid
    assert plan_stop("A_original", ctx("LONG", original_stop=98.5)).price == 98.5


def test_unknown_method_is_an_error():
    with pytest.raises(ValueError):
        plan_stop("Z", ctx())


# ------------------------------------------------------------------------------------ targets

NO_COSTS = Costs(0, 0, 0, 0)


@pytest.mark.parametrize("direction,sign,stop", [("LONG", 1, 98.0), ("SHORT", -1, 102.0)])
def test_targets_are_r_multiples_with_all_distances(direction, sign, stop):
    t = build_targets(direction, 100.0, stop, 4.0, [1, 2, 3], None, NO_COSTS)
    assert [x["price"] for x in t] == pytest.approx([100 + sign * 2, 100 + sign * 4, 100 + sign * 6])
    assert [x["gross_r"] for x in t] == pytest.approx([1, 2, 3])
    assert [x["net_r"] for x in t] == pytest.approx([1, 2, 3])
    assert t[0]["distance_pct"] == pytest.approx(2.0)
    assert t[1]["distance_atr"] == pytest.approx(1.0)


@pytest.mark.parametrize("direction,stop", [("LONG", 98.0), ("SHORT", 102.0)])
def test_costs_reduce_net_r_by_hand_calculation(direction, stop):
    c = Costs(0.055, 0.055, 0.02, 0.05)
    t = build_targets(direction, 100.0, stop, 2.0, [2], None, c)[0]
    fill = 100.02 if direction == "LONG" else 99.98
    move = (104 - fill) if direction == "LONG" else (fill - 96)
    px = t["price"]
    assert t["net_r"] == pytest.approx((move - 0.00055 * fill - 0.00055 * px) / 2)
    assert t["net_r"] < t["gross_r"]


def test_target_feasibility_against_the_opposing_zone():
    t = build_targets("LONG", 100.0, 98.0, 2.0, [1, 2, 3], 105.0, NO_COSTS)
    assert [x["feasibility"] for x in t] == ["clear", "clear", "blocked"]
    near = build_targets("SHORT", 100.0, 102.0, 2.0, [2], 95.8, NO_COSTS)[0]
    assert near["feasibility"] == "near_opposing"
    assert build_targets("LONG", 100.0, 98.0, 2.0, [1], None, NO_COSTS)[0]["feasibility"] == "no_opposing_zone"


def test_short_target_below_zero_is_invalid_not_hidden():
    t = build_targets("SHORT", 10.0, 16.0, 2.0, [1, 2], None, NO_COSTS)
    assert t[0]["valid"] and not t[1]["valid"] and t[1]["price"] is None


def test_target_rounding_goes_toward_the_entry():
    long_t = build_targets("LONG", 100.0, 99.33, 1.0, [1], None, NO_COSTS, 0.1)[0]
    short_t = build_targets("SHORT", 100.0, 100.67, 1.0, [1], None, NO_COSTS, 0.1)[0]
    assert long_t["price"] == pytest.approx(100.6)
    assert short_t["price"] == pytest.approx(99.4)


def test_structural_levels_on_the_wrong_side_are_dropped_and_sorted_by_distance():
    out = structural_targets("LONG", 100.0, 98.0, 2.0, [("a", "far", 110.0), ("b", "wrong side", 90.0), ("c", "near", 103.0), ("d", "none", None)], NO_COSTS)
    assert [x["kind"] for x in out] == ["c", "a"]
    assert out[0]["gross_r"] == pytest.approx(1.5)


# ------------------------------------------------------------------------------------ sizing

def position(**kw) -> PositionInput:
    base = dict(direction="LONG", equity=10_000, risk_pct=0.5, entry=100.0, stop=98.0, leverage=5, entry_fee_pct=0, exit_fee_pct=0,
                entry_slippage_pct=0, stop_slippage_pct=0)
    base.update(kw)
    return PositionInput(**base)


@pytest.mark.parametrize("direction,stop", [("LONG", 98.0), ("SHORT", 102.0)])
def test_quantity_without_costs_is_budget_over_distance(direction, stop):
    z = plan_position(position(direction=direction, stop=stop))
    assert z["risk_budget"] == pytest.approx(50)
    assert z["quantity"] == pytest.approx(25)
    assert z["notional"] == pytest.approx(2500)
    assert z["initial_margin"] == pytest.approx(500)
    assert z["planned_loss"] == pytest.approx(50)
    assert z["feasible"]


@pytest.mark.parametrize("direction,stop,ef,sf", [("LONG", 98.0, 100.02, 97.951), ("SHORT", 102.0, 99.98, 102.051)])
def test_costs_shrink_the_quantity_by_hand_calculation(direction, stop, ef, sf):
    z = plan_position(position(direction=direction, stop=stop, entry_fee_pct=0.055, exit_fee_pct=0.055, entry_slippage_pct=0.02, stop_slippage_pct=0.05))
    unit = abs(ef - sf) + 0.00055 * ef + 0.00055 * sf
    assert z["loss_per_unit"] == pytest.approx(unit)
    assert z["quantity"] == pytest.approx(50 / unit)
    assert z["planned_loss"] == pytest.approx(50)  # no step rounding here, so the whole budget
    assert z["price_risk"] < z["planned_loss"]


def test_wider_stop_means_smaller_position_with_the_same_risk():
    near = plan_position(position(stop=98.0), RULES)
    wide = plan_position(position(stop=95.0), RULES)
    assert wide["quantity"] < near["quantity"]
    assert wide["planned_loss"] == pytest.approx(near["planned_loss"], rel=0.01)


def test_quantity_rounds_down_to_the_step_and_stays_within_budget():
    z = plan_position(position(entry=97123.4, stop=96111.1, equity=1234.5, risk_pct=0.5, entry_fee_pct=0.055, exit_fee_pct=0.055, leverage=20), RULES)
    assert z["quantity_raw"] > z["quantity"]
    assert round(z["quantity"] / RULES.qty_step, 6) == round(z["quantity"] / RULES.qty_step)
    assert z["planned_loss"] <= z["risk_budget"]
    assert z["quantity_decimals"] == 3 and z["price_decimals"] == 1


def test_prices_use_exchange_ticks_with_stop_away_and_target_toward_entry():
    z = plan_position(position(entry=100.04, stop=98.04, targets=[TargetIn(label="TP1", price=104.06)]), RULES)
    assert z["entry"] == pytest.approx(100.0)
    assert z["stop"] == pytest.approx(98.0)
    assert z["targets"][0]["price"] == pytest.approx(104.0)
    s = plan_position(position(direction="SHORT", entry=100.04, stop=102.04, targets=[TargetIn(label="TP1", price=96.04)]), RULES)
    assert s["stop"] == pytest.approx(102.1)
    assert s["targets"][0]["price"] == pytest.approx(96.1)


def test_decimals_of_handles_exchange_steps():
    assert [decimals_of(x) for x in (1, 0.1, 0.001, 1e-05, None)] == [0, 1, 3, 5, None]


def test_minimum_order_above_budget_is_refused_not_rounded_up():
    rules = InstrumentRules("X", 0.1, 1.0, 1.0, 100, None, 0, 100, 0.005)
    z = plan_position(position(equity=100, risk_pct=0.5), rules)  # budget 0.50, one unit loses 2
    assert not z["feasible"] and z["quantity"] == 0 and z["planned_loss"] == 0
    assert z["issues"][0]["code"] == "min_order_exceeds_budget"


def test_minimum_notional_is_checked_as_a_minimum_order():
    rules = InstrumentRules("X", 0.1, 0.01, 0.01, 100, None, 50, 100, 0.005)
    z = plan_position(position(equity=1000, risk_pct=0.1, stop=90.0), rules)  # 0.1 units, 10 USDT
    assert z["issues"][0]["code"] == "min_order_exceeds_budget"
    assert z["min_order_quantity"] == pytest.approx(0.5)


def test_maximum_quantity_caps_the_position_and_warns():
    z = plan_position(position(equity=1_000_000, risk_pct=1, stop=99.9, leverage=200), RULES)
    assert z["quantity"] == pytest.approx(50)
    assert any("capped" in w for w in z["warnings"])


def test_margin_check_never_raises_leverage():
    z = plan_position(position(leverage=1, stop=99.9, risk_pct=2))
    assert z["leverage"] == 1
    assert "insufficient_margin" in {i["code"] for i in z["issues"]}
    tight = plan_position(position(leverage=5, available_margin=100))
    assert "insufficient_margin" in {i["code"] for i in tight["issues"]}


def test_liquidation_before_stop_is_refused():
    z = plan_position(position(stop=80.0, leverage=10), RULES)
    assert "liquidation_before_stop" in {i["code"] for i in z["issues"]}
    assert not z["feasible"]


def test_exchange_leverage_limit():
    rules = InstrumentRules("X", 0.1, 0.001, 0.001, 100, None, 0, 10, 0.005)
    assert "leverage_above_exchange_max" in {i["code"] for i in plan_position(position(leverage=20, stop=99.0), rules)["issues"]}


def test_portfolio_risk_limit_counts_open_risk():
    ok = plan_position(position(open_risk_usdt=100, max_open_risk_pct=2.0))
    assert ok["feasible"]
    over = plan_position(position(open_risk_usdt=160, max_open_risk_pct=2.0))
    assert "portfolio_risk_limit" in {i["code"] for i in over["issues"]} and not over["feasible"]


@pytest.mark.parametrize("direction,stop,tp", [("LONG", 98.0, 104.0), ("SHORT", 102.0, 96.0)])
def test_profit_and_loss_figures(direction, stop, tp):
    z = plan_position(position(direction=direction, stop=stop, targets=[TargetIn(label="TP2", price=tp)]))
    t = z["targets"][0]
    assert t["gross_profit"] == pytest.approx(100)
    assert t["net_profit"] == pytest.approx(100)
    assert t["net_rr"] == pytest.approx(2)


def test_fees_are_charged_on_both_sides_of_a_target():
    z = plan_position(position(entry_fee_pct=0.1, exit_fee_pct=0.2, targets=[TargetIn(label="TP", price=104.0)]))
    q = z["quantity"]
    t = z["targets"][0]
    assert t["fees"] == pytest.approx(q * (0.001 * 100 + 0.002 * 104))
    assert t["net_profit"] == pytest.approx(t["gross_profit"] - t["fees"])


def test_gap_stress_shows_loss_beyond_the_plan():
    z = plan_position(position())
    assert all(g["loss"] > z["planned_loss"] for g in z["stop_gap_stress"])
    assert z["stop_gap_stress"][1]["loss"] > z["stop_gap_stress"][0]["loss"]
    assert "not a maximum" in z["note"]


@pytest.mark.parametrize("kw", [dict(stop=101.0), dict(direction="SHORT", stop=99.0), dict(stop=98.0, targets=[TargetIn(label="T", price=99.0)]),
                                dict(risk_pct=11), dict(risk_pct=0), dict(leverage=0.5)])
def test_invalid_inputs_are_rejected(kw):
    with pytest.raises(ValidationError):
        position(**kw)


# ------------------------------------------------------------------------------------ gate

def gate(**kw) -> GateInput:
    base = dict(direction="LONG", strategy="pullback", entry=100.0, atr=2.0, stop=98.0, opposing_boundary=106.0, htf_trends={"4h": "BULLISH"},
                ext_fast_atr=0.5, run=1, rsi=55.0, break_dist_atr=None)
    base.update(kw)
    return GateInput(**base)


def test_gate_qualified_when_everything_holds():
    r = classify_candidate(gate())
    assert r.status == "QUALIFIED" and not r.reasons and all(c["passed"] for c in r.checks)


@pytest.mark.parametrize("kw,status,code", [
    (dict(stop=None, stop_refused="no stop"), "REJECTED", "no_valid_stop"),
    (dict(production_reason_codes=["failed_rsi"]), "REJECTED", "production_rule_failed"),
    (dict(same_symbol_open=True), "REJECTED", "symbol_already_open"),
    (dict(correlated_open=1), "REJECTED", "correlated_exposure"),
    (dict(htf_trends={"4h": "BEARISH"}), "CONFLICTING_TIMEFRAMES", "htf_against"),
    (dict(opposing_boundary=102.5), "INSUFFICIENT_REWARD", "room_too_small"),
    (dict(ext_fast_atr=1.6), "OVEREXTENDED", "overextended"),
    (dict(run=5, rsi=72.0), "OVEREXTENDED", "overextended"),
    (dict(strategy="breakout", break_dist_atr=0.8), "WAIT_FOR_RETEST", "wait_for_retest"),
])
def test_gate_classes_have_objective_conditions(kw, status, code):
    r = classify_candidate(gate(**kw))
    assert r.status == status
    assert code in {x["code"] for x in r.reasons}


def test_gate_is_symmetric_for_shorts():
    r = classify_candidate(gate(direction="SHORT", stop=102.0, opposing_boundary=94.0, htf_trends={"4h": "BEARISH"}, rsi=45.0))
    assert r.status == "QUALIFIED"
    assert classify_candidate(gate(direction="SHORT", stop=102.0, opposing_boundary=94.0, htf_trends={"4h": "BULLISH"})).status == "CONFLICTING_TIMEFRAMES"
    assert classify_candidate(gate(direction="SHORT", stop=102.0, opposing_boundary=94.0, htf_trends={}, run=6, rsi=25.0)).status == "OVEREXTENDED"


def test_gate_order_is_fixed():
    r = classify_candidate(gate(htf_trends={"4h": "BEARISH"}, opposing_boundary=100.5, ext_fast_atr=3.0))
    assert r.status == "CONFLICTING_TIMEFRAMES"


def test_room_rejected_production_setup_is_insufficient_reward_not_rejected():
    r = classify_candidate(gate(production_status="REJECTED", production_reason_codes=["opposing_zone_too_close"], opposing_boundary=101.0))
    assert r.status == "INSUFFICIENT_REWARD"


def test_a_wider_stop_can_lose_its_reward():
    near = classify_candidate(gate(stop=98.0, opposing_boundary=104.0))
    wide = classify_candidate(gate(stop=95.0, opposing_boundary=104.0))
    assert near.status == "QUALIFIED" and wide.status == "INSUFFICIENT_REWARD"


def test_narrow_stop_can_fail_the_net_reward_after_costs():
    r = classify_candidate(gate(entry=100.0, stop=99.9, opposing_boundary=None), costs=Costs())
    assert r.status == "INSUFFICIENT_REWARD" and r.net_r_at_primary < 1.2


def test_correlated_count_needs_same_direction_and_threshold():
    pos = [{"symbol": "ETH", "direction": "LONG"}, {"symbol": "SOL", "direction": "SHORT"}, {"symbol": "XRP", "direction": "LONG"}]
    corr = {"ETH": 0.9, "XRP": 0.5, "SOL": 0.99}
    same, n, detail = correlated_count("BTC", "LONG", pos, lambda a, b: corr[a], 0.7)
    assert not same and n == 1 and {d["symbol"] for d in detail} == {"ETH", "XRP"}
    assert correlated_count("ETH", "LONG", pos, lambda a, b: 0.1, 0.7)[0] is True
    assert correlated_count("BTC", "LONG", [pos[0]], lambda a, b: math.nan, 0.7)[1] == 0


# ------------------------------------------------------------------------------------ replay

def series(rows: list[tuple[float, float, float, float]], bar_ns=60_000_000_000, funding=None) -> Series:
    o, h, lo, c = (np.array(x, float) for x in zip(*rows, strict=True))
    t = np.arange(len(o)) * bar_ns
    ft, fr = (np.array(funding[0], dtype=np.int64), np.array(funding[1], float)) if funding else (np.array([]), np.array([]))
    return Series(o, h, lo, c, t, bar_ns, ft, fr)


FLAT = (100, 100.5, 99.5, 100)
NOCOST = Scenario("n", fee_pct=0, entry_slip_pct=0, stop_slip_pct=0, funding=False)


@pytest.mark.parametrize("direction,stop,target", [("LONG", 98.0, 104.0), ("SHORT", 102.0, 96.0)])
@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("sc", [Scenario(), Scenario("d", stop_delay=True), Scenario("e", entry_delay=1), Scenario("f", fee_mult=2, slip_mult=3, spread_pct=0.05)])
def test_single_leg_replay_matches_the_existing_simulator(direction, stop, target, seed, sc):
    rng = np.random.default_rng(seed)
    n = 40
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.012, n)))
    o = np.r_[100.0, c[:-1]] * (1 + rng.normal(0, 0.002, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.004, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.004, n)))
    s = Series(o, h, lo, c, np.arange(n) * 60_000_000_000, 60_000_000_000, np.array([120_000_000_000]), np.array([0.0001]))
    a = simulate(s, 0, direction, stop, target, 30, sc, 1.0)
    b = simulate_legs(s, 0, direction, stop, [(target, 1.0)], 30, sc, 1.0)
    if a is None:
        assert b is None
        return
    assert b["net_r"] == pytest.approx(a["net_r"], abs=1e-12)
    assert b["gross_r"] == pytest.approx(a["gross_r"], abs=1e-12)
    assert (b["reason"], b["bars_held"], b["exit_close_ns"]) == (a["reason"], a["bars_held"], a["exit_close_ns"])


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_partial_exits_add_up(direction):
    sign = 1 if direction == "LONG" else -1
    s = series([FLAT, (100, 100.2, 99.8, 100), (100, 102.1, 99.9, 102), (102, 104.2, 101.9, 104), (104, 106.2, 103.9, 106)] if sign > 0 else
               [FLAT, (100, 100.2, 99.8, 100), (100, 100.1, 97.9, 98), (98, 98.1, 95.8, 96), (96, 96.1, 93.8, 94)])
    legs = [(100 + sign * 2, 0.3), (100 + sign * 4, 0.4), (100 + sign * 6, 0.3)]
    r = simulate_legs(s, 0, direction, 100 - sign * 2, legs, 20, NOCOST, 1.0)
    assert r["net_r"] == pytest.approx(0.3 * 1 + 0.4 * 2 + 0.3 * 3)
    assert r["targets_hit"] == 3 and not r["stopped"]


def test_stop_closes_only_what_is_left_after_a_partial():
    s = series([FLAT, (100, 100.2, 99.8, 100), (100, 102.1, 99.9, 102), (102, 102.1, 97.5, 98)])
    r = simulate_legs(s, 0, "LONG", 98.0, [(102.0, 0.5), (104.0, 0.5)], 20, NOCOST, 1.0)
    assert r["net_r"] == pytest.approx(0.5 * 1 + 0.5 * -1)
    assert r["stopped"] and r["targets_hit"] == 1


def test_stop_comes_first_when_a_bar_touches_both():
    s = series([FLAT, (100, 100.2, 99.8, 100), (100, 104.5, 97.5, 100)])
    r = simulate_legs(s, 0, "LONG", 98.0, [(102.0, 0.5), (104.0, 0.5)], 20, NOCOST, 1.0)
    assert r["reason"] == "AMBIGUOUS_STOP" and r["net_r"] == pytest.approx(-1)


def test_gap_through_the_stop_fills_at_the_open_and_loses_more_than_planned():
    s = series([FLAT, (100, 100.2, 99.8, 100), (95, 95.5, 94.5, 95)])
    r = simulate_legs(s, 0, "LONG", 98.0, [(104.0, 1.0)], 20, NOCOST, 1.0)
    assert r["reason"] == "STOP_GAP" and r["net_r"] == pytest.approx(-2.5)


def test_funding_is_charged_only_on_what_is_still_open():
    s = series([FLAT, (100, 100.2, 99.8, 100), (100, 102.1, 99.9, 102), (102, 102.2, 101.8, 102), (102, 104.1, 101.9, 104)],
               funding=([3 * 60_000_000_000 + 1], [0.01]))
    sc = Scenario("f", fee_pct=0, entry_slip_pct=0, stop_slip_pct=0, funding=True)
    r = simulate_legs(s, 0, "LONG", 98.0, [(102.0, 0.5), (104.0, 0.5)], 20, sc, 1.0)
    assert r["funding_r"] == pytest.approx(0.5 * 100 * 0.01 / 2)
    short = simulate_legs(series([FLAT, (100, 100.2, 99.8, 100), (100, 100.1, 95.9, 96)], funding=([2 * 60_000_000_000], [0.01])), 0, "SHORT", 102.0, [(96.0, 1.0)], 20, sc, 1.0)
    assert short["funding_r"] < 0


def test_trade_still_open_at_the_end_of_data_is_not_scored():
    assert simulate_legs(series([FLAT] * 3), 0, "LONG", 90.0, [(110.0, 1.0)], 20, NOCOST, 1.0) is None


def test_entry_opening_beyond_a_level_is_no_trade():
    s = series([FLAT, (97, 98, 96, 97), FLAT])
    assert simulate_legs(s, 0, "LONG", 98.0, [(104.0, 1.0)], 20, NOCOST, 1.0) is None


def test_mfe_and_mae_are_in_r_from_the_fill():
    s = series([FLAT, (100, 103, 99, 100), (100, 100.5, 97.5, 98)])
    r = simulate_legs(s, 0, "LONG", 98.0, [(104.0, 1.0)], 20, NOCOST, 1.0)
    assert r["mfe_r"] == pytest.approx(1.5)
    assert r["mae_r"] == pytest.approx(1.25)


def test_retest_fill_rules():
    fills = series([FLAT, (103, 103.5, 102.5, 103), (103, 103.2, 101.9, 102.5)])
    assert find_retest_fill(fills, 0, "LONG", 102.0, 100.0, 106.0, 5) == (2, 102.0)
    target_first = series([FLAT, (103, 106.5, 102.5, 106), (106, 106, 101.9, 102)])
    assert find_retest_fill(target_first, 0, "LONG", 102.0, 100.0, 106.0, 5) is None
    stop_first = series([FLAT, (103, 103.5, 99.5, 101)])
    assert find_retest_fill(stop_first, 0, "LONG", 102.0, 100.0, 106.0, 5) is None
    too_late = series([FLAT] + [(103, 103.5, 102.5, 103)] * 3 + [(103, 103, 101.5, 102)])
    assert find_retest_fill(too_late, 0, "LONG", 102.0, 100.0, 106.0, 2) is None
    short = series([FLAT, (97, 98.1, 96.5, 97)])
    assert find_retest_fill(short, 0, "SHORT", 98.0, 100.0, 94.0, 5) == (1, 98.0)


def test_retest_can_require_trading_through_the_limit():
    just_touch = series([FLAT, (103, 103.2, 102.0, 103)])
    assert find_retest_fill(just_touch, 0, "LONG", 102.0, 100.0, 106.0, 5) == (1, 102.0)
    assert find_retest_fill(just_touch, 0, "LONG", 102.0, 100.0, 106.0, 5, through=0.2) is None


def test_limit_entry_has_no_entry_slippage():
    s = series([FLAT, (100, 100.2, 99.8, 100), (100, 104.2, 99.9, 104), (104, 104.2, 103.9, 104)])
    sc = Scenario("c", fee_pct=0, entry_slip_pct=1.0, stop_slip_pct=0, funding=False)
    r = simulate_legs(s, 0, "LONG", 98.0, [(104.0, 1.0)], 20, sc, 1.0, limit_entry=(1, 100.0))
    assert r["entry_fill"] == 100.0 and r["net_r"] == pytest.approx(2)


# ------------------------------------------------------------------------------------ study helpers

def row(direction="LONG", boundary=None, entry=100.0):
    return {"direction": direction, "ctx": {"entry": entry, "atr": 2.0, "opposing_boundary": boundary, "original_stop": 98.0 if direction == "LONG" else 102.0,
                                            "direction": direction}}


def test_exit_legs_are_built_from_the_chosen_stop():
    assert legs_for(row(), 98.0, "full_3R") == [(106.0, 1.0)]
    assert legs_for(row("SHORT"), 102.0, "partial_50_50") == [(98.0, 0.5), (96.0, 0.5)]
    assert sum(f for _, f in legs_for(row(), 98.0, "partial_30_40_30")) == pytest.approx(1)


def test_structural_exit_stops_in_front_of_the_zone_and_skips_poor_reward():
    assert legs_for(row(boundary=105.0), 98.0, "structural") == [(pytest.approx(104.8), 1.0)]
    assert legs_for(row(boundary=101.0), 98.0, "structural") is None
    assert legs_for(row(), 98.0, "structural") == [(106.0, 1.0)]
    assert legs_for(row("SHORT", boundary=95.0), 102.0, "structural") == [(pytest.approx(95.2), 1.0)]


def test_targets_are_never_negative():
    assert legs_for(row("SHORT", entry=2.0), 3.5, "full_2R") is None


def trade(i, symbol="BTC", tf="1h", direction="LONG", net=1.0, hold=10):
    return {"id": i, "symbol": symbol, "timeframe": tf, "direction": direction, "net_r": net, "decision_ns": i * 3_600_000_000_000,
            "exit_close_ns": (i + hold) * 3_600_000_000_000}


class Corr:
    def corr(self, a, b, at):
        return 0.9


def test_duplicate_detection_flags_each_kind_separately():
    tr = [trade(1, "BTC", "1h"), trade(2, "BTC", "15m", net=-1), trade(3, "ETH", "1h"), trade(100, "SOL", "4h", direction="SHORT")]
    rows = [{"id": 1, "ctx": {"zone_lower": 100, "zone_upper": 101}}, {"id": 2, "ctx": {"zone_lower": 100.5, "zone_upper": 102}},
            {"id": 3, "ctx": {"zone_lower": None, "zone_upper": None}}, {"id": 100, "ctx": {"zone_lower": None, "zone_upper": None}}]
    d = duplicates(tr, rows, Corr())["groups"]
    assert d["same_symbol_other_tf"]["with"]["n"] == 1
    assert d["same_zone"]["with"]["n"] == 1
    assert d["correlated_coin"]["with"]["n"] == 1
    assert d["any_position_open"]["with"]["n"] == 2  # the late short is on its own


def test_duplicates_ignore_closed_positions_and_other_directions():
    tr = [trade(1, "BTC", "1h", hold=1), trade(5, "BTC", "15m"), trade(6, "BTC", "4h", direction="SHORT")]
    rows = [{"id": k, "ctx": {"zone_lower": None, "zone_upper": None}} for k in (1, 5, 6)]
    assert duplicates(tr, rows, None)["groups"]["any_position_open"]["with"]["n"] == 0


def test_ranking_never_reads_the_technical_score():
    def r(**kw):
        ctx_ = dict(direction="LONG", strategy="pullback", production_status="QUALIFIED", production_reason_codes=[], entry=100.0, atr=2.0, atr_pct=2.0,
                    original_stop=98.0, original_target=104.0, zone_level=99.0, zone_lower=99.0, zone_upper=99.5, zone_timeframe="1h", swing_level=97.0,
                    noise_distance=2.0, opposing_boundary=110.0, opposing_timeframe="1h", major_swing=None, measured_move=None, rvol=1.0, structure="0",
                    mtf_confirmed=True, htf_trends={}, features={"ext_fast_atr": 0.5, "run": 1, "rsi": 55.0}, regime=None)
        ctx_.update(kw)
        return {"id": 1, "symbol": "BTC", "ctx": ctx_, "score": 100}
    a, b = r(), r()
    b["score"] = 0
    assert plan_rank(a, "A_original") == plan_rank(b, "A_original")
    assert plan_rank(r(opposing_boundary=120.0), "A_original") < plan_rank(a, "A_original")
    assert plan_rank(a, "A_original") < plan_rank(r(features={"ext_fast_atr": 3.0, "run": 1, "rsi": 55.0}), "A_original")


def test_walk_forward_choice_uses_only_trades_closed_before_each_fold():
    # X is good early and terrible later; Y is the reverse. Looking ahead would always pick Y.
    x = [trade(i, net=(1.0 if i < 500 else -1.0), hold=1) for i in range(0, 1000)]
    y = [trade(i, net=(-1.0 if i < 500 else 1.0), hold=1) for i in range(0, 1000)]
    r = walk_forward_choice({"X": x, "Y": y}, "X", folds=4)
    assert r["folds"][0]["chosen"] == "X"  # at 25% only X's good early trades exist
    assert r["folds"][1]["chosen"] == "X"  # at 50% as well; the switch shows only in the third fold


# ------------------------------------------------------------------------------------ no look-ahead

def params_for_test() -> StrategyParams:
    return StrategyParams()


def trending(n=900, seed=3):
    return make_candles(n, 15, seed=seed, drift=-0.0006, vol=0.006, price=500.0)


def test_context_reads_nothing_after_the_decision_candle():
    df = trending()
    params = params_for_test()
    full = compute_features(df, "15m", params)
    ra_full = regime_arrays(full)
    checked = 0
    for i in range(300, 880):
        ev = evaluate_at(full, i, params, None, context=False)
        triggered = [s for s in ev.setups if s.entry_reference is not None and s.status in ("QUALIFIED", "REJECTED")]
        if not triggered:
            continue
        cut = compute_features(df.iloc[: i + 1].reset_index(drop=True), "15m", params)
        ev_cut = evaluate_at(cut, i, params, None, context=False)
        ra_cut = regime_arrays(cut)
        for a, b in zip(triggered, [s for s in ev_cut.setups if s.entry_reference is not None and s.status in ("QUALIFIED", "REJECTED")], strict=True):
            ca, cb = build_context(full, ra_full, i, a).to_dict(), build_context(cut, ra_cut, i, b).to_dict()
            assert ca.keys() == cb.keys()
            for k, v in ca.items():
                if isinstance(v, float):
                    assert cb[k] == pytest.approx(v, rel=1e-9, abs=1e-9), k
                elif not isinstance(v, dict):
                    assert cb[k] == v, k
            checked += 1
        if checked >= 25:
            break
    assert checked > 0


def test_a_future_price_spike_cannot_change_a_decision_time_plan():
    df = trending()
    params = params_for_test()
    base = compute_features(df, "15m", params)
    ev = next(e for e in (evaluate_at(base, i, params, None, context=False) for i in range(300, 880))
              if any(s.entry_reference is not None and s.status in ("QUALIFIED", "REJECTED") for s in e.setups))
    i = ev.index
    spiked = df.copy()
    spiked.loc[i + 1 :, ["open", "high", "low", "close"]] *= 3.0
    f2 = compute_features(spiked, "15m", params)
    ev2 = evaluate_at(f2, i, params, None, context=False)
    s1 = next(s for s in ev.setups if s.entry_reference is not None and s.status in ("QUALIFIED", "REJECTED"))
    s2 = next(s for s in ev2.setups if s.entry_reference is not None and s.status in ("QUALIFIED", "REJECTED"))
    p1 = plan_all(build_context(base, regime_arrays(base), i, s1).stop_context())
    p2 = plan_all(build_context(f2, regime_arrays(f2), i, s2).stop_context())
    for m in STOP_METHODS:
        assert p1[m].price == pytest.approx(p2[m].price) if p1[m].valid else not p2[m].valid


def test_major_swing_ignores_pivots_not_yet_confirmed():
    df = trending()
    f = compute_features(df, "15m", params_for_test())
    pv = f.pivots
    k = int(np.argmax(pv.confirmed_at > 400))
    i = int(pv.confirmed_at[k]) - 1  # one bar before that pivot is confirmed
    if pv.is_high[k]:
        got = major_swing_beyond(f, i, "LONG", 1.0, lookback=len(f))
        assert got is None or got == pytest.approx(max(pv.price[(pv.confirmed_at <= i) & pv.is_high]))


def test_measured_move_is_for_breakouts_only():
    assert measured_move("pullback", "LONG", 100.0, 90.0) is None
    assert measured_move("breakout", "LONG", 100.0, 90.0) == pytest.approx(110.0)
    assert measured_move("breakout", "SHORT", 100.0, 110.0) == pytest.approx(90.0)
    assert measured_move("breakout", "LONG", 100.0, 105.0) is None


# ------------------------------------------------------------------------------------ isolation

def test_locked_strategy_rules_and_paper_defaults_are_unchanged():
    assert VARIANT_RULES["A"] == {"name": "Baseline", "stop": "production", "require_mtf": False}
    assert VARIANT_RULES["B"] == {"name": "2.5 ATR stop", "stop": "atr", "atr_multiplier": 2.5, "target_r": 2.0, "require_mtf": False}
    assert VARIANT_RULES["C"] == {"name": "2.5 ATR + HTF", "stop": "atr", "atr_multiplier": 2.5, "target_r": 2.0, "require_mtf": True}
    assert ValidationSettings().risk.risk_per_trade_pct == 0.5
    v = variant_version(variant_config("A", "2.0.0+x", ValidationSettings()))
    assert v == variant_version(variant_config("A", "2.0.0+x", ValidationSettings()))


def test_planning_code_does_not_import_order_or_forward_services():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    for p in [*(root / "planning").glob("*.py"), root / "api" / "routes_planning.py", *(root / "research").glob("p9_*.py")]:
        text = p.read_text(encoding="utf-8")
        for banned in ("place_order", "create_order", "app.forward.service", "/v5/order"):
            assert banned not in text, f"{p.name} mentions {banned}"


def test_gate_defaults_do_not_loosen_production_minimums():
    p = GateParams()
    s = StrategyParams()
    assert p.min_reward_r >= s.qualification.min_rr
    assert p.min_net_rr >= s.qualification.min_net_rr
