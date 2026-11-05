"""The Trade Setup checklist restates the engine's rules. These tests fail if the two ever disagree."""
from __future__ import annotations

import pytest

from app.strategy.diagnostics import explain_decision
from app.strategy.engine import evaluate_at
from app.strategy.features import compute_features
from app.strategy.params import StrategyParams
from tests.conftest import make_candles
from tests.test_zones_setups import P, _eval, _pb_params, breakout_bars, frame, pullback_bars


def entry(d, strategy, direction):
    return next(s for s in d["strategies"] if s["strategy"] == strategy and s["direction"] == direction)


def rule(s, key):
    return next(r for r in s["rules"] if r["key"] == key)


def blocking(s):
    return [r for r in s["rules"] if r["state"] == "fail" and not r["advisory"]]


def explain(ev, f, p, i=None):
    d = ev.to_dict()
    return explain_decision(f, len(f) - 1 if i is None else i, p, d["setups"], "BTCUSDT", ev.ready, ev.not_ready_reason)


def test_qualified_breakout_passes_every_rule():
    ev, f = _eval(frame(breakout_bars()))
    s = entry(explain(ev, f, P), "breakout", "LONG")
    assert s["status"] == "QUALIFIED"
    assert blocking(s) == [] and all(r["state"] == "pass" for r in s["rules"])
    assert rule(s, "net_rr")["threshold"] == f"min {P.qualification.min_net_rr:g}"


def test_rejected_breakout_names_the_plan_rule_that_failed():
    ev, f = _eval(frame(breakout_bars(opposing=True)))
    s = entry(explain(ev, f, P), "breakout", "LONG")
    assert s["status"] == "REJECTED"
    assert rule(s, "room")["state"] == "fail" and rule(s, "room")["value"].endswith("R")


def test_low_volume_shows_value_against_threshold():
    ev, f = _eval(frame(breakout_bars()), rvol=0.8)
    r = rule(entry(explain(ev, f, P), "breakout", "LONG"), "rvol")
    assert (r["state"], r["value"], r["threshold"]) == ("fail", "0.80x", f"min {P.breakout.min_rvol:g}x")


def test_plan_rules_wait_for_a_trigger_rather_than_inventing_values():
    ev, f = _eval(frame(breakout_bars(last=(102.0, 102.4, 101.8, 102.2))))
    s = entry(explain(ev, f, P), "breakout", "LONG")
    assert s["status"] != "QUALIFIED"
    for key in ("stop_distance", "room", "net_rr"):
        assert (rule(s, key)["state"], rule(s, key)["value"]) == ("na", "needs a trigger candle")


def test_pullback_rules_and_the_trigger_line():
    p = _pb_params(min_rr=0)
    ev, f = _eval(frame(pullback_bars()), params=p, rsi=50.0, ema_fast=101.3, ema_mid=101.0, ema_slow=100.5)
    d = explain(ev, f, p)
    s = entry(d, "pullback", "LONG")
    assert s["status"] == "QUALIFIED" and blocking(s) == []
    assert rule(s, "zone_retest")["state"] == "pass"
    assert d["trend_direction"] == "LONG"
    assert {t["strategy"] for t in d["triggers"]} == {"breakout", "pullback"}


def test_mixed_trend_explains_itself_and_reports_every_rule():
    p = P
    ev, f = _eval(frame(breakout_bars()), ema_fast=100.0, ema_mid=103.0, ema_slow=101.0)
    d = explain(ev, f, p)
    assert ev.setups == [] and d["trend_direction"] is None
    assert "trend is mixed" in d["triggers"][0]["text"]
    s = entry(d, "breakout", "LONG")
    assert s["status"] == "NO_SETUP" and rule(s, "ema_fast_mid")["state"] == "fail"
    assert rule(s, "rsi_band")["state"] in ("pass", "fail")  # still measured, not hidden


def test_disabled_strategy_is_reported_as_disabled():
    p = P.model_copy(deep=True)
    p.pullback.enabled = False
    ev, f = _eval(frame(breakout_bars()), params=p)
    assert entry(explain(ev, f, p), "pullback", "LONG")["status"] == "DISABLED"


@pytest.mark.parametrize("seed,drift", [(7, 0.0004), (11, -0.0004), (23, 0.0012), (31, -0.0012), (47, 0.0)])
@pytest.mark.parametrize("params", [
    StrategyParams(),
    StrategyParams.model_validate({"qualification": {"min_rr": 0.0, "min_net_rr": 0.0}, "breakout": {"min_rvol": 0.0}}),
    StrategyParams.model_validate({"breakout": {"level_source": "range"}, "qualification": {"min_rr": 0.0, "min_net_rr": 0.0}}),
], ids=["defaults", "relaxed", "range"])
def test_checklist_agrees_with_the_engine_on_every_bar(params, seed, drift):
    """For every bar and strategy: QUALIFIED exactly when no rule blocks, and the engine's verdict is reported unchanged."""
    f = compute_features(make_candles(600, seed=seed, drift=drift, vol=0.003), "15m", params)
    seen: dict[str, int] = {}
    for i in range(params.warmup_bars, len(f)):
        ev = evaluate_at(f, i, params, symbol="BTCUSDT")
        if not ev.ready:
            continue
        d = explain(ev, f, params, i)
        by_key = {(s["strategy"], s["direction"]): s for s in ev.to_dict()["setups"]}
        for s in d["strategies"]:
            engine = by_key.get((s["strategy"], s["direction"]))
            assert s["status"] == (engine["status"] if engine else "NO_SETUP")
            seen[s["status"]] = seen.get(s["status"], 0) + 1
            fails = blocking(s)
            if s["status"] == "QUALIFIED":
                assert fails == [], (i, s["strategy"], s["direction"], fails)
                assert all(r["state"] != "na" for r in s["rules"] if r["group"] == "Trigger")
            elif s["status"] == "REJECTED":
                assert fails, (i, s["strategy"], s["direction"], engine["reasons"])
            else:
                # NO_SETUP / WATCHLIST: something before the plan stage is unmet, so the checklist must not read as all clear.
                early = [r for r in s["rules"] if r["group"] in ("Trend", "Trigger") and r["state"] != "pass"]
                assert early, (i, s["strategy"], s["direction"], s["rules"])
    assert sum(seen.values()) > 1000


def test_explaining_a_decision_does_not_change_it():
    params = StrategyParams()
    f = compute_features(make_candles(500, seed=23, drift=0.0012, vol=0.003), "15m", params)
    i = len(f) - 1
    before = evaluate_at(f, i, params, symbol="BTCUSDT").to_dict()
    explain_decision(f, i, params, before["setups"], "BTCUSDT")
    assert evaluate_at(f, i, params, symbol="BTCUSDT").to_dict() == before
