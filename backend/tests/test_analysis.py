"""Technical analysis engine (Phase 4): synthetic OHLCV with hand-checkable answers,
no-look-ahead / non-repainting checks, aggregation rules and edge cases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.analysis import indicators as ind
from app.analysis.candles import detect_candles
from app.analysis.chart_patterns import pattern_log, patterns_at
from app.analysis.engine import analyze_timeframe
from app.analysis.evidence import Evidence, aggregate
from app.analysis.frame import build_frame
from app.analysis.liquidity import fair_value_gaps, fibonacci
from app.analysis.momentum import divergences
from app.analysis.params import AnalysisParams
from app.analysis.structure import structure_at
from app.analysis.validation import event_study
from app.strategy.engine import evaluate_at
from app.strategy.params import StrategyParams
from app.strategy.setups import zones_at
from app.timeutil import floor_time, is_aligned
from tests.conftest import make_candles

SP = StrategyParams(mtf={"enabled": False}, zones={"htf_timeframes": []})
AP = AnalysisParams()


def frame_from_bars(bars, tf="15m", params=AP):
    start = datetime(2026, 4, 1, tzinfo=UTC)
    o, h, lo, c = (np.array(x, float) for x in zip(*bars, strict=True))
    df = pd.DataFrame({"open_time": pd.to_datetime([start + timedelta(minutes=15 * k) for k in range(len(bars))], utc=True),
                       "open": o, "high": h, "low": lo, "close": c, "volume": np.full(len(bars), 100.0),
                       "turnover": np.full(len(bars), 1e4)})
    return build_frame(df, tf, SP, params), df


def path(points, wick=0.2):
    """Piecewise-linear closes through (bar, price) points; open = previous close."""
    closes = []
    for (a, pa), (b, pb) in zip(points, points[1:], strict=False):
        closes += list(np.linspace(pa, pb, b - a, endpoint=False))
    closes.append(points[-1][1])
    bars, prev = [], closes[0]
    for c in closes:
        bars.append((prev, max(prev, c) + wick, min(prev, c) - wick, c))
        prev = c
    return bars


# ---------------------------------------------------------------- indicators

def test_extended_indicators():
    df = make_candles(400, seed=1, drift=0.003, vol=0.003)  # strong uptrend
    fr = build_frame(df, "1h", SP, AP)
    assert fr.adx[-1] > 25 and fr.plus_di[-1] > fr.minus_di[-1]
    assert fr.supertrend_dir[-1] == 1 and fr.supertrend[-1] < fr.f.close[-1]
    k = fr.stoch_k[np.isfinite(fr.stoch_k)]
    assert k.min() >= 0 and k.max() <= 100
    assert np.allclose(ind.obv([1, 2, 2, 1], [10, 20, 30, 40]), [0, 20, 20, -20])
    t = (pd.to_datetime(["2026-01-01 23:00", "2026-01-01 23:30", "2026-01-02 00:00"], utc=True).astype("int64")).to_numpy()
    vw = ind.session_vwap([11, 13, 21], [9, 11, 19], [10, 12, 20], [1, 1, 1], t, 86400)
    assert vw[0] == pytest.approx(10) and vw[1] == pytest.approx(11) and vw[2] == pytest.approx(20)  # resets at 00:00 UTC


def test_weekly_candles_align_to_monday():
    d = datetime(2026, 11, 5, 15, tzinfo=UTC)  # Thursday
    w = floor_time(d, "1w")
    assert w == datetime(2026, 11, 2, tzinfo=UTC) and w.weekday() == 0 and is_aligned(w, "1w")
    assert not is_aligned(datetime(2026, 11, 5, tzinfo=UTC), "1w")


# ---------------------------------------------------------------- market structure

UP_THEN_DOWN = [(0, 100), (10, 106), (20, 103), (30, 110), (40, 107), (50, 114), (60, 111), (70, 118), (85, 104)]


def test_structure_labels_bos_and_choch():
    fr, _ = frame_from_bars(path(UP_THEN_DOWN))
    st = structure_at(fr, len(fr) - 1)
    labels = [s.label for s in st.swings]
    assert "HH" in labels and "HL" in labels
    kinds = [(e.kind, e.direction) for e in st.events if e.kind != "WICK_BREAK"]
    assert ("BOS", "BULLISH") in kinds
    assert kinds[-1] == ("CHOCH", "BEARISH")
    assert st.trend == "DOWN" and st.classification == "TRANSITION_DOWN"
    mid = structure_at(fr, 65)  # before the drop: bullish structure
    assert mid.trend == "UP" and mid.classification in ("BULLISH", "CONSOLIDATION")


def test_wick_beyond_swing_without_close_is_not_a_break():
    bars = path(UP_THEN_DOWN[:-1] + [(75, 115)])
    o, h, lo, c = bars[-1]
    bars.append((c, c + 0.2, 110.0, 113.0))  # wick below the 111 swing low, close back above
    bars += [(113.0, 113.4, 112.6, 113.2)] * 3
    fr, _ = frame_from_bars(bars)
    st = structure_at(fr, len(fr) - 1)
    assert any(e.kind == "WICK_BREAK" and e.direction == "BEARISH" for e in st.events)
    assert not any(e.kind == "CHOCH" for e in st.events) and st.trend == "UP"


def test_structure_is_non_repainting():
    df = make_candles(700, seed=12, vol=0.006)
    full = build_frame(df, "15m", SP, AP)
    for i in (300, 450, 650):
        trunc = build_frame(df.iloc[: i + 1].reset_index(drop=True), "15m", SP, AP)
        assert structure_at(full, i).to_dict() == structure_at(trunc, i).to_dict()


# ---------------------------------------------------------------- candlesticks

DOWN = path([(0, 140), (40, 100)], wick=0.3)[:-1]
UP = path([(0, 100), (40, 140)], wick=0.3)[:-1]


@pytest.mark.parametrize("prefix, bars, name", [
    (DOWN, [(100, 100.4, 98.5, 100.3)], "Hammer"),
    (DOWN, [(100, 100.6, 99.9, 100.1)][:0] + [(99.9, 101.5, 99.85, 100.15)], "Inverted hammer"),
    (UP, [(140, 141.6, 139.7, 139.8)], "Shooting star"),
    (UP, [(140, 140.2, 138.4, 139.75)], "Hanging man"),
    (DOWN, [(100, 101, 99, 100)], "Doji"),
    (DOWN, [(100, 100.08, 98.5, 100.05)], "Dragonfly doji"),
    (UP, [(140, 141.5, 139.95, 139.97)], "Gravestone doji"),
    (DOWN, [(100, 102.02, 99.98, 102)], "Bullish marubozu"),
    (DOWN, [(100.5, 100.6, 99.5, 99.6), (99.5, 100.9, 99.4, 100.8)], "Bullish engulfing"),
    (UP, [(139.5, 140.6, 139.4, 140.5), (140.6, 140.7, 139.1, 139.2)], "Bearish engulfing"),
    (DOWN, [(101, 101.2, 98.8, 99), (98.9, 99.2, 98.5, 98.8), (99, 100.7, 98.9, 100.6)], "Morning star"),
    (UP, [(139, 141.2, 138.8, 141), (141.1, 141.5, 140.8, 141.2), (141, 141.1, 139.3, 139.4)], "Evening star"),
    (DOWN, [(101, 101.1, 98.9, 99), (98.9, 100.3, 98.8, 100.2)], "Piercing pattern"),
    (UP, [(139, 141.2, 138.8, 141), (141.1, 141.3, 139.5, 139.6)], "Dark cloud cover"),
    (DOWN, [(99, 100.1, 98.95, 100), (99.6, 100.9, 99.55, 100.8), (100.4, 101.7, 100.35, 101.6)], "Three white soldiers"),
    (UP, [(141, 141.05, 139.9, 140), (140.4, 140.45, 139.1, 139.2), (139.6, 139.65, 138.3, 138.4)], "Three black crows"),
    (DOWN, [(101, 101.1, 98.9, 99), (99.6, 100.3, 99.5, 100.2)], "Bullish harami"),
])
def test_candlestick_patterns(prefix, bars, name):
    fr, _ = frame_from_bars(list(prefix) + bars)
    found = {p.name: p for p in detect_candles(fr, len(fr) - 1)}
    assert name in found, list(found)
    p = found[name]
    assert p.end_index == len(fr) - 1 and p.start_index == len(fr) - len(bars)
    assert 0 <= p.body_pct <= 1 and p.explanation


def test_candle_context_and_levels():
    fr, _ = frame_from_bars(list(DOWN) + [(100, 100.4, 98.5, 100.3)])
    hammer = next(p for p in detect_candles(fr, len(fr) - 1) if p.name == "Hammer")
    assert hammer.trend_context == "DOWN" and hammer.in_context and hammer.direction == "BULLISH"


# ---------------------------------------------------------------- chart patterns

DOUBLE_TOP = [(0, 100), (30, 90), (60, 110), (80, 100), (100, 110.2), (125, 95)]


def test_double_top_developing_then_confirmed_and_non_repainting():
    fr, df = frame_from_bars(path(DOUBLE_TOP))
    log = [p for p in pattern_log(fr) if p.name == "Double top"]
    assert log, [p.name for p in pattern_log(fr)]
    inst = log[0]
    assert inst.detected_at == 105  # second top (bar 100) confirmed after 5 bars
    at_detect = {p.name: p for p in patterns_at(fr, inst.detected_at)}
    assert at_detect["Double top"].status == "DEVELOPING"
    final = {p.name: p for p in patterns_at(fr, len(fr) - 1)}
    dt = final["Double top"]
    assert dt.status == "CONFIRMED" and dt.breakout_direction == "BEARISH"
    assert dt.breakout_level == pytest.approx(100, abs=0.3)
    assert dt.target == pytest.approx(dt.breakout_level - dt.height)
    assert dt.invalidation_level == pytest.approx(110.4, abs=0.3)
    for i in (inst.detected_at, inst.detected_at + 8, len(fr) - 1):
        trunc = build_frame(df.iloc[: i + 1].reset_index(drop=True), "15m", SP, AP)
        assert [p.to_dict() for p in patterns_at(fr, i)] == [p.to_dict() for p in patterns_at(trunc, i)]


def test_ascending_triangle_and_no_forced_pattern():
    tri = [(0, 100), (12, 110), (24, 102), (36, 110.1), (48, 105), (60, 110), (70, 107.5)]
    fr, _ = frame_from_bars(path(tri))
    names = {p.name for p in patterns_at(fr, len(fr) - 1)}
    assert "Ascending triangle" in names
    flat, _ = frame_from_bars([(100, 100.1, 99.9, 100)] * 120)
    assert patterns_at(flat, len(flat) - 1) == []  # no pivots, no pattern


# ---------------------------------------------------------------- momentum, liquidity

def test_bearish_divergence_on_confirmed_pivots():
    # first high on a strong rally, second (higher) high on a weak grind: RSI lower at the second high
    pts = [(0, 100), (15, 120), (30, 110), (60, 121), (75, 112)]
    fr, _ = frame_from_bars(path(pts))
    kinds = {d["kind"] for d in divergences(fr, len(fr) - 1, recent=40)}
    assert "Bearish RSI divergence" in kinds


def test_fair_value_gap_and_fibonacci():
    bars = [(100, 100.5, 99.5, 100)] * 30 + [(100, 100.5, 99.8, 100.4), (100.4, 104, 100.3, 103.8),
                                             (103.8, 104.5, 101.5, 104.2)] + [(104.2, 104.6, 103.9, 104.3)] * 5
    fr, _ = frame_from_bars(bars)
    gaps = fair_value_gaps(fr, len(fr) - 1)
    assert gaps and gaps[-1]["direction"] == "BULLISH" and gaps[-1]["lower"] == 100.5 and gaps[-1]["upper"] == 101.5
    fr2, _ = frame_from_bars(path([(0, 100), (30, 90), (60, 110), (80, 104)]))
    fib = fibonacci(fr2, len(fr2) - 1)
    assert fib is not None and fib.direction == "UP"
    r618 = next(x for x in fib.levels if x["ratio"] == 0.618)
    assert r618["price"] == pytest.approx(fib.anchor_high - 0.618 * (fib.anchor_high - fib.anchor_low))


# ---------------------------------------------------------------- evidence aggregation

def test_correlated_oscillators_count_once():
    osc = [Evidence("momentum", "oscillators", k, 1, 1.0, k) for k in ("rsi", "stoch", "stochrsi")]
    other = [Evidence("trend", "ma_alignment", "ma", -1, 1.0, "ma"), Evidence("structure", "structure_state", "s", -1, 1, "s"),
             Evidence("volume", "vwap", "v", -1, 1.0, "v")]
    a = aggregate(osc + other, AP)
    mom = next(c for c in a["categories"] if c["category"] == "momentum")
    assert mom["score"] == pytest.approx(1.0)  # three oscillators = one group
    assert a["bias"] == "BEARISH BIAS" and 0 <= a["alignment_score"] <= 100
    assert "not a probability" in a["alignment_note"].lower()


def test_conflicted_and_insufficient():
    conflict = [Evidence("structure", "s", "s", 1, 1, "s"), Evidence("trend", "t", "t", -1, 1, "t"),
                Evidence("momentum", "m", "m", 1, 0.2, "m"), Evidence("levels", "l", "l", -1, 0.2, "l")]
    assert aggregate(conflict, AP)["bias"] == "CONFLICTED"
    assert aggregate([Evidence("trend", "t", "t", 1, 1, "t")], AP)["bias"] == "INSUFFICIENT DATA"


# ---------------------------------------------------------------- engine

def _analysis(fr, i):
    zones = zones_at(fr.f, i, SP, None, 4)
    return analyze_timeframe(fr, i, SP, zones, {}, evaluate_at(fr.f, i, SP), None, True)


def test_engine_full_analysis_has_no_look_ahead():
    df = make_candles(800, seed=21, drift=0.0002, vol=0.006)
    full = build_frame(df, "15m", SP, AP)
    for i in (450, 700):
        trunc = build_frame(df.iloc[: i + 1].reset_index(drop=True), "15m", SP, AP)
        a, b = _analysis(full, i), _analysis(trunc, i)
        assert a == b


def test_engine_outputs_and_scenarios():
    df = make_candles(900, seed=5, drift=0.0004, vol=0.005)
    fr = build_frame(df, "1h", SP, AP)
    a = _analysis(fr, len(fr) - 1)
    assert a["trend"] in ("BULLISH", "BEARISH", "SIDEWAYS")
    assert a["bias"] in ("BULLISH BIAS", "BEARISH BIAS", "NEUTRAL", "CONFLICTED", "INSUFFICIENT DATA")
    names = [s["name"][0] for s in a["scenarios"]]
    assert names == ["A", "B", "C"]
    ev = evaluate_at(fr.f, len(fr) - 1, SP)
    qualified = {s.direction for s in ev.setups if s.status == "QUALIFIED"}
    for s in a["scenarios"][:2]:
        assert (s["status"] == "QUALIFIED") == (s["direction"] in qualified)  # only when the strategy qualifies
        if s["status"] == "DEVELOPING":
            assert "Hypothetical" in s["note"] and "sizing" not in s
    cats = {c["category"] for c in a["evidence"]["categories"]}
    assert cats == {"structure", "trend", "momentum", "levels", "price_action", "volume", "volatility", "futures"}


def test_engine_handles_short_history():
    fr = build_frame(make_candles(60, seed=3), "1w", SP, AP)
    a = analyze_timeframe(fr, len(fr) - 1, SP, [], {}, None, None, True)
    assert a["bias"] in ("INSUFFICIENT DATA", "NEUTRAL", "CONFLICTED", "BULLISH BIAS", "BEARISH BIAS")
    assert a["scenarios"][-1]["direction"] == "NONE"


# ---------------------------------------------------------------- validation

def test_event_study_reports_insufficient_data_and_splits():
    fr = build_frame(make_candles(1500, seed=9, vol=0.006), "1h", SP, AP)
    r = event_study(fr, horizon=10)
    assert r["baseline"]["out_of_sample"]["n"] > 30
    assert r["events"]
    for e in r["events"]:
        for part in ("in_sample", "out_of_sample"):
            s = e[part]
            assert s["insufficient_data"] == (s["n"] < 30)
            if not s["insufficient_data"]:
                assert 0 <= s["hit_rate"] <= 1
