"""Phase 5 research: execution/outcome regressions, forensics reconstruction, regime and
entry-quality diagnostics, portfolio limits, experiment acceptance rules, ML leakage
controls and the decision engine. All data here is synthetic test data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.research.entry_quality import classify_entry
from app.research.experiments import Split, day_block_bootstrap, est_cost_r, verdict
from app.research.metrics import summarize, wilson
from app.research.ml import train_and_eval
from app.research.portfolio import PortfolioParams, independent_baseline, run_portfolio
from app.research.regime import regime_arrays
from app.research.simulate import ZERO_COSTS, CostModel, resolve_ambiguous_with_lower_tf, simulate_trade
from app.signals.evaluation import evaluate_signal_outcome, net_r
from app.strategy.engine import evaluate_at
from app.strategy.features import compute_features
from tests.conftest import make_candles, phase1_params

NS = 1_000_000_000
BAR = 900 * NS


def bars(rows):
    o, h, lo, c = (np.array([r[k] for r in rows], float) for k in range(4))
    t = np.arange(len(rows), dtype=np.int64) * BAR
    return o, h, lo, c, t


# ----------------------------------------------------------------------------- execution / outcome regressions

def test_entry_is_next_open_and_signal_candle_is_never_part_of_the_trade():
    # Signal candle (0) trades through the stop; the trade only starts at bar 1's open.
    o, h, lo, c, t = bars([(100, 100.5, 94, 100), (100, 101, 99.5, 100.8), (100.8, 110.5, 100.5, 110)])
    r = simulate_trade("LONG", o, h, lo, c, t, BAR, 0, stop=95, target=110, max_bars=10, costs=ZERO_COSTS)
    assert r.status == "FILLED" and r.entry_index == 1 and r.reason == "TARGET"
    assert r.gross_r == pytest.approx((110 - 100) / 5)


def test_entry_open_beyond_stop_is_invalidated():
    o, h, lo, c, t = bars([(100, 101, 99, 100), (94, 95, 93, 94)])
    assert simulate_trade("LONG", o, h, lo, c, t, BAR, 0, 95, 110, 10).status == "INVALIDATED"


def test_same_candle_stop_and_target_is_stop_first_and_lower_tf_can_resolve():
    o, h, lo, c, t = bars([(100, 100, 100, 100), (100, 111, 94, 100)])
    r = simulate_trade("LONG", o, h, lo, c, t, BAR, 0, 95, 110, 10, ZERO_COSTS)
    assert r.reason == "AMBIGUOUS_STOP" and r.gross_r == pytest.approx(-1.0)
    minute = pd.DataFrame({"open_time": pd.to_datetime([BAR + k * 60 * NS for k in range(3)], utc=True),
                           "open": [100, 105, 110], "high": [106, 111, 110], "low": [99, 104, 94],
                           "close": [105, 110, 95]})
    assert resolve_ambiguous_with_lower_tf("LONG", 95, 110, BAR, BAR, minute) == "TARGET"


def test_gap_through_stop_fills_at_open_and_stop_slippage_is_charged():
    o, h, lo, c, t = bars([(100, 100, 100, 100), (100, 101, 99, 100), (90, 91, 89, 90)])
    r = simulate_trade("LONG", o, h, lo, c, t, BAR, 0, 95, 110, 10, CostModel(0.05, 0.0, 0.1, False))
    assert r.reason == "STOP_GAP"
    assert r.gross_r == pytest.approx(-2.0)
    exit_fill = 90 * (1 - 0.001)
    expected = ((exit_fill - 100) - 0.0005 * (100 + exit_fill)) / 5
    assert r.net_r == pytest.approx(expected)
    assert r.net_r < r.gross_r


def test_funding_is_charged_to_longs_and_paid_to_shorts():
    o, h, lo, c, t = bars([(100, 100, 100, 100)] + [(100, 100.5, 99.5, 100)] * 5 + [(100, 111, 99.8, 110)])
    funding = pd.DataFrame({"t_ns": [3 * BAR], "rate": [0.001]})
    lng = simulate_trade("LONG", o, h, lo, c, t, BAR, 0, 95, 110, 20, CostModel(0, 0, 0, True), funding)
    sht = simulate_trade("SHORT", o, h, lo, c, t, BAR, 0, 110.5, 90, 20, CostModel(0, 0, 0, True), funding)
    assert lng.funding_r > 0 and lng.net_r < lng.gross_r
    assert sht.funding_r < 0


def test_live_evaluator_reports_net_r_below_gross_r_and_ignores_candles_before_entry():
    df = pd.DataFrame({
        "open_time": pd.to_datetime([datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=15 * k) for k in range(4)]),
        # bar 0 = signal candle (touches the stop), bar 1 = entry, bar 3 hits the stop
        "open": [100, 100, 99.5, 97], "high": [100.5, 100.4, 99.8, 97.2], "low": [94.0, 99.2, 97.1, 94.5],
        "close": [100, 99.6, 97.2, 95.0]})
    ev = evaluate_signal_outcome("LONG", df["open_time"].iloc[0], 95, 110, df, 20)
    assert ev["status"] == "STOP_HIT" and ev["exit_time"] == df["open_time"].iloc[3].isoformat()
    assert ev["r_multiple"] == pytest.approx(-1.0)
    assert ev["net_r_multiple"] < ev["r_multiple"] and ev["cost_r"] > 0
    assert net_r("SHORT", 100, 90, 105, False, 0, 0) == pytest.approx(2.0)


# ----------------------------------------------------------------------------- forensics

def _first_signal(df, p):
    f = compute_features(df, "15m", p)
    for i in range(p.warmup_bars, len(f) - 20):
        ev = evaluate_at(f, i, p, {}, context=False)
        if ev.setup is not None:
            return f, i, ev.setup
    raise AssertionError("no setup in synthetic data")


def test_forensic_reconstruction_matches_and_ignores_future_candles():
    from app.research.forensics import reconstruct

    p = phase1_params(mtf={"enabled": False})
    df = make_candles(1200, seed=3, drift=0.0008)
    f, i, st = _first_signal(df, p)
    sig = {"id": "x", "symbol": "TESTUSDT", "timeframe": "15m", "direction": st.direction, "strategy_type": st.strategy,
           "candle_open_time": pd.Timestamp(int(f.open_time[i]), tz="UTC").isoformat(),
           "entry_reference": st.entry_reference, "stop_loss": st.stop_loss, "take_profit": st.take_profit,
           "score": st.score}
    full = reconstruct(sig, df, {}, p, window=None)
    cut = reconstruct(sig, df.iloc[: i + 1], {}, p, window=None)
    assert full["matches"] and cut["matches"], (full["differences"], cut["differences"])
    sig["stop_loss"] = st.stop_loss * 1.01
    assert not reconstruct(sig, df, {}, p, window=None)["matches"]


def test_noise_distance_uses_only_closed_windows():
    from app.research.candidates import noise_distance

    p = phase1_params()
    df = make_candles(400, seed=9)
    f1 = compute_features(df, "15m", p)
    df2 = df.copy()
    df2.loc[300:, ["high", "low", "close"]] *= 1.2
    f2 = compute_features(df2, "15m", p)
    assert noise_distance(f1, 299, "LONG") == noise_distance(f2, 299, "LONG")


# ----------------------------------------------------------------------------- regime / entry quality

def test_regime_classifier_is_causal_and_labels_trends():
    p = phase1_params()
    up = make_candles(900, seed=4, drift=0.003, vol=0.002)
    f = compute_features(up, "15m", p)
    ra = regime_arrays(f)
    tail = list(ra.label[-200:])
    assert tail.count("STRONG_BULL") + tail.count("WEAK_BULL") + tail.count("EXHAUSTION") > 100
    for i in (650, 800):
        cut = regime_arrays(compute_features(up.iloc[: i + 1], "15m", p))
        assert cut.label[i] == ra.label[i]


def test_entry_quality_classes():
    base = {"ext_fast_atr": 0.8, "ext_mid_atr": 1.0, "run": 1, "rsi": 45, "room_r": 3.0, "swing_room_atr": 3.0,
            "momentum_fading": False}
    assert classify_entry(base, "SHORT")[0] == "CONFIRMED"
    assert classify_entry({**base, "ext_fast_atr": 0.1}, "SHORT")[0] == "EARLY"
    assert classify_entry({**base, "ext_fast_atr": 1.7}, "SHORT")[0] == "EXTENDED"
    assert classify_entry({**base, "swing_room_atr": 0.3}, "SHORT")[0] == "EXTENDED"
    assert classify_entry({**base, "ext_fast_atr": 2.8}, "SHORT")[0] == "EXHAUSTION_RISK"
    assert classify_entry({**base, "run": 6, "rsi": 25}, "SHORT")[0] == "EXHAUSTION_RISK"
    assert classify_entry({**base, "room_r": 0.5}, "SHORT")[0] == "INVALID"


# ----------------------------------------------------------------------------- portfolio

def _t(k, sym, d, t0, t1, r=-1.0, tf="15m"):
    return {"id": k, "symbol": sym, "timeframe": tf, "direction": d, "decision_ns": t0 * 3600 * NS,
            "exit_close_ns": t1 * 3600 * NS, "net_r": r, "entry_class": "CONFIRMED", "room_r": 2.0, "score": 70}


class FixedCorr:
    def corr(self, a, b, at):
        return 1.0 if a == b else (0.9 if {a, b} <= {"AAAUSDT", "BBBUSDT"} else 0.1)


def test_portfolio_rejects_repeats_correlated_and_respects_daily_loss():
    trades = [_t(1, "AAAUSDT", "SHORT", 1, 5), _t(2, "AAAUSDT", "SHORT", 2, 6), _t(3, "BBBUSDT", "SHORT", 2, 6),
              _t(4, "CCCUSDT", "SHORT", 2, 6)]
    acc, why = run_portfolio(trades, PortfolioParams(max_daily_loss_r=0, max_consecutive_losses=0), FixedCorr())
    assert [t["id"] for t in acc] == [1, 4]
    assert why["symbol_already_open"] == 1 and why["correlated_exposure"] == 1
    losses = [_t(k, f"S{k}USDT", "LONG", k, k + 0.5) for k in range(1, 6)]
    acc, why = run_portfolio(losses, PortfolioParams(max_daily_loss_r=3, max_consecutive_losses=0), None)
    assert len(acc) == 3 and why["daily_loss_limit"] == 2
    assert [t["id"] for t in independent_baseline(trades)] == [1, 3, 4]


# ----------------------------------------------------------------------------- experiment acceptance

def _rep(dev, val, test, n=40):
    mk = lambda a: {"n": n, "avg_r": a, "total_r": a * n, "max_drawdown_r": 5.0}  # noqa: E731
    return {"dev": mk(dev), "val": mk(val), "test": mk(test), "oos": mk((val + test) / 2)}


def test_verdict_rules():
    base = _rep(-0.2, -0.2, -0.2)
    good = {"p_improvement": 0.99}
    assert verdict(base, _rep(-0.1, -0.1, -0.1), good)[0] == "VALIDATED"
    # fixed rule that is worse on dev -> unstable; a dev-fitted rule is judged on val/test only
    assert verdict(base, _rep(-0.3, -0.1, -0.1), good)[0] == "NOT_VALIDATED"
    assert verdict(base, _rep(-0.3, -0.1, -0.1), good, fitted=True)[0] == "VALIDATED"
    assert verdict(base, _rep(-0.1, -0.1, -0.3), good)[0] == "NOT_VALIDATED"
    assert verdict(base, _rep(-0.1, -0.1, -0.1), {"p_improvement": 0.85})[0] == "PROMISING"
    assert verdict(base, _rep(-0.1, -0.1, -0.1, n=10), good)[0] == "INSUFFICIENT_DATA"


def test_day_block_bootstrap_and_stats():
    day = 86_400 * NS
    base = [{"decision_ns": d * day, "net_r": r} for d in range(60) for r in (-1.0, 0.5)]
    better = [{"decision_ns": d * day, "net_r": 0.5} for d in range(60)]
    assert day_block_bootstrap(base, better)["p_improvement"] == 1.0
    assert day_block_bootstrap(base, base)["p_improvement"] == 0.0
    s = summarize([{"net_r": x, "exit_close_ns": k} for k, x in enumerate([1, -1, -1, 2, -1])])
    assert s["n"] == 5 and s["total_r"] == 0 and s["max_drawdown_r"] == 2 and s["worst_losing_streak"] == 2
    assert wilson(0, 10)[0] == 0.0
    assert est_cost_r({"stop_atr": 1.0, "atr_pct": 0.5}) == pytest.approx(0.36)


def test_split_periods():
    sp = Split(0, 50, 75, 100)
    assert [sp.period(x) for x in (0, 49, 50, 74, 75, 100)] == ["dev", "dev", "val", "val", "test", "test"]


# ----------------------------------------------------------------------------- ML

def _ml_rows(n, seed=1, signal=True):
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(n):
        x = float(rng.normal())
        win = (x + rng.normal(0, 0.5) > 0) if signal else bool(rng.random() < 0.4)
        rows.append({"decision_ns": k * 3600 * NS, "exit_close_ns": (k + 10) * 3600 * NS, "net_r": 1.0 if win else -1.0,
                     "score": 60, "components": {}, "direction": "LONG", "strategy": "breakout", "timeframe": "15m",
                     "entry": {"ext_fast_atr": x, "rsi": 50}, "regime": {}, "atr_pct": 0.5})
    return rows


def test_ml_purges_overlapping_labels_and_reports_insufficient_data():
    rows = _ml_rows(900)
    res = train_and_eval(rows[:700], rows[700:], embargo_ns=24 * 3600 * NS)
    assert res["enabled"] and res["purged_rows"] >= 10  # labels overlapping the test window were dropped
    assert res["brier_model"] < res["brier_climatology"] and res["auc"] > 0.7
    small = train_and_eval(rows[:100], rows[100:200], embargo_ns=0)
    assert small["enabled"] is False and "insufficient" in small["reason"]
    noise = _ml_rows(900, seed=2, signal=False)
    r2 = train_and_eval(noise[:700], noise[700:], embargo_ns=0)
    assert abs(r2["auc"] - 0.5) < 0.12  # no invented skill on noise


# ----------------------------------------------------------------------------- decision engine

def test_decision_engine_rejects_correlated_exposure_and_explains_every_check():
    from app.research.decision import decide
    from app.research.settings import DecisionSettings

    p = phase1_params(mtf={"enabled": False})
    df = make_candles(1200, seed=3, drift=0.0008)
    f, i, _ = _first_signal(df, p)
    ev = evaluate_at(f, i, p, {}, context=True)
    common = dict(data_ok=True, realized_today_r=0.0, losing_streak=0, last_loss_ns=None, btc_features=None,
                  htf4h_features=None, study={"keys": [], "params": {}}, now_ns=int(f.close_time[i]))
    free = decide(ev, f, "AAAUSDT", DecisionSettings(), open_positions=[], corr_fn=lambda a, b: 0.1, **common)
    assert free["outcome"] == "QUALIFIED" and [c["step"] for c in free["checks"]] == list(range(1, 13))
    assert {c["rule_type"] for c in free["checks"]} <= {"risk_control", "validated_filter", "diagnostic"}
    d = ev.setup.direction
    busy = decide(ev, f, "AAAUSDT", DecisionSettings(), corr_fn=lambda a, b: 0.95,
                  open_positions=[{"symbol": "BBBUSDT", "timeframe": "15m", "direction": d}], **common)
    assert busy["outcome"] == "REJECTED" and "correlated" in busy["reason"]
    bad = decide(ev, f, "AAAUSDT", DecisionSettings(), open_positions=[], corr_fn=lambda a, b: 0.1,
                 **{**common, "data_ok": False})
    assert bad["outcome"] == "REJECTED"
    # A validated filter blocks only when the study lists it.
    gated = decide(ev, f, "AAAUSDT", DecisionSettings(), open_positions=[], corr_fn=lambda a, b: 0.1,
                   **{**common, "study": {"keys": ["H7"], "params": {"H7": {"params": {"min_score": 101},
                                                                            "title": "score"}}}})
    assert gated["outcome"] == "REJECTED" and "validated filter" in gated["reason"]


async def test_enforce_mode_blocks_telegram_but_shadow_does_not(db):
    from app.api.hub import EventHub
    from app.config import Settings
    from app.db.session import session_factory
    from app.settings_store import RuntimeSettings
    from app.signals.service import SignalService

    class Tg:
        sent = 0

        async def send_message(self, text):
            Tg.sent += 1
            from app.alerts.telegram import SendResult
            return SendResult(True, 1, None)

    svc = SignalService(session_factory, EventHub(), Settings(), Tg())  # type: ignore[arg-type]
    row = {"id": "s1", "score": 90, "plan": {"decision": {"mode": "enforce", "outcome": "REJECTED"}}}
    assert await svc._dispatch(row, RuntimeSettings(), too_old=False) == "BLOCKED_BY_DECISION"
    assert Tg.sent == 0


def test_research_api_without_study(client, tmp_path):
    c = client.app.state.container
    c.research.cache_dir = tmp_path
    c.research.data.cache_dir = tmp_path
    assert client.get("/api/research/study").status_code == 404
    r = client.get("/api/research/today?day=2026-01-01")
    assert r.status_code == 200 and r.json()["signals"] == []
    assert client.get("/api/research/signals/nope/forensics").status_code == 404
    assert client.get("/api/research/decision/filters").json()["mode"] == "shadow"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from tests.test_api import client as api_client  # reuse the offline app fixture

    yield from api_client.__wrapped__(tmp_path, monkeypatch)


async def test_scanner_decision_wiring_produces_a_decision(db):
    from app.api.hub import EventHub
    from app.db.session import session_factory
    from app.research.decision import decide  # noqa: F401  (import check)
    from app.settings_store import RuntimeSettings
    from app.workers.scanner import Scanner

    p = phase1_params(mtf={"enabled": False})
    df = make_candles(1200, seed=3, drift=0.0008)
    f, i, _ = _first_signal(df, p)
    ev = evaluate_at(f, i, p, {}, context=True)
    sc = Scanner(market=None, settings=None, signals=None, hub=EventHub(), session_factory=session_factory)  # type: ignore[arg-type]
    sc._features[("BTCUSDT", "1h")] = ("x", "y", compute_features(df, "1h", p))
    out = await sc._decide("BTCUSDT", f, ev, {"15m": f}, True, RuntimeSettings(), datetime(2026, 1, 20, tzinfo=UTC))
    assert out["outcome"] in ("QUALIFIED", "REJECTED", "NO_TRADE") and len(out["checks"]) >= 3, out
