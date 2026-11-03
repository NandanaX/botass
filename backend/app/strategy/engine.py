"""Deterministic trend-following breakout strategy.

``evaluate_at`` is the single source of truth for signal decisions. Both the live
scanner (evaluating the last closed candle) and the backtester (evaluating every
historical candle) call it with the same ``Features`` arrays. It only reads indices
``<= i`` and higher-timeframe candles that had closed by bar ``i``'s close.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from app.indicators.structure import structure_label
from app.indicators.zones import Zone
from app.risk.levels import Direction
from app.risk.sizing import InstrumentRules, SizingConfig
from app.strategy.features import Features
from app.strategy.params import StrategyParams
from app.strategy.setups import (
    Candidate,
    Check,
    detect_breakout,
    detect_pullback,
    htf_index,
    qualify,
    reasoning_text,
    zones_at,
)
from app.timeutil import tf_seconds


@dataclass(slots=True)
class ConditionCheck:
    key: str
    label: str
    passed: bool
    detail: str


@dataclass(slots=True)
class HTFState:
    timeframe: str
    trend: str
    candle_close_time: str
    close: float
    ema_fast: float
    ema_mid: float
    ema_slow: float


@dataclass(slots=True)
class ScoreComponent:
    key: str
    label: str
    points: float
    max_points: float
    evaluated: bool
    reason: str


@dataclass(slots=True)
class SignalSetup:
    """One entry candidate. ``status`` is QUALIFIED, WATCHLIST or REJECTED (see app.strategy.setups).

    Price fields are ``None`` for WATCHLIST candidates (no trigger yet, so no plan)."""

    direction: Direction
    strategy: str  # breakout | pullback
    status: str
    entry_reference: float | None
    stop_loss: float | None
    take_profit: float | None  # primary target
    stop_distance: float | None
    risk_reward: float | None
    atr: float
    score: int
    score_components: list[ScoreComponent]
    mtf_confirmed: bool | None
    mtf_details: list[HTFState]
    conditions: list[Check]
    explanation: str
    strategy_name: str
    strategy_version: str
    reasons: list[dict[str, str]] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Evaluation:
    timeframe: str
    index: int
    ready: bool
    open_time: str
    close_time: str
    close: float
    trend: str = "NEUTRAL"
    ema_alignment: str = "MIXED"
    macd_state: str = "FLAT"
    structure: str = "insufficient swings"
    indicators: dict[str, float | None] = field(default_factory=dict)
    long_checks: list[ConditionCheck] = field(default_factory=list)
    short_checks: list[ConditionCheck] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    setup: SignalSetup | None = None  # best QUALIFIED candidate
    setups: list[SignalSetup] = field(default_factory=list)  # every candidate, any status
    zones: list[dict[str, Any]] = field(default_factory=list)
    htf_zones: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    rejection: str | None = None
    not_ready_reason: str | None = None

    @property
    def long_passed(self) -> int:
        return sum(c.passed for c in self.long_checks)

    @property
    def short_passed(self) -> int:
        return sum(c.passed for c in self.short_checks)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["long_passed"] = self.long_passed
        d["short_passed"] = self.short_passed
        return d


def _num(x: float) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), 8)


def _fmt(x: float) -> str:
    if not math.isfinite(x):
        return "n/a"
    if abs(x) >= 1000:
        return f"{x:,.2f}"
    if abs(x) >= 1:
        return f"{x:,.4f}".rstrip("0").rstrip(".")
    return f"{x:.6g}"


def _iso(ns: int) -> str:
    return np.datetime_as_string(np.datetime64(int(ns), "ns"), unit="s") + "Z"


def classify_trend(ema_fast: float, ema_mid: float, ema_slow: float) -> str:
    if not all(math.isfinite(v) for v in (ema_fast, ema_mid, ema_slow)):
        return "UNKNOWN"
    if ema_fast > ema_mid > ema_slow:
        return "BULLISH"
    if ema_fast < ema_mid < ema_slow:
        return "BEARISH"
    return "NEUTRAL"


def htf_states_at(
    htf_features: dict[str, Features], decision_close_ns: int, signal_timeframe: str, params: StrategyParams
) -> list[HTFState]:
    """Trend state of each configured higher timeframe using only HTF candles whose close
    time is <= the decision time (the close of the signal candle)."""
    states: list[HTFState] = []
    base = tf_seconds(signal_timeframe)
    for tf in params.mtf.timeframes:
        if tf_seconds(tf) <= base or tf not in htf_features:
            continue
        f = htf_features[tf]
        idx = int(np.searchsorted(f.close_time, decision_close_ns, side="right")) - 1
        if idx < 0:
            states.append(HTFState(tf, "UNKNOWN", "", math.nan, math.nan, math.nan, math.nan))
            continue
        states.append(
            HTFState(
                timeframe=tf,
                trend=classify_trend(f.ema_fast[idx], f.ema_mid[idx], f.ema_slow[idx]),
                candle_close_time=_iso(f.close_time[idx]),
                close=float(f.close[idx]),
                ema_fast=float(f.ema_fast[idx]),
                ema_mid=float(f.ema_mid[idx]),
                ema_slow=float(f.ema_slow[idx]),
            )
        )
    return states


def indicator_snapshot(f: Features, i: int) -> dict[str, float | None]:
    s = f.structure
    return {
        "open": _num(f.open[i]),
        "high": _num(f.high[i]),
        "low": _num(f.low[i]),
        "close": _num(f.close[i]),
        "volume": _num(f.volume[i]),
        "ema_fast": _num(f.ema_fast[i]),
        "ema_mid": _num(f.ema_mid[i]),
        "ema_slow": _num(f.ema_slow[i]),
        "sma_long": _num(f.sma_long[i]),
        "rsi": _num(f.rsi[i]),
        "macd": _num(f.macd[i]),
        "macd_signal": _num(f.macd_signal[i]),
        "macd_hist": _num(f.macd_hist[i]),
        "atr": _num(f.atr[i]),
        "bb_upper": _num(f.bb_upper[i]),
        "bb_mid": _num(f.bb_mid[i]),
        "bb_lower": _num(f.bb_lower[i]),
        "volume_ma": _num(f.volume_ma[i]),
        "rvol": _num(f.rvol[i]),
        "prior_high": _num(f.prior_high[i]),
        "prior_low": _num(f.prior_low[i]),
        "last_swing_high": _num(s.last_swing_high[i]),
        "last_swing_low": _num(s.last_swing_low[i]),
    }


def _required_ready(f: Features, i: int) -> bool:
    arrays = (f.ema_slow, f.sma_long, f.rsi, f.macd_hist, f.atr, f.prior_high, f.prior_low)
    return all(math.isfinite(a[i]) for a in arrays) and f.atr[i] > 0


def _long_checks(f: Features, i: int, p: StrategyParams) -> list[ConditionCheck]:
    return [
        ConditionCheck("ema_fast_above_mid", f"EMA{p.ema_fast} > EMA{p.ema_mid}", bool(f.ema_fast[i] > f.ema_mid[i]),
                       f"{_fmt(f.ema_fast[i])} vs {_fmt(f.ema_mid[i])}"),
        ConditionCheck("ema_mid_above_slow", f"EMA{p.ema_mid} > EMA{p.ema_slow}", bool(f.ema_mid[i] > f.ema_slow[i]),
                       f"{_fmt(f.ema_mid[i])} vs {_fmt(f.ema_slow[i])}"),
        ConditionCheck("rsi_in_long_band", f"RSI between {p.rsi_long_min:g} and {p.rsi_long_max:g}",
                       bool(p.rsi_long_min <= f.rsi[i] <= p.rsi_long_max), f"RSI {f.rsi[i]:.2f}"),
        ConditionCheck("macd_hist_positive", "MACD histogram > 0", bool(f.macd_hist[i] > 0),
                       f"hist {_fmt(f.macd_hist[i])}"),
        ConditionCheck("breakout", f"Close > highest high of previous {p.breakout_lookback} candles",
                       bool(f.close[i] > f.prior_high[i]), f"close {_fmt(f.close[i])} vs {_fmt(f.prior_high[i])}"),
    ]


def _short_checks(f: Features, i: int, p: StrategyParams) -> list[ConditionCheck]:
    return [
        ConditionCheck("ema_fast_below_mid", f"EMA{p.ema_fast} < EMA{p.ema_mid}", bool(f.ema_fast[i] < f.ema_mid[i]),
                       f"{_fmt(f.ema_fast[i])} vs {_fmt(f.ema_mid[i])}"),
        ConditionCheck("ema_mid_below_slow", f"EMA{p.ema_mid} < EMA{p.ema_slow}", bool(f.ema_mid[i] < f.ema_slow[i]),
                       f"{_fmt(f.ema_mid[i])} vs {_fmt(f.ema_slow[i])}"),
        ConditionCheck("rsi_in_short_band", f"RSI between {p.rsi_short_min:g} and {p.rsi_short_max:g}",
                       bool(p.rsi_short_min <= f.rsi[i] <= p.rsi_short_max), f"RSI {f.rsi[i]:.2f}"),
        ConditionCheck("macd_hist_negative", "MACD histogram < 0", bool(f.macd_hist[i] < 0),
                       f"hist {_fmt(f.macd_hist[i])}"),
        ConditionCheck("breakdown", f"Close < lowest low of previous {p.breakout_lookback} candles",
                       bool(f.close[i] < f.prior_low[i]), f"close {_fmt(f.close[i])} vs {_fmt(f.prior_low[i])}"),
    ]


def _observations(f: Features, i: int, trend: str) -> list[str]:
    obs: list[str] = []
    s = f.structure
    if f.close[i] > f.prior_high[i]:
        obs.append("Breakout above prior range high")
    if f.close[i] < f.prior_low[i]:
        obs.append("Breakdown below prior range low")
    if trend == "BULLISH" and f.low[i] <= f.ema_fast[i] < f.close[i]:
        obs.append("Bullish trend continuation: pullback to fast EMA reclaimed")
    if trend == "BEARISH" and f.high[i] >= f.ema_fast[i] > f.close[i]:
        obs.append("Bearish trend continuation: rally to fast EMA rejected")
    if trend == "BEARISH" and math.isfinite(s.last_swing_high[i]) and f.close[i] > s.last_swing_high[i]:
        obs.append("Potential bullish reversal: close above last confirmed swing high against bearish EMA trend")
    if trend == "BULLISH" and math.isfinite(s.last_swing_low[i]) and f.close[i] < s.last_swing_low[i]:
        obs.append("Potential bearish reversal: close below last confirmed swing low against bullish EMA trend")
    if s.structure[i] == 1:
        obs.append("Market structure: higher highs and higher lows")
    elif s.structure[i] == -1:
        obs.append("Market structure: lower highs and lower lows")
    return obs


def score_setup(
    f: Features, i: int, p: StrategyParams, direction: Direction, htf: list[HTFState],
    confirmation: tuple[float, str] | None = None, zone_strength: float | None = None,
    room_r: float | None = None, room_evaluated: bool = False,
) -> tuple[int, list[ScoreComponent]]:
    """Rule-agreement score in [0, 100]. This is NOT a probability of success."""
    w = p.weights
    long = direction == "LONG"
    sign = 1.0 if long else -1.0
    comps: list[ScoreComponent] = []

    # Trend alignment
    stack = (f.ema_fast[i] > f.ema_mid[i] > f.ema_slow[i]) if long else (f.ema_fast[i] < f.ema_mid[i] < f.ema_slow[i])
    above_sma = (f.close[i] > f.sma_long[i]) if long else (f.close[i] < f.sma_long[i])
    struct_ok = f.structure.structure[i] == (1 if long else -1)
    frac = 0.6 * stack + 0.2 * above_sma + 0.2 * struct_ok
    comps.append(ScoreComponent("trend", "Trend alignment", w.trend * frac, w.trend, True,
                                f"EMA stack {'aligned' if stack else 'not aligned'}; close "
                                f"{'beyond' if above_sma else 'not beyond'} SMA{p.sma_long}; structure "
                                f"{'confirms' if struct_ok else 'does not confirm'} ({'HH/HL' if long else 'LH/LL'})"))

    # Momentum
    lo, hi = (p.rsi_long_min, p.rsi_long_max) if long else (p.rsi_short_min, p.rsi_short_max)
    rsi_ok = lo <= f.rsi[i] <= hi
    hist_ok = sign * f.macd_hist[i] > 0
    rising = i > 0 and math.isfinite(f.macd_hist[i - 1]) and sign * (f.macd_hist[i] - f.macd_hist[i - 1]) > 0
    frac = 0.4 * rsi_ok + 0.3 * hist_ok + 0.3 * rising
    comps.append(ScoreComponent("momentum", "Momentum confirmation", w.momentum * frac, w.momentum, True,
                                f"RSI {f.rsi[i]:.1f} {'inside' if rsi_ok else 'outside'} {lo:g}-{hi:g}; MACD histogram "
                                f"{'agrees' if hist_ok else 'disagrees'} and is {'expanding' if rising else 'not expanding'}"))

    # Entry confirmation: breakout distance/body, or rejection-candle quality for pullbacks.
    if confirmation is None:
        level = f.prior_high[i] if long else f.prior_low[i]
        dist_atr = sign * (f.close[i] - level) / f.atr[i]
        rng = f.high[i] - f.low[i]
        body = sign * (f.close[i] - f.open[i]) / rng if rng > 0 else 0.0
        frac = 0.6 * float(np.clip(dist_atr / 0.5, 0, 1)) + 0.4 * float(np.clip(body / 0.6, 0, 1))
        confirmation = (frac, f"close {dist_atr:.2f} ATR beyond the {p.breakout_lookback}-bar level; directional "
                              f"body {max(body, 0) * 100:.0f}% of candle range")
    comps.append(ScoreComponent("breakout", "Entry confirmation", w.breakout * confirmation[0], w.breakout, True,
                                confirmation[1]))

    # Relative volume
    rvol = f.rvol[i]
    if math.isfinite(rvol):
        frac = float(np.clip((rvol - 0.8) / 0.7, 0, 1))
        comps.append(ScoreComponent("volume", "Relative volume", w.volume * frac, w.volume, True,
                                    f"volume is {rvol:.2f}x the prior {p.volume_ma_period}-bar average"))
    else:
        comps.append(ScoreComponent("volume", "Relative volume", 0, w.volume, False, "volume baseline unavailable"))

    # Higher-timeframe agreement
    want = "BULLISH" if long else "BEARISH"
    if p.mtf.enabled and htf:
        agree = sum(s.trend == want for s in htf)
        comps.append(ScoreComponent("higher_timeframe", "Higher-timeframe agreement",
                                    w.higher_timeframe * agree / len(htf), w.higher_timeframe, True,
                                    ", ".join(f"{s.timeframe} {s.trend.lower()}" for s in htf)))
    else:
        reason = "multi-timeframe confirmation disabled" if not p.mtf.enabled else "no higher timeframe available"
        comps.append(ScoreComponent("higher_timeframe", "Higher-timeframe agreement", 0, w.higher_timeframe, False,
                                    reason))

    # Volatility conditions
    atr_pct = 100 * f.atr[i] / f.close[i]
    vol_ok = p.atr_pct_min <= atr_pct <= p.atr_pct_max
    comps.append(ScoreComponent("volatility", "Volatility conditions", w.volatility * vol_ok, w.volatility, True,
                                f"ATR is {atr_pct:.2f}% of price ({'within' if vol_ok else 'outside'} "
                                f"{p.atr_pct_min:g}-{p.atr_pct_max:g}%)"))

    # Zone strength and reward room (only for zone-based setups).
    if zone_strength is not None:
        comps.append(ScoreComponent("zone", "Zone strength", w.zone * zone_strength / 100, w.zone, True,
                                    f"trigger zone strength {zone_strength:.0f}/100"))
    else:
        comps.append(ScoreComponent("zone", "Zone strength", 0, w.zone, False, "no zone used"))
    if room_evaluated:
        if room_r is None:
            comps.append(ScoreComponent("room", "Reward room", w.room, w.room, True,
                                        "no opposing zone found in the analysed range"))
        else:
            frac = float(np.clip(room_r / 3.0, 0, 1))
            comps.append(ScoreComponent("room", "Reward room", w.room * frac, w.room, True,
                                        f"nearest opposing zone is {room_r:.1f}R away"))
    else:
        comps.append(ScoreComponent("room", "Reward room", 0, w.room, False, "not evaluated"))

    total_max = sum(c.max_points for c in comps if c.evaluated)
    total = sum(c.points for c in comps if c.evaluated)
    score = int(round(100 * total / total_max)) if total_max > 0 else 0
    for c in comps:
        c.points = round(c.points, 2)
    return max(0, min(100, score)), comps


def _htf_text(htf: list[HTFState], mtf_confirmed: bool | None) -> str:
    if not htf:
        return ""
    text = "Higher timeframes: " + ", ".join(f"{s.timeframe} {s.trend.lower()}" for s in htf) + "."
    return text + (" Higher-timeframe trend agrees." if mtf_confirmed
                   else " Higher-timeframe trend does NOT fully agree.")


def _candle(f: Features, i: int) -> dict[str, Any]:
    return {"open_time": _iso(f.open_time[i]), "close_time": _iso(f.close_time[i]), "open": float(f.open[i]),
            "high": float(f.high[i]), "low": float(f.low[i]), "close": float(f.close[i]),
            "volume": float(f.volume[i]), "rvol": _num(f.rvol[i])}


def _build_setup(cand: Candidate, f: Features, i: int, p: StrategyParams, htf: list[HTFState],
                 opposing_pool: list[Zone], sizing: SizingConfig | None, rules: InstrumentRules | None
                 ) -> SignalSetup:
    long = cand.direction == "LONG"
    mtf_confirmed: bool | None = None
    if p.mtf.enabled and htf:
        want = "BULLISH" if long else "BEARISH"
        mtf_confirmed = all(st.trend == want for st in htf)
    plan = qualify(cand, f, i, p, opposing_pool, mtf_confirmed, sizing, rules)
    triggered = cand.triggered
    if triggered:
        zone_based = cand.zone is not None or cand.strategy == "pullback"
        score, comps = score_setup(
            f, i, p, cand.direction, htf,
            confirmation=(cand.confirmation_frac, cand.confirmation_reason) if zone_based else None,
            zone_strength=cand.zone.strength if cand.zone is not None else None,
            room_r=plan.room_r, room_evaluated=True,
        )
        explanation = reasoning_text(cand, plan, _htf_text(htf, mtf_confirmed))
    else:
        score, comps = 0, []
        explanation = plan.reasons[0]["message"] if plan.reasons else "Setup developing."
    stop = plan.stop or {}
    primary = plan.targets[plan.primary_target_index] if plan.targets else None
    return SignalSetup(
        direction=cand.direction,
        strategy=cand.strategy,
        status=plan.status,
        entry_reference=plan.entry_reference,
        stop_loss=stop.get("price"),
        take_profit=primary["price"] if primary else None,
        stop_distance=stop.get("distance"),
        risk_reward=plan.risk_reward,
        atr=float(f.atr[i]),
        score=score,
        score_components=comps,
        mtf_confirmed=mtf_confirmed,
        mtf_details=htf,
        conditions=cand.checks,
        explanation=explanation,
        strategy_name=cand.strategy_name,
        strategy_version=p.version,
        reasons=plan.reasons,
        plan={
            "status": plan.status,
            "strategy": cand.strategy,
            "zone": cand.zone.to_dict() if cand.zone else None,
            "entry_zone": list(cand.entry_zone) if cand.entry_zone else None,
            "entry_note": ("Entry reference is the confirmation candle close; the hypothetical fill is the next "
                           "candle open. A detected signal is not an executed position."),
            "confirmation_candle": _candle(f, i) if triggered else None,
            "stop": plan.stop,
            "targets": plan.targets,
            "primary_target_index": plan.primary_target_index,
            "risk_reward": plan.risk_reward,
            "net_risk_reward": plan.net_risk_reward,
            "opposing_zone": plan.opposing_zone,
            "room_r": plan.room_r,
            "sizing": plan.sizing,
            "invalidation": plan.invalidation,
            "warnings": plan.warnings,
            "partial_exits": p.targets.partial_exits,
        },
    )


STATUS_RANK = {"QUALIFIED": 0, "REJECTED": 1, "WATCHLIST": 2}


def evaluate_at(
    f: Features, i: int, p: StrategyParams, htf_features: dict[str, Features] | None = None,
    sizing: SizingConfig | None = None, rules: InstrumentRules | None = None, context: bool = True,
    symbol: str | None = None,
) -> Evaluation:
    """Evaluate both strategies at closed candle ``i`` using information available at its close.

    ``context=False`` (backtests) skips work that is only needed for display: zone lists
    are then computed only when a setup actually triggers."""
    if i < 0 or i >= len(f):
        raise IndexError("index outside feature range")
    ev = Evaluation(
        timeframe=f.timeframe,
        index=i,
        ready=False,
        open_time=_iso(f.open_time[i]),
        close_time=_iso(f.close_time[i]),
        close=float(f.close[i]),
    )
    ev.indicators = indicator_snapshot(f, i)
    if i + 1 < p.warmup_bars or not _required_ready(f, i):
        ev.not_ready_reason = f"insufficient history: need {p.warmup_bars} closed candles"
        return ev
    ev.ready = True
    ev.trend = classify_trend(f.ema_fast[i], f.ema_mid[i], f.ema_slow[i])
    ev.ema_alignment = {"BULLISH": "BULLISH_STACK", "BEARISH": "BEARISH_STACK"}.get(ev.trend, "MIXED")
    ev.macd_state = "POSITIVE" if f.macd_hist[i] > 0 else "NEGATIVE" if f.macd_hist[i] < 0 else "FLAT"
    s = f.structure
    ev.structure = structure_label(
        int(s.structure[i]), s.last_swing_high[i], s.prev_swing_high[i], s.last_swing_low[i], s.prev_swing_low[i]
    )
    ev.long_checks = _long_checks(f, i, p)
    ev.short_checks = _short_checks(f, i, p)
    ev.observations = _observations(f, i, ev.trend)

    decision_ns = int(f.close_time[i])
    htf_feats = htf_features or {}
    base = tf_seconds(f.timeframe)

    def htf_zone_lists() -> dict[str, list[Zone]]:
        out: dict[str, list[Zone]] = {}
        for tf in p.zones.htf_timeframes:
            hf = htf_feats.get(tf)
            if hf is None or tf_seconds(tf) <= base:
                continue
            j = htf_index(hf, decision_ns)  # only HTF candles closed by this decision
            if j >= 0:
                out[tf] = zones_at(hf, j, p, symbol, p.zones.max_zones_per_side)
        return out

    zones_now: list[Zone] | None = None
    htf_z: dict[str, list[Zone]] | None = None
    if context:
        zones_now = zones_at(f, i, p, symbol, p.zones.max_zones_per_side)
        htf_z = htf_zone_lists()
        ev.zones = [z.to_dict() for z in zones_now]
        ev.htf_zones = {tf: [z.to_dict() for z in zs] for tf, zs in htf_z.items()}

    trend_long = bool(f.ema_fast[i] > f.ema_mid[i] > f.ema_slow[i])
    trend_short = bool(f.ema_fast[i] < f.ema_mid[i] < f.ema_slow[i])
    if not (trend_long or trend_short) or i < 1:
        return ev
    needs_zones = p.pullback.enabled or (p.breakout.enabled and p.breakout.level_source == "zone")
    # Trigger zones: every zone as known at the close of the previous candle.
    trigger = zones_at(f, i - 1, p, symbol, None) if needs_zones else []
    cands: list[Candidate] = []
    for direction, ok in (("LONG", trend_long), ("SHORT", trend_short)):
        if not ok:
            continue
        if p.breakout.enabled:
            c = detect_breakout(f, i, p, direction, trigger)
            if c is not None:
                cands.append(c)
        if p.pullback.enabled:
            c = detect_pullback(f, i, p, direction, trigger)
            if c is not None:
                cands.append(c)
    if not cands or (not context and not any(c.triggered for c in cands)):
        return ev
    if zones_now is None:
        zones_now = zones_at(f, i, p, symbol, None)
    if htf_z is None:
        htf_z = htf_zone_lists()
    pool = list(zones_now) + [z for zs in htf_z.values() for z in zs]
    htf = htf_states_at(htf_feats, decision_ns, f.timeframe, p) if p.mtf.enabled and htf_features else []
    setups = [_build_setup(c, f, i, p, htf, pool, sizing, rules) for c in cands]
    setups.sort(key=lambda st: (STATUS_RANK.get(st.status, 9), -st.score))
    ev.setups = setups
    qualified = [st for st in setups if st.status == "QUALIFIED"]
    if qualified:
        ev.setup = qualified[0]
    else:
        rejected = [st for st in setups if st.status == "REJECTED"]
        if rejected:
            ev.rejection = rejected[0].reasons[0]["message"] if rejected[0].reasons else "setup rejected"
    return ev
