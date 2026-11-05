"""The Trade Setup card and API routes, built from real engine output on synthetic candles."""

from __future__ import annotations

import json

import pytest

from app.planning.card import UNVALIDATED_NOTE, build_card, evidence_for, gate_support
from app.planning.gate import GATE_STATUSES
from app.planning.stops import STOP_METHODS
from app.planning.targets import Costs
from app.risk.sizing import InstrumentRules
from app.strategy.engine import evaluate_at
from app.strategy.features import compute_features
from app.strategy.params import StrategyParams
from tests.conftest import make_candles
from tests.test_api import client  # noqa: F401  (pytest fixture)

RULES = InstrumentRules("SYNUSDT", 0.01, 0.001, 0.001, 1000, 100, 5, 100, 0.005, "SYN")


def triggered(direction: str):
    """First candle where the engine triggers a setup in ``direction``, from several synthetic paths."""
    params = StrategyParams()
    for seed in range(40):
        drift = 0.0006 if direction == "LONG" else -0.0006
        df = make_candles(1000, 15, seed=seed, drift=drift, vol=0.006, price=500.0)
        f = compute_features(df, "15m", params)
        for i in range(params.warmup_bars, len(f)):
            ev = evaluate_at(f, i, params, None, context=True, symbol="SYNUSDT")
            if any(s.direction == direction and s.status in ("QUALIFIED", "REJECTED") and s.entry_reference is not None for s in ev.setups):
                return f, i, ev
    pytest.skip(f"no {direction} setup on the synthetic paths")


@pytest.fixture(params=["LONG", "SHORT"])
def card_input(request):
    return (request.param, *triggered(request.param))


def card(f, i, ev, method="A_original", positions=None, corr=None, study=None, tp=None):
    return build_card("SYNUSDT", "15m", f, i, ev, RULES, method, tp or [1.0, 2.0, 3.0], Costs(), positions or [], corr, 0.7, study)


def test_card_has_every_requested_field_for_both_directions(card_input):
    direction, f, i, ev = card_input
    s = next(x for x in card(f, i, ev)["setups"] if x["direction"] == direction)
    assert s["gate"]["status"] in GATE_STATUSES
    assert {v["method"] for v in s["stop_variants"]} == set(STOP_METHODS)
    assert s["stop"]["method"] == "A_original"
    long = direction == "LONG"
    for v in s["stop_variants"]:
        if v["valid"]:
            assert (v["price"] < s["entry_reference"]) == long
    for t in s["targets"]:
        if t["valid"]:
            assert (t["price"] > s["entry_reference"]) == long
            assert {"distance", "distance_pct", "distance_atr", "gross_r", "net_r", "feasibility"} <= t.keys()
            assert t["net_r"] < t["gross_r"]
    r = s["reasoning"]
    for key in ("technical_evidence", "entry_quality", "stop_justification", "take_profit_feasibility", "net_risk_reward", "correlation_exposure", "rejection_reasons"):
        assert r[key] is not None
    assert s["evidence"]["status"] == "Unvalidated"
    assert s["evidence"]["note"] == UNVALIDATED_NOTE


def test_card_prices_use_exchange_precision(card_input):
    _, f, i, ev = card_input
    c = card(f, i, ev, "B_atr_2_5")
    assert c["price_decimals"] == 2
    for s in c["setups"]:
        for x in [s["stop"]["price"], *[t["price"] for t in s["targets"] if t["valid"]]]:
            assert x == pytest.approx(round(x, 2), abs=1e-9)


def test_card_never_reports_a_score_or_probability(card_input):
    _, f, i, ev = card_input
    text = json.dumps(card(f, i, ev)).lower()
    assert '"score"' not in text and "probability" not in text and "confidence" not in text


def test_changing_the_stop_method_changes_only_the_plan(card_input):
    direction, f, i, ev = card_input
    a = next(x for x in card(f, i, ev)["setups"] if x["direction"] == direction)
    b = next(x for x in card(f, i, ev, "D_atr_3_5")["setups"] if x["direction"] == direction)
    assert a["stop"]["price"] != b["stop"]["price"]
    assert a["stop_variants"] == b["stop_variants"]
    assert a["entry_reference"] == b["entry_reference"]
    assert a["production_status"] == b["production_status"]


def test_open_positions_feed_the_gate(card_input):
    direction, f, i, ev = card_input
    same = card(f, i, ev, positions=[{"symbol": "SYNUSDT", "direction": direction, "timeframe": "1h"}])
    s = next(x for x in same["setups"] if x["direction"] == direction)
    assert s["gate"]["status"] == "REJECTED"
    assert "symbol_already_open" in {r["code"] for r in s["gate"]["reasons"]}
    other = card(f, i, ev, positions=[{"symbol": "ETHUSDT", "direction": direction, "timeframe": "1h"}], corr=lambda a, b: 0.95)
    s2 = next(x for x in other["setups"] if x["direction"] == direction)
    assert "correlated_exposure" in {r["code"] for r in s2["gate"]["reasons"]}
    assert "ETHUSDT" in s2["reasoning"]["correlation_exposure"]


def test_watchlist_only_candles_get_no_plan():
    params = StrategyParams()
    f = compute_features(make_candles(600, 15, seed=2), "15m", params)
    ev = evaluate_at(f, 300, params, None, context=True)
    ev.setups = [s for s in ev.setups if s.status == "WATCHLIST"]
    assert card(f, 300, ev)["setups"] == []


def test_evidence_stays_unvalidated_even_with_good_history():
    study = {"stops": {"A_original": {"n": 5000, "summary": {"avg_r": 5.0, "win_rate_pct": 90}, "stop_out_pct": 1}}}
    e = evidence_for("A_original", study)
    assert e["status"] == "Unvalidated"
    assert e["historical"]["avg_net_r"] == 5.0
    assert "not forward" in e["historical"]["label"]
    assert evidence_for("A_original", None)["historical"] is None


@pytest.mark.parametrize("verdict,phrase", [("IMPROVES_IN_ALL_PERIODS", "every period"), ("PROMISING", "promising"), ("NOT_VALIDATED", "did not reliably")])
def test_gate_support_reports_what_history_shows(verdict, phrase):
    study = {"gate": {"A_original": {"filters": {"reject_OVEREXTENDED": {"n": 100, "vs_baseline": {"research_verdict": verdict}}}}}}
    assert phrase in gate_support("OVEREXTENDED", "A_original", study)


def test_gate_support_says_so_when_untested():
    assert "hasn't been tested" in gate_support("OVEREXTENDED", "A_original", None)
    assert gate_support("QUALIFIED", "A_original", None) is None


# ------------------------------------------------------------------------------------ API

def test_trade_plan_route_uses_the_paper_default_risk(client):  # noqa: F811
    r = client.get("/api/planning/BTCUSDT/trade-plan", params={"timeframe": "1h"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["defaults"]["risk_pct"] == 0.5
    assert d["defaults"]["equity"] == 10_000
    assert d["defaults"]["max_open_risk_pct"] == 2.0
    assert set(d["stop_methods"]) == set(STOP_METHODS)
    assert set(d["gate_definitions"]) == set(GATE_STATUSES)
    assert "doesn't change live signals" in d["label"]


@pytest.mark.parametrize("params", [{"stop_method": "nope"}, {"tp": [2, 1]}, {"tp": [0]}, {"tp": [25]}, {"tp": [1, 1]}])
def test_trade_plan_route_rejects_bad_input(client, params):  # noqa: F811
    assert client.get("/api/planning/BTCUSDT/trade-plan", params=params).status_code == 422


def test_position_route_applies_exchange_rules(client):  # noqa: F811
    body = {"symbol": "BTCUSDT", "direction": "LONG", "equity": 10_000, "risk_pct": 0.5, "entry": 100.004, "stop": 98.006, "leverage": 5,
            "targets": [{"label": "TP2", "price": 104.009}]}
    r = client.post("/api/planning/position", json=body)
    assert r.status_code == 200, r.text
    z = r.json()
    assert z["feasible"] and z["quantity_decimals"] == 3 and z["price_decimals"] == 2
    assert z["entry"] == pytest.approx(100.0)
    assert z["stop"] == pytest.approx(98.0)  # rounded away from the entry
    assert z["targets"][0]["price"] == pytest.approx(104.0)  # rounded toward the entry
    assert z["planned_loss"] <= z["risk_budget"]


def test_position_route_rejects_a_stop_on_the_wrong_side(client):  # noqa: F811
    body = {"direction": "SHORT", "equity": 10_000, "risk_pct": 0.5, "entry": 100, "stop": 99, "leverage": 5}
    assert client.post("/api/planning/position", json=body).status_code == 422


def test_planning_routes_create_no_records(client):  # noqa: F811
    client.get("/api/planning/BTCUSDT/trade-plan")
    client.post("/api/planning/position", json={"direction": "LONG", "equity": 10_000, "risk_pct": 0.5, "entry": 100, "stop": 98, "leverage": 5})
    body = client.get("/api/signals").json()
    assert not (body["items"] if isinstance(body, dict) else body)
