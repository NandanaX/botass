"""Strategy D: timestamps, leakage, symmetry, disagreement, plans, portfolio limits, isolation and routing."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from app.db.models import StrategyDDecision
from app.forward.variants import VARIANT_RULES
from app.notifications.events import EventIn, route
from app.notifications.settings import NotificationSettings
from app.settings_store import RuntimeSettings
from app.strategy_d.confluence import RULES, alignment, h1_allows_pullback, rule_passes, score
from app.strategy_d.context import classify_at, prepare, state_at_decision
from app.strategy_d.engine import Trigger, decide, detect_continuation, detect_retest, pick_per_direction, trade_plan
from app.strategy_d.params import D_PARAMS, DParams
from app.strategy_d.service import StrategyDService, message_for
from tests.conftest import make_candles

P = D_PARAMS
START = datetime(2024, 1, 1, tzinfo=UTC)


def frames(n15=3000):
    """A consistent synthetic market on all four timeframes, built from one 15m random walk."""
    base = make_candles(n15 * 8, 15, seed=11, start=START, drift=0.0001)
    def agg(rule):
        g = base.set_index("open_time").resample(rule, label="left", closed="left")
        return g.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "turnover": "sum"}).dropna().reset_index()
    return {"15m": base, "1h": agg("1h"), "4h": agg("4h"), "1d": agg("1D")}


@pytest.fixture(scope="module")
def dfs():
    return frames()


@pytest.fixture(scope="module")
def data(dfs):
    return {tf: prepare(df, tf, P) for tf, df in dfs.items()}


def mirror(df):
    """The same market upside down: every LONG condition becomes the matching SHORT condition."""
    k = 2 * float(df["close"].max())
    out = df.copy()
    out["open"], out["close"], out["high"], out["low"] = k - df["open"], k - df["close"], k - df["low"], k - df["high"]
    return out


def closes(ts):
    return int(pd.Timestamp(ts).value)


# ----------------------------------------------------------------------- timestamp alignment

def test_higher_timeframes_use_only_candles_closed_at_the_decision(data):
    t = data["4h"].f
    j = 400
    close = int(t.close_time[j])
    assert state_at_decision(data["4h"], "4h", close, P).candle_close_ns == close  # closes exactly now: known
    assert state_at_decision(data["4h"], "4h", close - 1, P).candle_close_ns == int(t.close_time[j - 1])  # a moment earlier: not yet


def test_daily_candle_is_not_used_until_it_has_closed(data):
    d = data["1d"].f
    j = 230
    mid_day = int(d.close_time[j]) + 12 * 3600 * 10**9
    assert state_at_decision(data["1d"], "1d", mid_day, P).candle_close_ns == int(d.close_time[j])


@pytest.mark.parametrize("tf", ["1h", "4h", "1d"])
def test_a_missing_or_incomplete_higher_timeframe_is_insufficient_not_a_guess(data, tf):
    f = data[tf].f
    late = int(f.close_time[-1]) + 3 * int(f.close_time[1] - f.close_time[0])
    assert state_at_decision(data[tf], tf, late, P).bias == "INSUFFICIENT_DATA"
    assert state_at_decision(None, tf, late, P).bias == "INSUFFICIENT_DATA"
    assert state_at_decision(data[tf], tf, int(f.open_time[0]), P).bias == "INSUFFICIENT_DATA"


def test_too_little_history_is_insufficient(data):
    assert classify_at(data["1h"], P.min_bars - 1, P).bias == "INSUFFICIENT_DATA"


async def test_only_15m_candles_produce_decisions(db):
    svc = service()
    for tf in ("5m", "30m", "1h", "4h", "1d"):
        await svc.on_candle("BTCUSDT", tf, True, START, None)
    assert svc.status["candles_checked"] == 0


# ----------------------------------------------------------------------- no future data

def test_future_candles_cannot_change_a_past_decision(dfs):
    full = {tf: prepare(df, tf, P) for tf, df in dfs.items()}
    f = full["15m"].f
    seen = 0
    cut_at = pd.Timestamp(int(f.close_time[-400]), tz="UTC")
    cut = {tf: prepare(df[df["open_time"] + pd.Timedelta(_minutes(tf), "min") <= cut_at].reset_index(drop=True), tf, P) for tf, df in dfs.items()}
    n = len(cut["15m"].f)
    assert n < len(f)
    for i in range(n - 1, n - 300, -1):
        a, b = decide(full, i, P, "T"), decide(cut, i, P, "T")
        assert [(x["direction"], x["entry_type"], x["decision"], x["confluence"]["score"]) for x in a] == [(x["direction"], x["entry_type"], x["decision"], x["confluence"]["score"]) for x in b]
        seen += len(a)
    assert seen > 0  # the check above compared real decisions, not two empty lists


def _minutes(tf):
    return {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}[tf]


# ----------------------------------------------------------------------- LONG and SHORT symmetry

def test_bias_is_symmetric(dfs):
    flip = {"BULLISH": "BEARISH", "BEARISH": "BULLISH"}
    up, down = prepare(dfs["1h"], "1h", P), prepare(mirror(dfs["1h"]), "1h", P)
    checked = set()
    for j in range(P.min_bars, len(up.f), 7):
        a, b = classify_at(up, j, P).bias, classify_at(down, j, P).bias
        assert b == flip.get(a, a)
        checked.add(a)
    assert {"BULLISH", "BEARISH"} <= checked


def bars(rows, sw_high=None, sw_low=None, structure=None):
    a = np.array(rows, dtype=float)
    n = len(a)
    return SimpleNamespace(open=a[:, 0], high=a[:, 1], low=a[:, 2], close=a[:, 3], atr=np.full(n, 1.0), ema_fast=a[:, 3] * 0 + 90, ema_mid=a[:, 3] * 0 + 80, ema_slow=a[:, 3] * 0 + 70,
                           prior_high=np.full(n, 100.0), prior_low=np.full(n, np.nan),
                           structure=SimpleNamespace(last_swing_high=np.full(n, np.nan) if sw_high is None else sw_high, last_swing_low=np.full(n, np.nan) if sw_low is None else sw_low, structure=np.full(n, 1) if structure is None else structure))


def mirrored(ns):
    k = 400.0
    return SimpleNamespace(open=k - ns.open, high=k - ns.low, low=k - ns.high, close=k - ns.close, atr=ns.atr, ema_fast=k - ns.ema_fast, ema_mid=k - ns.ema_mid, ema_slow=k - ns.ema_slow,
                           prior_high=np.full(len(ns.open), np.nan), prior_low=k - ns.prior_high,
                           structure=SimpleNamespace(last_swing_high=k - ns.structure.last_swing_low, last_swing_low=k - ns.structure.last_swing_high, structure=-ns.structure.structure))


RETEST = [(99, 99.5, 98.5, 99), (100, 102, 100, 101.5), (101.5, 103, 101, 102), (100.5, 102.5, 99.9, 101)]


def test_retest_fires_on_the_first_return_in_both_directions():
    f = bars(RETEST)
    t = detect_retest(f, 3, P, "LONG")
    assert t is not None and t.entry_type == "retest" and t.zone_level == 100.0
    s = detect_retest(mirrored(f), 3, P, "SHORT")
    assert s is not None and s.zone_level == 300.0


def test_retest_needs_a_close_back_beyond_the_level_and_only_counts_once():
    assert detect_retest(bars(RETEST[:3] + [(102, 102.5, 99, 99.5)]), 3, P, "LONG") is None
    twice = [RETEST[0], RETEST[1], (101.5, 102, 100.05, 101), (101, 102, 100.05, 101.8)]
    assert detect_retest(bars(twice), 3, P, "LONG") is None


def test_retest_needs_the_15m_trend():
    f = bars(RETEST)
    f.ema_fast = f.ema_slow - 1
    assert detect_retest(f, 3, P, "LONG") is None


def test_continuation_is_symmetric():
    rows = [(99, 100, 98, 99.5), (100, 102, 100, 101.5)]
    f = bars(rows, sw_high=np.array([100.0, 100.0]), sw_low=np.array([95.0, 95.0]))
    assert detect_continuation(f, 1, "LONG").entry_type == "continuation"
    assert detect_continuation(mirrored(f), 1, "SHORT").entry_type == "continuation"
    f.close = np.array([100.5, 101.5])  # already beyond the swing on the candle before: not a new break
    assert detect_continuation(f, 1, "LONG") is None


# ----------------------------------------------------------------------- disagreement and confluence

def st(bias, last_break=None, last_dir=0):
    return {"bias": bias, "last_break": last_break, "last_break_dir": last_dir}


def test_score_shows_how_it_was_derived_and_isnt_a_probability():
    c = score("LONG", {"1d": "BULLISH", "4h": "BULLISH", "1h": "SIDEWAYS"}, True, P)
    assert c["score"] == 15 + 30 + 12 + 25 and c["out_of"] == 100
    assert {x["timeframe"]: x["points"] for x in c["parts"]} == {"1d": 15, "4h": 30, "1h": 12, "15m": 25}
    assert "Not a probability" in c["note"]


def test_score_is_symmetric():
    up = score("LONG", {"1d": "BULLISH", "4h": "BEARISH", "1h": "CONFLICTED"}, True, P)
    down = score("SHORT", {"1d": "BEARISH", "4h": "BULLISH", "1h": "CONFLICTED"}, True, P)
    assert up["score"] == down["score"]


def test_missing_timeframes_earn_nothing():
    assert score("LONG", {"1d": "INSUFFICIENT_DATA", "4h": "BULLISH", "1h": "BULLISH"}, True, P)["score"] == 85


def test_an_opposed_or_missing_4h_blocks_the_default_rule_whatever_the_score():
    opposed = {"1d": st("BULLISH"), "4h": st("BEARISH"), "1h": st("BULLISH")}
    assert not rule_passes("d_default", "LONG", opposed, 99, None, P)
    missing = {"1d": st("BULLISH"), "4h": st("INSUFFICIENT_DATA"), "1h": st("BULLISH")}
    assert not rule_passes("d_default", "LONG", missing, 99, None, P)


@pytest.mark.parametrize(("rule", "d1", "h4", "h1", "expected"), [
    ("strict_1d_4h_1h", "BULLISH", "BULLISH", "BULLISH", True),
    ("strict_1d_4h_1h", "SIDEWAYS", "BULLISH", "BULLISH", False),
    ("4h_1h_1d_context", "BEARISH", "BULLISH", "BULLISH", True),
    ("4h_1h_1d_context", "BULLISH", "BULLISH", "CONFLICTED", False),
    ("4h_with_1h_pullback", "BEARISH", "BULLISH", "BEARISH", True),
    ("4h_with_1h_pullback", "BULLISH", "SIDEWAYS", "BULLISH", False),
    ("no_filter", "BEARISH", "BEARISH", "BEARISH", True),
])
def test_alternative_rules(rule, d1, h4, h1, expected):
    assert rule in RULES
    states = {"1d": st(d1), "4h": st(h4), "1h": st(h1)}
    assert rule_passes(rule, "LONG", states, 50, None, P) is expected


def test_a_1h_pullback_is_allowed_until_it_changes_character():
    assert h1_allows_pullback("LONG", {"1h": st("BEARISH", "BOS", -1)})
    assert not h1_allows_pullback("LONG", {"1h": st("BEARISH", "CHOCH", -1)})
    assert not h1_allows_pullback("LONG", {"1h": st("INSUFFICIENT_DATA")})
    assert not h1_allows_pullback("SHORT", {"1h": st("BULLISH", "CHOCH", 1)})


def test_existing_c_rule_reads_the_engines_own_confirmation():
    assert rule_passes("existing_c", "LONG", {}, 0, True, P) and not rule_passes("existing_c", "LONG", {}, 100, False, P)


def test_alignment_labels():
    assert [alignment("SHORT", b) for b in ("BEARISH", "BULLISH", "CONFLICTED", "INSUFFICIENT_DATA")] == ["aligned", "opposed", "neutral", "missing"]


def test_one_decision_per_direction_per_candle():
    picked = pick_per_direction([Trigger("LONG", "continuation", ""), Trigger("LONG", "breakout", ""), Trigger("SHORT", "pullback", "")])
    assert [(t.direction, t.entry_type) for t in picked] == [("LONG", "breakout"), ("SHORT", "pullback")]


def test_no_15m_setup_means_no_decision_whatever_the_higher_timeframes_say(data):
    f = data["15m"].f
    decided = [i for i in range(len(f) - 300, len(f) - 1) if decide(data, i, P, "T")]
    assert len(decided) < 299  # most candles are NO TRADE


def test_real_decisions_explain_themselves_and_only_come_from_15m(data):
    f = data["15m"].f
    rows = [r for i in range(len(f) - 600, len(f) - 1) for r in decide(data, i, P, "T")]
    assert rows
    for r in rows:
        assert r["decision"] in ("QUALIFIED", "WATCHLIST", "REJECTED") and r["reason"] and set(r["states"]) == {"1d", "4h", "1h"}
        assert r["entry_type"] in ("breakout", "retest", "pullback", "continuation")
        if r["decision"] == "QUALIFIED":
            assert r["plan"] and r["confluence"]["score"] >= P.qualify_score


# ----------------------------------------------------------------------- plans

@pytest.mark.parametrize(("direction", "stop", "tp"), [("LONG", 97.5, [102.5, 105.0, 107.5]), ("SHORT", 102.5, [97.5, 95.0, 92.5])])
def test_stop_and_targets(direction, stop, tp):
    plan = trade_plan(direction, 100.0, stop, P)
    assert [t["price"] for t in plan["targets"]] == tp and [t["weight"] for t in plan["targets"]] == [0.3, 0.4, 0.3]
    assert plan["gross_r_all_targets"] == pytest.approx(2.0) and plan["stop_move_pct"] == pytest.approx(2.5)
    assert plan["net_r_all_targets"] < 2.0 and plan["net_r_at_stop"] < -1.0  # costs are charged, and a stop can lose more than 1R


# ----------------------------------------------------------------------- service: duplicates, portfolio, routing

class FakeCenter:
    def __init__(self):
        self.events = []

    async def emit(self, ev):
        self.events.append(ev)


def service(telegram=False, market=None):
    from app.db.session import session_factory
    s = RuntimeSettings()
    s.notifications.strategy_d_telegram = telegram
    return StrategyDService(market or SimpleNamespace(), SimpleNamespace(current=s), session_factory, FakeCenter())


def f15(n=3, ts=START):
    t0 = int(pd.Timestamp(ts).value)
    t = t0 + 900 * 10**9 * np.arange(n, dtype=np.int64)
    return SimpleNamespace(open=np.full(n, 100.0), high=np.full(n, 100.5), low=np.full(n, 99.5), close=np.full(n, 100.0), atr=np.full(n, 1.0), open_time=t, close_time=t + 900 * 10**9)


def row(direction="LONG", entry_type="breakout", decision="QUALIFIED"):
    stop = 97.5 if direction == "LONG" else 102.5
    return {"direction": direction, "entry_type": entry_type, "entry_label": "breakout confirmation", "decision": decision, "reason_code": "ok", "reason": "because",
            "confluence": score(direction, {"1d": "BULLISH", "4h": "BULLISH", "1h": "BULLISH"}, True, P), "plan": trade_plan(direction, 100.0, stop, P),
            "states": {tf: {"bias": "BULLISH"} for tf in ("1d", "4h", "1h")}}


def at(f, i=2, extra=5):
    return datetime.fromtimestamp(int(f.close_time[i]) / 1e9, UTC) + timedelta(seconds=extra)


async def stored():
    from sqlalchemy import select

    from app.db.session import session_factory
    async with session_factory()() as s:
        return (await s.execute(select(StrategyDDecision).order_by(StrategyDDecision.signal_ns))).scalars().all()


async def test_a_re_evaluated_candle_is_one_decision(db):
    svc, f = service(), f15()
    for _ in range(2):
        await svc._record("BTCUSDT", f, 2, row(), True, at(f), None, None)
    assert len(await stored()) == 1 and len(svc.center.events) == 1


async def test_a_repeat_soon_after_a_taken_trade_is_rejected_as_a_duplicate(db):
    svc, f = service(), f15()
    await svc._record("BTCUSDT", f, 1, row(), True, at(f, 1), None, None)
    (await stored())
    await svc._record("BTCUSDT", f, 2, row(entry_type="retest"), True, at(f), None, None)
    a, b = await stored()
    assert a.taken and not b.taken and b.reason_code == "duplicate"


async def test_a_previously_rejected_setup_can_qualify_on_a_new_candle(db):
    svc, f = service(), f15()
    await svc._record("BTCUSDT", f, 1, row(decision="REJECTED"), True, at(f, 1), None, None)
    await svc._record("BTCUSDT", f, 2, row(), True, at(f), None, None)
    a, b = await stored()
    assert not a.taken and b.taken and b.decision == "QUALIFIED"


async def test_portfolio_limits_match_the_standard_ones_and_are_applied(db):
    assert (P.risk.risk_per_trade_pct, P.risk.max_open_risk_pct, P.risk.max_correlated_positions, P.risk.daily_loss_limit_pct, P.risk.max_drawdown_pct) == (0.5, 2.0, 1, 2.0, 10.0)
    svc, f = service(), f15(40)
    await svc._record("BTCUSDT", f, 1, row(), True, at(f, 1), None, None)  # one position per coin
    await svc._record("BTCUSDT", f, 10, row(), True, at(f, 10), None, None)
    for n, s in enumerate(("ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT")):  # 0.5% each: the fifth breaks the 2% limit
        await svc._record(s, f, 20 + n, row(), True, at(f, 20 + n), None, None)
    rows = await stored()
    assert [r.reason_code for r in rows] == ["ok", "symbol_open", "ok", "ok", "ok", "open_risk"]
    assert rows[0].risk_amount == pytest.approx(50, rel=0.01) and rows[1].decision == "REJECTED"


async def test_correlated_position_is_rejected(db):
    svc, f = service(), f15()
    await svc._record("BTCUSDT", f, 1, row(), True, at(f, 1), None, None)
    await svc._record("ETHUSDT", f, 2, row(), True, at(f), lambda a, b: 0.9, None)
    assert [r.reason_code for r in await stored()] == ["ok", "correlated"]


async def test_bad_data_blocks_entry_and_late_detection_is_not_entered(db):
    svc, f = service(), f15()
    await svc._record("BTCUSDT", f, 1, row(), False, at(f, 1), None, None)
    await svc._record("ETHUSDT", f, 2, row(), True, at(f, 2, 7200), None, None)
    assert [r.reason_code for r in await stored()] == ["data_integrity", "late_detection"]
    assert not any(r.taken for r in await stored())


async def test_outcome_comes_from_later_closed_candles_and_reaches_the_account(db):
    svc, f = service(), f15(4)
    await svc._record("BTCUSDT", f, 1, row(), True, at(f, 1), None, None)
    await svc._reconcile("BTCUSDT", f)
    assert (await stored())[0].status == "OPEN"
    f.low = f.low.copy()
    f.low[3] = 90.0
    await svc._reconcile("BTCUSDT", f)
    r = (await stored())[0]
    assert r.status == "CLOSED" and r.net_r < -1
    v = await svc.report()
    assert v["closed"] == 1 and v["account"]["equity"] < 10_000 and v["evidence"] == "Unvalidated" and v["mode"] == "shadow"


async def test_telegram_stays_off_by_default_and_needs_the_setting(db):
    svc, f = service(), f15()
    await svc._record("BTCUSDT", f, 2, row(), True, at(f), None, None)
    ev = svc.center.events[0]
    assert ev["telegram"] is False and ev["strategy_id"] == "D" and "research" in ev["title"].lower()
    assert route(EventIn(**ev), NotificationSettings(), True) == (False, False)
    on, f2 = service(telegram=True), f15()
    await on._record("ETHUSDT", f2, 2, row(), True, at(f2), None, None)
    assert route(EventIn(**on.center.events[0]), NotificationSettings(), True)[1] is True


async def test_rejected_decisions_are_recorded_without_a_notification(db):
    svc, f = service(), f15()
    await svc._record("BTCUSDT", f, 2, row(decision="REJECTED"), True, at(f), None, None)
    assert svc.center.events == [] and len(await stored()) == 1


async def test_watchlist_stays_in_app_even_with_the_telegram_setting(db):
    svc, f = service(telegram=True), f15()
    await svc._record("BTCUSDT", f, 2, row(decision="WATCHLIST"), True, at(f), None, None)
    assert svc.center.events[0]["telegram"] is False


def test_message_uses_calculated_prices_and_labels_research():
    r = {**row(), "risk_amount": 49.2, "quantity": 0.043}
    text = message_for("BTCUSDT", r, "QUALIFIED", "D-x")
    assert "No order was placed" in text and "Primary timeframe: 15m" in text and "Stop-loss: 97.5" in text and "102.5 / 105 / 107.5" in text and "not a probability" in text and "49.20 USDT" in text


def test_d_events_are_never_grouped_with_live_signals_or_on_telegram_by_default():
    from app.notifications.events import TAXONOMY
    assert TAXONOMY["STRATEGY_D_QUALIFIED"][2] is False and TAXONOMY["STRATEGY_D_WATCHLIST"][2] is False
    assert NotificationSettings().strategy_d_telegram is False


async def test_failures_never_reach_the_scanner(db):
    async def boom(*a, **k):
        raise RuntimeError("no market")
    svc = service(market=SimpleNamespace(get_closed_candles=boom))
    await svc.on_candle("BTCUSDT", "15m", True, START, None)
    assert svc.status["last_error"] == "no market"


# ----------------------------------------------------------------------- isolation and versioning

def test_parameters_are_immutable_and_any_change_changes_the_version():
    with pytest.raises(ValidationError):
        P.qualify_score = 10  # type: ignore[misc]
    assert DParams().version == P.version and P.version.startswith("D-1.0.0+")
    assert DParams(qualify_score=71).version != P.version
    assert P.base.mtf.timeframes == ["1h", "4h"]


def test_editing_live_settings_cannot_change_d():
    s = RuntimeSettings()
    before = P.version
    s.strategy.mtf.timeframes.append("1d")
    assert P.version == before


def test_locked_strategies_abc_are_unchanged():
    assert VARIANT_RULES == {"A": {"name": "Baseline", "stop": "production", "require_mtf": False},
                             "B": {"name": "2.5 ATR stop", "stop": "atr", "atr_multiplier": 2.5, "target_r": 2.0, "require_mtf": False},
                             "C": {"name": "2.5 ATR + HTF", "stop": "atr", "atr_multiplier": 2.5, "target_r": 2.0, "require_mtf": True}}


def test_live_settings_dont_load_daily_candles_for_abc():
    assert "1d" not in RuntimeSettings().required_timeframes and "5m" not in RuntimeSettings().required_timeframes


@pytest.mark.parametrize("module", ["app/forward/service.py", "app/forward/variants.py", "app/strategy/engine.py", "app/strategy/setups.py", "app/signals/service.py"])
def test_the_live_path_doesnt_import_strategy_d(module):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / module).read_text(encoding="utf-8"))
    names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not any("strategy_d" in x for x in names)


def test_d_writes_only_its_own_table():
    root = Path(__file__).resolve().parents[1] / "app" / "strategy_d"
    tree = ast.parse((root / "service.py").read_text(encoding="utf-8"))
    models = [a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "app.db.models" for a in n.names]
    assert models == ["StrategyDDecision"]
