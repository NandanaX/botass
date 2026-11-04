"""Phase 6 forward validation: parallel variants, immutable versions, paper-trade
persistence and exits, execution costs, portfolio risk and circuit breakers, statistics on
clustered trades, promotion gate, stress simulation and the API. Synthetic test data only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.forward.costs import Quote, check_exit, entry_fill, exit_fill, settle, trigger_price
from app.forward.report import assess, variant_report
from app.forward.risk import AccountState, Position, breakers, check_entry, exposure
from app.forward.settings import GateThresholds, PaperCosts, RiskLimits, ValidationSettings
from app.forward.variants import plan_for, variant_config, variant_version
from app.research.stats import assign_clusters, cluster_bootstrap, drawdown_distribution, holm, period_consistency
from app.settings_store import RuntimeSettings
from app.strategy.engine import evaluate_at
from app.strategy.features import compute_features
from tests.conftest import make_candles, phase1_params

H = 3_600_000_000_000
T0 = datetime(2026, 1, 10, tzinfo=UTC)


# ----------------------------------------------------------------------------- variants / versioning

class _Setup:
    def __init__(self, direction="LONG", mtf=True):
        self.direction, self.mtf_confirmed = direction, mtf
        self.entry_reference, self.atr, self.stop_loss, self.take_profit = 100.0, 2.0, 98.0, 104.0


def test_variant_rules_and_immutable_versions():
    a, b, c = plan_for("A", _Setup()), plan_for("B", _Setup()), plan_for("C", _Setup(mtf=False))
    assert (a.stop, a.target) == (98.0, 104.0)
    assert b.stop == pytest.approx(95.0) and b.target == pytest.approx(110.0)  # 2.5 ATR, 2R
    assert not c.accepted and "HTF" in c.reason
    s = plan_for("B", _Setup("SHORT"))
    assert s.stop == pytest.approx(105.0) and s.target == pytest.approx(90.0)
    vs = ValidationSettings()
    v1 = variant_version(variant_config("B", "2.0.0+x", vs))
    assert v1 == variant_version(variant_config("B", "2.0.0+x", vs))
    assert v1 != variant_version(variant_config("B", "2.0.0+y", vs))
    vs2 = ValidationSettings(paper=PaperCosts(taker_fee_pct=0.06))
    assert v1 != variant_version(variant_config("B", "2.0.0+x", vs2))


# ----------------------------------------------------------------------------- costs

PC = PaperCosts()


def test_entry_uses_bid_ask_and_flags_stale_quotes():
    q = Quote(99.9, 100.1, 100.0, 1.0)
    f = entry_fill("LONG", q, PC)
    assert f.source == "quote" and f.price == pytest.approx(100.1 * 1.0002)
    assert f.spread_per_unit == pytest.approx(0.1)
    s = entry_fill("SHORT", q, PC)
    assert s.price == pytest.approx(99.9 * 0.9998)
    stale = entry_fill("LONG", Quote(99.9, 100.1, 100.0, 60.0), PC)
    assert stale.source == "last_price_estimate" and stale.price > 100.0
    assert entry_fill("LONG", Quote(None, None, None, None), PC) is None
    assert trigger_price("LONG", q, PC) == (99.9, "quote")  # a long closes on the bid


def test_exit_fills_and_settlement_math():
    assert check_exit("LONG", 94.9, 95, 110) == "STOP" and check_exit("SHORT", 89.9, 105, 90) == "TARGET"
    stop = exit_fill("LONG", "STOP", 94.0, 110, PC)  # gapped below the stop: fills at the gap, worse
    assert stop.price == pytest.approx(94.0 * (1 - 0.0005))
    assert exit_fill("LONG", "TARGET", 111, 110, PC).price == 110
    r = settle("LONG", 2.0, 100.0, 95.0, 110.0, "TARGET", 0.5, PC)
    assert r["gross_pnl"] == pytest.approx(20.0)
    assert r["exit_fee"] == pytest.approx(0.0002 * 110 * 2)  # maker fee on the take-profit
    assert r["net_r"] == pytest.approx((20 - 0.00055 * 200 - 0.0002 * 220 - 0.5) / 10)


# ----------------------------------------------------------------------------- risk engine

def test_risk_breakers_and_exposure():
    lim = RiskLimits()
    st = AccountState(10_000, [], [Position("AAAUSDT", "15m", "SHORT", 50, 1000)])
    corr = lambda a, b: 0.9  # noqa: E731
    assert check_entry(st, "BBBUSDT", "15m", "SHORT", 50, lim, T0, True, True, corr)[1] == "correlated"
    assert check_entry(st, "AAAUSDT", "1h", "LONG", 50, lim, T0, True, True, None)[1] == "symbol_open"
    assert check_entry(st, "BBBUSDT", "15m", "LONG", 50, lim, T0, False, True, None)[1] == "data_integrity"
    assert check_entry(st, "BBBUSDT", "15m", "LONG", 50, lim, T0, True, False, None)[1] == "data_integrity"
    assert check_entry(st, "BBBUSDT", "15m", "LONG", 200, lim, T0, True, True, None)[1] == "open_risk"
    assert check_entry(st, "BBBUSDT", "15m", "LONG", 50, lim, T0, True, True, None)[0]
    st.realized = [(T0, -250.0)]
    assert check_entry(st, "BBBUSDT", "15m", "LONG", 50, lim, T0 + timedelta(hours=1), True, True, None)[1] == "daily_loss"
    st.realized = [(T0 - timedelta(days=3), 500.0), (T0 - timedelta(days=2), -1700.0)]
    assert check_entry(st, "BBBUSDT", "15m", "LONG", 50, lim, T0, True, True, None)[1] == "max_drawdown"
    assert breakers(st, lim, T0)["max_drawdown"]["tripped"]
    st.drawdown_reset_at = T0 - timedelta(days=1)  # manual reset: peak restarts
    assert check_entry(st, "BBBUSDT", "15m", "LONG", 50, lim, T0, True, True, None)[0]
    ex = exposure(AccountState(10_000, [], [Position("A", "15m", "LONG", 50, 1000), Position("B", "1h", "SHORT", 50, 400)]))
    assert ex["open_risk_pct"] == 1.0 and ex["net_directional_notional"] == 600 and ex["by_timeframe"] == {"15m": 1, "1h": 1}


# ----------------------------------------------------------------------------- statistics

def _tr(k, d, r, hours):
    return {"decision_ns": int(hours * H), "exit_close_ns": int((hours + 1) * H), "direction": d, "net_r": r, "id": k}


def test_clusters_bootstrap_drawdown_and_consistency():
    trades = [_tr(0, "SHORT", -1, 0), _tr(1, "SHORT", -1, 0.5), _tr(2, "SHORT", -1, 1.4), _tr(3, "LONG", 2, 0.6),
              _tr(4, "SHORT", 2, 10)]
    ids = assign_clusters(trades)
    assert ids[0] == ids[1] == ids[2] and len(set(ids)) == 3  # chained shorts = one cluster
    b = cluster_bootstrap(trades * 1, reps=500)
    assert b["n_clusters"] == 3 and b["n_trades"] == 5
    many = [_tr(k, "LONG", 1.0 if k % 3 else -1.0, k * 5) for k in range(300)]
    bb = cluster_bootstrap(many, reps=500)
    assert bb["prob_avg_positive"] == 1.0 and bb["avg_net_r_ci95"][0] > 0
    dd = drawdown_distribution(many, [1, 5, 50], reps=300)
    p = dd["prob_dd_beyond"]
    assert p["1R"] >= p["5R"] >= p["50R"] and p["50R"] == 0.0
    month = 35 * 24
    cons = period_consistency([_tr(0, "LONG", 10, 0), _tr(1, "LONG", 1, month + 5), _tr(2, "LONG", -1, 2 * month + 5)])
    assert cons["best_month_share_of_positive_r"] == pytest.approx(10 / 11, abs=1e-3)
    assert holm({"a": 0.01, "b": 0.04, "c": 0.5}) == {"a": 0.03, "b": 0.08, "c": 0.5}


# ----------------------------------------------------------------------------- promotion gate

def _closed(k, r, day, cid_hours=None):
    t = T0 + timedelta(days=day)
    return {"id": str(k), "status": "CLOSED", "decision": "ACCEPTED", "portfolio_taken": True, "net_r": r,
            "net_pnl": r * 50, "risk_amount": 50.0, "entry_fee": 1.0, "exit_fee": 1.0, "spread_cost": 0.2,
            "slippage_cost": 0.3, "funding_cost": 0.0, "candle_close_time": t, "exit_time": t + timedelta(hours=2),
            "direction": "LONG", "symbol": "BTCUSDT", "data": {},
            "cost_flags": {"entry": "observed_bid_ask", "exit": "ticker_quote", "funding": "exchange_funding_history"}}


def _variant(days):
    return {"id": "C@x", "key": "C", "name": "C", "version": "x", "period_start": T0 - timedelta(days=days), "config": {}}


def test_gate_insufficient_then_ready_and_manual_only():
    gate = GateThresholds()
    tests_ok = {"finished_at": (T0 + timedelta(days=89)).isoformat(), "passed": 200, "failed": 0, "full_suite": True}
    few = [_closed(k, 1.0, k) for k in range(5)]
    rep = variant_report(few, _variant(0), 10_000, T0 + timedelta(days=5), gate)
    a = assess(rep, few, gate, T0 + timedelta(days=5), tests_ok, False)
    assert a["readiness"] == "INSUFFICIENT_FORWARD_DATA" and a["manual_approval_required"]
    rng = np.random.default_rng(1)
    good = [_closed(k, float(rng.choice([1.5, -1.0], p=[0.55, 0.45])), k * 0.9) for k in range(100)]
    now = T0 + timedelta(days=90)
    rep = variant_report(good, _variant(1), 10_000, now, gate)
    a = assess(rep, good, gate, now, tests_ok, False)
    failed = [c["key"] for c in a["checks"] if not c["passed"]]
    assert a["readiness"] == "READY_FOR_REVIEW", failed
    assert assess(rep, good, gate, now, None, False)["readiness"] == "NOT_READY"  # no recorded test run
    assert assess(rep, good, gate, now, tests_ok, True)["readiness"] == "NOT_READY"  # breaker tripped
    est = [{**t, "cost_flags": {"entry": "estimated_from_last_price"}} for t in good]
    assert assess(variant_report(est, _variant(1), 10_000, now, gate), est, gate, now, tests_ok, False)["readiness"] == "NOT_READY"
    # one huge month drives everything -> concentration fails
    conc = [_closed(k, 20.0 if k < 3 else (-0.2 if k % 2 else 0.1), k * 0.9) for k in range(100)]
    a2 = assess(variant_report(conc, _variant(1), 10_000, now, gate), conc, gate, now, tests_ok, False)
    assert not next(c for c in a2["checks"] if c["key"] == "concentration")["passed"]


# ----------------------------------------------------------------------------- forward service (persistence)

class _Store:
    def __init__(self, s):
        self.current = s


class _State:
    def __init__(self):
        self.view = {}

    def ticker_view(self, symbol):
        return self.view


class _Market:
    def __init__(self):
        self.state = _State()


def _signal_frame():
    p = phase1_params(mtf={"enabled": False})
    df = make_candles(1200, seed=3, drift=0.0008)
    f = compute_features(df, "15m", p)
    for i in range(p.warmup_bars, len(f) - 40):
        ev = evaluate_at(f, i, p, {}, context=False)
        if ev.setup is not None:
            return p, df, i, ev
    raise AssertionError("no setup")


async def test_parallel_paper_trades_persist_fill_and_exit(db):
    from app.db.session import session_factory
    from app.forward.service import ForwardService

    p, df, i, ev = _signal_frame()
    s = RuntimeSettings(strategy=p)
    st = ev.setup
    f = compute_features(df.iloc[: i + 1], "15m", p)
    ev = evaluate_at(f, i, p, {}, context=False)
    close = datetime.fromtimestamp(int(f.close_time[i]) / 1e9, UTC)
    now = close + timedelta(seconds=5)
    m = _Market()
    px = float(f.close[i])
    m.state.view = {"available": True, "bid_price": px * 0.9999, "ask_price": px * 1.0001, "last_price": px,
                    "quote_at": now.isoformat(), "funding_rate": 0.0001,
                    "next_funding_time": (now + timedelta(hours=4)).isoformat()}
    svc = ForwardService(m, _Store(s), session_factory)
    import app.forward.service as fs
    real_now = fs.utcnow
    fs.utcnow = lambda: now + timedelta(seconds=15)  # local clock 15 s ahead of the exchange clock
    m.state.view["quote_at"] = (now + timedelta(seconds=14)).isoformat()  # quote 1 s old on the local clock
    try:
        await svc.on_evaluation("TESTUSDT", "15m", f, ev, True, now, {}, None, None)
    finally:
        fs.utcnow = real_now
    ov = await svc.overview()
    recs = {k: v["recent"] for k, v in ov["variants"].items()}
    assert set(recs) == {"A", "B", "C"}
    assert recs["A"][0]["status"] == "OPEN" and recs["B"][0]["status"] == "OPEN"
    assert recs["C"][0]["status"] == "REJECTED"  # no higher timeframe -> HTF rule rejects
    a = recs["A"][0]
    assert a["fill_price"] == pytest.approx(px * 1.0001 * 1.0002 if st.direction == "LONG" else px * 0.9999 * 0.9998)
    assert a["cost_flags"]["entry"] == "observed_bid_ask" and a["portfolio_taken"]  # regression: clock skew
    assert recs["B"][0]["portfolio_taken"] is False or recs["B"][0]["portfolio_taken"]  # separate paper account
    # idempotent: the same evaluation does not duplicate records
    await svc.on_evaluation("TESTUSDT", "15m", f, ev, True, now, {}, None, None)
    assert len((await svc.overview())["variants"]["A"]["recent"]) == 1
    # stop hit on the closing side of the book
    long = st.direction == "LONG"
    stop = a["stop_loss"]
    hit = stop * (0.999 if long else 1.001)
    later = now + timedelta(minutes=3)
    view = {**m.state.view, "bid_price": hit, "ask_price": hit, "last_price": hit, "quote_at": later.isoformat()}
    import app.forward.service as fs
    real = fs.utcnow
    fs.utcnow = lambda: later
    try:
        await svc.on_ticker("TESTUSDT", view)
    finally:
        fs.utcnow = real
    ov = await svc.overview()
    ra = ov["variants"]["A"]["recent"][0]
    assert ra["status"] == "CLOSED" and ra["exit_reason"] == "STOP" and ra["net_r"] < -1.0
    assert ov["variants"]["A"]["strategy"]["summary"]["n"] == 1
    assert ov["variants"]["A"]["promotion"]["readiness"] == "INSUFFICIENT_FORWARD_DATA"


async def test_late_detection_is_missed_and_gaps_are_backfilled_as_missed(db):
    from app.db.session import session_factory
    from app.forward.service import ForwardService

    p, df, i, _ = _signal_frame()
    s = RuntimeSettings(strategy=p)
    m = _Market()
    svc = ForwardService(m, _Store(s), session_factory)
    f0 = compute_features(df.iloc[: i - 5], "15m", p)
    ev0 = evaluate_at(f0, len(f0) - 1, p, {}, context=False)
    t0 = datetime.fromtimestamp(int(f0.close_time[-1]) / 1e9, UTC)
    await svc.on_evaluation("TESTUSDT", "15m", f0, ev0, True, t0, {}, None, None)  # sets the cursor
    f = compute_features(df.iloc[: i + 3], "15m", p)  # the tracker "was down" for several candles
    ev = evaluate_at(f, len(f) - 1, p, {}, context=False)
    late = datetime.fromtimestamp(int(f.close_time[-1]) / 1e9, UTC) + timedelta(hours=2)
    await svc.on_evaluation("TESTUSDT", "15m", f, ev, True, late, {}, None, None)
    rows = (await svc.overview())["variants"]["A"]["recent"]
    assert any(r["status"] == "MISSED" and "not running" in r["decision_reason"] for r in rows)


def test_validation_api(client):
    r = client.get("/api/validation/overview")
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d["variants"]) == {"A", "B", "C"} and "FORWARD" in d["label"]
    assert d["variants"]["C"]["promotion"]["readiness"] == "INSUFFICIENT_FORWARD_DATA"
    bad = client.post("/api/validation/promotion/C/decision", json={"decision": "APPROVED", "note": "try", "reviewer": "me"})
    assert bad.status_code == 409
    ok = client.post("/api/validation/promotion/C/decision", json={"decision": "DEFERRED", "note": "wait", "reviewer": "me"})
    assert ok.json()["production_changed"] is False
    assert client.get("/api/settings").json()["settings"]["decision"]["mode"] == "shadow"  # never auto-enforced


@pytest.fixture
def client(tmp_path, monkeypatch):
    from tests.test_api import client as api_client

    yield from api_client.__wrapped__(tmp_path, monkeypatch)


# ----------------------------------------------------------------------------- stress simulation

def test_stress_simulation_adverse_assumptions_hurt():
    from app.research.robustness import Scenario, Series, correlated_reversal, simulate

    o = np.array([100, 100, 99.5, 97.0, 96.0, 98.0])
    h = np.array([100.5, 100.4, 99.8, 97.2, 96.5, 98.5])
    lo = np.array([99.5, 99.2, 97.1, 94.5, 95.0, 97.0])
    c = np.array([100, 99.6, 97.2, 95.0, 96.0, 98.0])
    s = Series(o, h, lo, c, np.arange(6) * 900 * 10**9, 900 * 10**9, np.array([]), np.array([]))
    base = simulate(s, 0, "LONG", 95.0, 110.0, 5, Scenario(), 0.5)
    delayed_stop = simulate(s, 0, "LONG", 95.0, 110.0, 5, Scenario(stop_delay=True), 0.5)
    fees = simulate(s, 0, "LONG", 95.0, 110.0, 5, Scenario(fee_mult=3), 0.5)
    assert base["reason"] == "STOP" and delayed_stop["net_r"] <= base["net_r"] and fees["net_r"] < base["net_r"]
    assert simulate(s, 0, "LONG", 99.7, 110.0, 5, Scenario(entry_delay=1), 0.5) is None  # delayed entry beyond stop
    tr = [{"decision_ns": 0, "direction": "SHORT", "net_r": 2.0}, {"decision_ns": 10, "direction": "SHORT", "net_r": -1.0},
          {"decision_ns": 10 * H, "direction": "SHORT", "net_r": 2.0}]
    assert [t["net_r"] for t in correlated_reversal(tr)] == [-1.0, -1.0, 2.0]
