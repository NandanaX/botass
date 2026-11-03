"""Support/resistance zones and the breakout / pullback entry strategies.

Price paths are built bar by bar so every expected zone, trigger and level can be checked
by hand. Indicator values at the decision bar are forced (as in test_strategy) so that
the trend/momentum inputs are known exactly; zones always come from the real price path.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.indicators.structure import find_pivots
from app.indicators.zones import PivotArrays, detect_zones
from app.strategy.engine import evaluate_at
from app.strategy.features import compute_features
from app.strategy.params import StrategyParams

P = StrategyParams(mtf={"enabled": False}, zones={"htf_timeframes": []})


def frame(bars: list[tuple[float, float, float, float]], volume: float = 100.0) -> pd.DataFrame:
    start = datetime(2026, 3, 1, tzinfo=UTC)
    o, h, lo, c = (np.array(x, dtype=float) for x in zip(*bars, strict=True))
    times = pd.to_datetime([start + timedelta(minutes=15 * k) for k in range(len(bars))], utc=True)
    return pd.DataFrame({"open_time": times, "open": o, "high": h, "low": lo, "close": c,
                         "volume": np.full(len(bars), volume), "turnover": np.full(len(bars), volume * 100)})


def flat(n: int, level: float, amp: float = 0.3, period: int = 12, start_close: float | None = None):
    bars, prev = [], start_close if start_close is not None else level
    for k in range(n):
        c = level + amp * math.sin(2 * math.pi * k / period)
        bars.append((prev, max(prev, c) + 0.1, min(prev, c) - 0.1, c))
        prev = c
    return bars


def spike_high(bars, k: int, high: float, close: float):
    o = bars[k][0]
    bars[k] = (o, high, min(o, close) - 0.1, close)
    nxt = bars[k + 1]
    bars[k + 1] = (close, max(close, nxt[3]) + 0.1, min(close, nxt[3]) - 0.1, nxt[3])


def spike_low(bars, k: int, low: float, close: float):
    o = bars[k][0]
    bars[k] = (o, max(o, close) + 0.1, low, close)
    nxt = bars[k + 1]
    bars[k + 1] = (close, max(close, nxt[3]) + 0.1, min(close, nxt[3]) - 0.1, nxt[3])


def force(f, i, long=True, **over):
    """Known trend/momentum inputs at bar i (and ATR at i-1/i for zone tolerances)."""
    if long:
        f.ema_fast[i], f.ema_mid[i], f.ema_slow[i] = 104.0, 103.0, 101.0
        f.rsi[i], f.macd_hist[i], f.macd_hist[i - 1] = 62.0, 0.4, 0.3
    else:
        f.ema_fast[i], f.ema_mid[i], f.ema_slow[i] = 96.0, 97.0, 99.0
        f.rsi[i], f.macd_hist[i], f.macd_hist[i - 1] = 38.0, -0.4, -0.3
    f.rvol[i] = 1.8
    f.atr[i] = f.atr[i - 1] = 1.0
    for name, value in over.items():
        getattr(f, name)[i] = value
    return f


def mirror(df: pd.DataFrame, around: float = 200.0) -> pd.DataFrame:
    out = df.copy()
    out["open"], out["close"] = around - df["open"], around - df["close"]
    out["high"], out["low"] = around - df["low"], around - df["high"]
    return out


def breakout_bars(last=(104.2, 105.7, 104.1, 105.6), opposing: bool = False, pre=()):
    bars = flat(260, 100.0, amp=0.8) + flat(39, 102.0, start_close=100.0)
    spike_high(bars, 265, 105.0, 103.5)
    spike_high(bars, 280, 105.2, 103.4)
    if opposing:
        spike_high(bars, 200, 106.2, 100.5)
        spike_high(bars, 230, 106.3, 100.5)
    bars += list(pre)
    bars.append(last)
    return bars


# ---------------------------------------------------------------- zones

def _zones(df, at, **kw):
    pv = PivotArrays.from_pivots(find_pivots(df["high"].to_numpy(), df["low"].to_numpy(), kw.pop("strength", 3)))
    return detect_zones(timeframe="15m", open_time=df["open_time"].astype("int64").to_numpy(),
                        close=df["close"].to_numpy(), pivots=pv, atr_value=kw.pop("atr", 1.0), at_index=at,
                        max_zones_per_side=None, **kw)


def test_pivot_confirmation_delay_controls_zone_visibility():
    df = frame(breakout_bars())
    # Second swing high is at bar 280 and needs 3 bars on the right: known from bar 283.
    before = [z for z in _zones(df, 282) if z.upper > 104]
    after = [z for z in _zones(df, 283) if z.upper > 104]
    assert before == []  # one confirmed test only (min_tests=2)
    assert len(after) == 1 and after[0].tests == 2 and after[0].formed_index == 283


def test_zone_formation_bounds_type_strength_and_explanation():
    df = frame(breakout_bars())
    z = next(z for z in _zones(df, 298) if z.upper > 104)
    assert (z.lower, z.upper) == (105.0, 105.2)
    assert z.zone_type == "RESISTANCE" and z.status == "ACTIVE" and z.origin == "swing_highs"
    assert z.rejections == 2  # both spike candles closed back below the zone
    assert 0 < z.strength <= 100
    assert "2 confirmed swing highs" in z.explanation and "No candle has closed through it" in z.explanation
    # The id is stable as later bars arrive.
    assert z.id == next(x for x in _zones(df, 299) if x.upper > 104).id


def test_atr_clustering_tolerance_and_min_tests():
    df = frame(breakout_bars())
    assert [z for z in _zones(df, 298, cluster_atr_mult=0.1) if z.upper > 104] == []  # 0.2 apart > 0.1 ATR
    singles = [z for z in _zones(df, 298, cluster_atr_mult=0.1, min_tests=1) if z.upper > 104]
    assert len(singles) == 2
    assert [z for z in _zones(df, 298, min_tests=3) if z.upper > 104] == []


def test_broken_zone_flips_role_and_repeated_crossings_invalidate():
    bars = breakout_bars()
    bars.append((105.6, 106.2, 105.5, 106.0))
    df = frame(bars)
    z = next(z for z in _zones(df, len(df) - 1) if z.upper > 104 and z.lower < 106)
    assert z.status == "BROKEN" and z.zone_type == "SUPPORT" and z.crossings == 1
    assert "role reversal" in z.explanation
    chop = bars + [(106, 106.1, 103.9, 104.0), (104, 106.1, 103.9, 106.0), (106, 106.1, 103.9, 104.0)]
    df2 = frame(chop)
    z2 = next(z for z in _zones(df2, len(df2) - 1) if z.upper > 104 and z.lower < 106)
    assert z2.status == "INVALIDATED" and z2.crossings >= 3


def test_zones_have_no_look_ahead():
    df = frame(breakout_bars(opposing=True) + flat(30, 101.0, start_close=105.6))
    full = _zones(df, 290)
    trunc = _zones(df.iloc[:291].reset_index(drop=True), 290)
    assert [z.to_dict() for z in full] == [z.to_dict() for z in trunc]
    mutated = df.copy()
    mutated.loc[291:, ["high"]] = 150.0
    assert [z.to_dict() for z in _zones(mutated, 290)] == [z.to_dict() for z in full]


# ---------------------------------------------------------------- breakout (Strategy A)

def _eval(df, params=P, long=True, **over):
    f = compute_features(df, "15m", params)
    i = len(f) - 1
    force(f, i, long, **over)
    return evaluate_at(f, i, params, symbol="BTCUSDT"), f


def _setup(ev, strategy, direction="LONG"):
    return next(s for s in ev.setups if s.strategy == strategy and s.direction == direction)


def test_breakout_long_qualified_with_hybrid_stop_and_three_targets():
    ev, _ = _eval(frame(breakout_bars()))
    s = _setup(ev, "breakout")
    assert s.status == "QUALIFIED" and ev.setup is s
    assert s.entry_reference == 105.6
    plan = s.plan
    assert plan["zone"]["lower"] == 105.0 and plan["zone"]["upper"] == 105.2
    assert plan["stop"]["method"] == "hybrid"
    assert s.stop_loss == pytest.approx(105.0 - 0.25)  # zone lower - 0.25 ATR
    dist = 105.6 - 104.75
    assert [t["price"] for t in plan["targets"]] == pytest.approx([105.6 + dist, 105.6 + 2 * dist, 105.6 + 3 * dist])
    assert s.take_profit == pytest.approx(105.6 + 2 * dist) and s.risk_reward == pytest.approx(2.0)
    assert plan["entry_zone"] == [105.2, 105.6]
    assert plan["confirmation_candle"]["close"] == 105.6
    assert any("closes back below 105" in x for x in plan["invalidation"])
    assert plan["sizing"]["feasible"] and plan["sizing"]["estimated_loss"] <= plan["sizing"]["risk_budget"] + 1e-9


def test_breakout_short_is_the_mirror_image():
    ev, _ = _eval(mirror(frame(breakout_bars())), long=False)
    s = _setup(ev, "breakout", "SHORT")
    assert s.status == "QUALIFIED"
    assert s.entry_reference == pytest.approx(94.4)
    assert s.stop_loss == pytest.approx(200 - 104.75)
    assert s.stop_loss > s.entry_reference > s.take_profit


def test_breakout_rejected_when_opposing_zone_leaves_too_little_room():
    ev, _ = _eval(frame(breakout_bars(opposing=True)))
    s = _setup(ev, "breakout")
    assert s.status == "REJECTED" and ev.setup is None
    reason = next(r for r in s.reasons if r["code"] == "opposing_zone_too_close")
    room = (106.2 - 105.6) / (105.6 - 104.75)
    assert f"leaving only {room:.1f}R potential reward" in reason["message"]
    assert all(t["blocked"] for t in s.plan["targets"])  # every target lies beyond 106.2


@pytest.mark.parametrize("over, code", [
    ({"rvol": 0.9}, "failed_volume_confirmation"),
    ({"rsi": 75.0}, "failed_rsi_in_long_band"),
    ({"macd_hist": -0.1}, "failed_macd_hist_positive"),
])
def test_breakout_rejected_with_machine_readable_reason(over, code):
    ev, _ = _eval(frame(breakout_bars()), **over)
    s = _setup(ev, "breakout")
    assert s.status == "REJECTED" and code in [r["code"] for r in s.reasons]


def test_breakout_overextension_and_body_filters():
    ev, _ = _eval(frame(breakout_bars(last=(104.2, 107.1, 104.1, 107.0))))
    assert "failed_not_overextended" in [r["code"] for r in _setup(ev, "breakout").reasons]
    ev2, _ = _eval(frame(breakout_bars(last=(105.5, 106.9, 104.0, 105.6))))
    assert "failed_candle_body" in [r["code"] for r in _setup(ev2, "breakout").reasons]


def test_breakout_watchlist_before_the_close_above_resistance():
    # The bar before already closed above the nearby ~102.4 range zone, so the only
    # resistance left is 105.0-105.2, 0.4 ATR above the close.
    ev, _ = _eval(frame(breakout_bars(last=(104.3, 104.7, 104.1, 104.6), pre=[(102.0, 104.4, 101.9, 104.3)])))
    s = _setup(ev, "breakout")
    assert s.status == "WATCHLIST" and s.entry_reference is None and s.stop_loss is None
    assert s.reasons[0]["code"] == "awaiting_breakout_close"
    assert ev.setup is None


def test_no_setup_is_forced_without_a_trend():
    ev, _ = _eval(frame(breakout_bars()), ema_fast=102.0)
    assert ev.setups == [] and ev.setup is None


def test_stop_too_wide_rejects():
    params = P.model_copy(update={"stops": P.stops.model_copy(update={"max_stop_atr": 0.6})})
    ev, _ = _eval(frame(breakout_bars()), params=params)
    assert "stop_too_wide" in [r["code"] for r in _setup(ev, "breakout").reasons]


# ---------------------------------------------------------------- pullback (Strategy B)

def pullback_bars(last=(100.9, 101.5, 100.1, 101.4)):
    bars = flat(200, 90.0, amp=0.8) + flat(99, 102.0, start_close=90.0)
    spike_low(bars, 265, 100.0, 101.6)
    spike_low(bars, 280, 100.15, 101.6)
    bars.append(last)
    return bars


def _pb_params(**q):
    return P.model_copy(update={"qualification": P.qualification.model_copy(update=q)})


def test_pullback_long_qualified_on_confirmed_rejection():
    params = _pb_params(min_rr=0)
    ev, _ = _eval(frame(pullback_bars()), params=params, rsi=50.0, ema_fast=101.3, ema_mid=101.0, ema_slow=100.5)
    s = _setup(ev, "pullback")
    assert s.status == "QUALIFIED", s.reasons
    assert s.plan["zone"]["lower"] == 100.0 and s.plan["zone"]["upper"] == 100.15
    assert s.stop_loss == pytest.approx(100.0 - 0.25)
    assert s.stop_loss < s.entry_reference < s.take_profit
    keys = {c.key for c in s.conditions}
    assert {"zone_retest", "close_beyond_zone", "rejection_close_position", "directional_candle",
            "rsi_pullback_band", "macd_turn"} <= keys
    # The resistance overhead is reported even when the room filter is off.
    assert s.plan["opposing_zone"] is not None and s.plan["room_r"] < 1.5


def test_pullback_rejected_by_default_room_filter_and_watchlist_without_rejection_candle():
    ev, _ = _eval(frame(pullback_bars()), rsi=50.0, ema_fast=101.3, ema_mid=101.0, ema_slow=100.5)
    assert "opposing_zone_too_close" in [r["code"] for r in _setup(ev, "pullback").reasons]
    # Bearish candle at support: touched, but no confirmed bullish rejection -> WATCHLIST.
    ev2, _ = _eval(frame(pullback_bars(last=(101.2, 101.3, 100.1, 100.4))), rsi=50.0, ema_fast=101.3,
                   ema_mid=101.0, ema_slow=100.3)
    s2 = _setup(ev2, "pullback")
    assert s2.status == "WATCHLIST" and s2.reasons[0]["code"] == "rejection_not_confirmed"


def test_pullback_momentum_failure_is_rejected():
    ev, _ = _eval(frame(pullback_bars()), params=_pb_params(min_rr=0), rsi=35.0, ema_fast=101.3, ema_mid=101.0,
                  ema_slow=100.5)
    assert "failed_rsi_pullback_band" in [r["code"] for r in _setup(ev, "pullback").reasons]


def test_strategies_can_be_disabled_independently():
    only_pb = P.model_copy(update={"breakout": P.breakout.model_copy(update={"enabled": False})})
    ev, _ = _eval(frame(breakout_bars()), params=only_pb)
    assert all(s.strategy == "pullback" for s in ev.setups)
    with pytest.raises(ValueError):
        StrategyParams(breakout={"enabled": False}, pullback={"enabled": False})


def test_setup_evaluation_has_no_look_ahead_on_random_data():
    from tests.conftest import make_candles
    df = make_candles(900, seed=11, drift=0.0003)
    f_full = compute_features(df, "15m", P)
    for i in (500, 650, 800):
        f_trunc = compute_features(df.iloc[: i + 1].reset_index(drop=True), "15m", P)
        a, b = evaluate_at(f_full, i, P).to_dict(), evaluate_at(f_trunc, i, P).to_dict()
        assert a == b
