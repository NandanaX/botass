"""Entry-setup detection (Strategy A: breakout, Strategy B: pullback/retest) and the
setup qualification engine.

Both strategies read only bar ``i`` and earlier, plus zones as they were known at the
close of bar ``i - 1`` (the *trigger* zones). That way a candle that breaks a zone is
judged against the zone as it stood before that candle closed. Opposing zones and the
displayed zones are those known at the close of bar ``i``.

A candidate gets exactly one status:

* ``QUALIFIED``: every mandatory entry condition passed and every risk constraint holds.
* ``WATCHLIST``: trend is aligned and price is near or at a zone, but the trigger
  (breakout close / rejection candle) has not been confirmed yet.
* ``REJECTED``: the trigger happened, but a mandatory condition or risk rule failed. The
  machine-readable ``reasons`` say which.
* ``EXPIRED``: set later on a stored signal whose hypothetical entry never became valid.

No setup is forced. When nothing fits, the list of candidates is empty.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np

from app.indicators.zones import Zone, detect_zones
from app.risk.sizing import InstrumentRules, SizingConfig, round_price, size_position
from app.risk.stops import compute_stop
from app.risk.targets import compute_targets, find_opposing_zone
from app.strategy.features import Features
from app.strategy.params import BREAKOUT_NAME, PULLBACK_NAME, StrategyParams

Direction = Literal["LONG", "SHORT"]
SetupStatus = Literal["QUALIFIED", "WATCHLIST", "REJECTED", "EXPIRED"]


@dataclass(slots=True)
class Check:
    key: str
    label: str
    passed: bool
    detail: str
    mandatory: bool = True


@dataclass(slots=True)
class Candidate:
    strategy: Literal["breakout", "pullback"]
    direction: Direction
    triggered: bool
    checks: list[Check]
    zone: Zone | None
    structural_level: float | None
    structural_label: str
    entry_zone: tuple[float, float] | None
    watch_reason: dict[str, str] | None = None
    confirmation_frac: float = 0.0
    confirmation_reason: str = ""

    @property
    def strategy_name(self) -> str:
        return BREAKOUT_NAME if self.strategy == "breakout" else PULLBACK_NAME


def fmt(x: float | None) -> str:
    if x is None or not math.isfinite(x):
        return "n/a"
    if abs(x) >= 1000:
        return f"{x:,.2f}"
    if abs(x) >= 1:
        return f"{x:,.4f}".rstrip("0").rstrip(".")
    return f"{x:.6g}"


def zones_at(f: Features, i: int, p: StrategyParams, symbol: str | None = None,
             max_per_side: int | None = None) -> list[Zone]:
    z = p.zones
    if i < 0 or not math.isfinite(f.atr[i]):
        return []
    return detect_zones(
        timeframe=f.timeframe, open_time=f.open_time, close=f.close, pivots=f.pivots, atr_value=float(f.atr[i]),
        at_index=i, lookback=z.lookback, cluster_atr_mult=z.cluster_atr_mult, min_width_atr=z.min_width_atr,
        min_tests=z.min_tests, break_buffer_atr=z.break_buffer_atr,
        invalidate_after_crossings=z.invalidate_after_crossings,
        max_zones_per_side=max_per_side, symbol=symbol,
    )


def htf_index(f: Features, decision_close_ns: int) -> int:
    return int(np.searchsorted(f.close_time, decision_close_ns, side="right")) - 1


def _trend_checks(f: Features, i: int, p: StrategyParams, long: bool) -> list[Check]:
    if long:
        return [
            Check("ema_fast_above_mid", f"EMA{p.ema_fast} > EMA{p.ema_mid}", bool(f.ema_fast[i] > f.ema_mid[i]),
                  f"{fmt(f.ema_fast[i])} vs {fmt(f.ema_mid[i])}"),
            Check("ema_mid_above_slow", f"EMA{p.ema_mid} > EMA{p.ema_slow}", bool(f.ema_mid[i] > f.ema_slow[i]),
                  f"{fmt(f.ema_mid[i])} vs {fmt(f.ema_slow[i])}"),
        ]
    return [
        Check("ema_fast_below_mid", f"EMA{p.ema_fast} < EMA{p.ema_mid}", bool(f.ema_fast[i] < f.ema_mid[i]),
              f"{fmt(f.ema_fast[i])} vs {fmt(f.ema_mid[i])}"),
        Check("ema_mid_below_slow", f"EMA{p.ema_mid} < EMA{p.ema_slow}", bool(f.ema_mid[i] < f.ema_slow[i]),
              f"{fmt(f.ema_mid[i])} vs {fmt(f.ema_slow[i])}"),
    ]


def _body_ratio(f: Features, i: int, long: bool) -> float:
    rng = f.high[i] - f.low[i]
    if rng <= 0:
        return 0.0
    return float((f.close[i] - f.open[i]) / rng * (1 if long else -1))


def detect_breakout(f: Features, i: int, p: StrategyParams, direction: Direction,
                    trigger_zones: list[Zone]) -> Candidate | None:
    bp = p.breakout
    long = direction == "LONG"
    atr = float(f.atr[i])
    trend = _trend_checks(f, i, p, long)
    if not all(c.passed for c in trend):
        return None
    close = float(f.close[i])
    buf = p.zones.break_buffer_atr * atr
    zone: Zone | None = None
    if bp.level_source == "range":
        level = float(f.prior_high[i] if long else f.prior_low[i])
        broke = close > level if long else close < level
        near = level
        far_struct = float(f.structure.last_swing_low[i] if long else f.structure.last_swing_high[i])
        struct_level = far_struct if math.isfinite(far_struct) else None
        struct_label = "last confirmed swing low" if long else "last confirmed swing high"
        break_label = (f"Close > highest high of previous {p.breakout_lookback} candles" if long
                       else f"Close < lowest low of previous {p.breakout_lookback} candles")
        break_detail = f"close {fmt(close)} vs {fmt(level)}"
        key = "breakout" if long else "breakdown"
    else:
        want = "RESISTANCE" if long else "SUPPORT"
        usable = [z for z in trigger_zones if z.zone_type == want and z.status != "INVALIDATED"]
        broken = [z for z in usable if (close > z.upper + buf if long else close < z.lower - buf)]
        if not broken:
            # Distance to the boundary that must be closed through (price may already be inside the zone).
            def gap(z: Zone) -> float:
                return (z.upper - close) if long else (close - z.lower)
            ahead = sorted((z for z in usable if gap(z) >= 0), key=gap)
            if ahead and gap(ahead[0]) <= bp.watch_distance_atr * atr:
                z = ahead[0]
                return Candidate("breakout", direction, False, trend, z, None, "", None, {
                    "code": "awaiting_breakout_close",
                    "message": (f"Price is within {bp.watch_distance_atr:g} ATR of the {z.timeframe} "
                                f"{'resistance' if long else 'support'} zone {fmt(z.lower)}-{fmt(z.upper)}; "
                                f"waiting for a candle to close {'above' if long else 'below'} it."),
                })
            return None
        zone = max(broken, key=lambda z: z.upper) if long else min(broken, key=lambda z: z.lower)
        near = zone.upper if long else zone.lower
        broke = True
        struct_level = zone.lower if long else zone.upper
        struct_label = ("lower boundary of the broken resistance zone" if long
                        else "upper boundary of the broken support zone")
        break_label = (f"Close above {zone.timeframe} resistance zone {fmt(zone.lower)}-{fmt(zone.upper)}" if long
                       else f"Close below {zone.timeframe} support zone {fmt(zone.lower)}-{fmt(zone.upper)}")
        break_detail = f"close {fmt(close)}, {abs(close - near) / atr:.2f} ATR beyond the zone (min {p.zones.break_buffer_atr:g})"
        key = "zone_breakout" if long else "zone_breakdown"
    if not broke:
        return None
    lo, hi = (p.rsi_long_min, p.rsi_long_max) if long else (p.rsi_short_min, p.rsi_short_max)
    checks = list(trend)
    checks += [
        Check("rsi_in_long_band" if long else "rsi_in_short_band", f"RSI between {lo:g} and {hi:g}",
              bool(lo <= f.rsi[i] <= hi), f"RSI {f.rsi[i]:.2f}"),
        Check("macd_hist_positive" if long else "macd_hist_negative",
              "MACD histogram > 0" if long else "MACD histogram < 0",
              bool(f.macd_hist[i] > 0 if long else f.macd_hist[i] < 0), f"hist {fmt(f.macd_hist[i])}"),
        Check(key, break_label, True, break_detail),
    ]
    if bp.min_rvol > 0:
        rv = f.rvol[i]
        checks.append(Check("volume_confirmation", f"Relative volume >= {bp.min_rvol:g}x",
                            bool(math.isfinite(rv) and rv >= bp.min_rvol),
                            f"{rv:.2f}x the prior {p.volume_ma_period}-bar average" if math.isfinite(rv)
                            else "volume baseline unavailable"))
    body = _body_ratio(f, i, long)
    if bp.min_body_ratio > 0:
        checks.append(Check("candle_body", f"Directional body >= {bp.min_body_ratio * 100:.0f}% of range",
                            body >= bp.min_body_ratio, f"{max(body, 0) * 100:.0f}% of candle range"))
    ext = abs(close - near) / atr
    if bp.max_extension_atr > 0:
        checks.append(Check("not_overextended", f"Close within {bp.max_extension_atr:g} ATR of the broken level",
                            ext <= bp.max_extension_atr, f"{ext:.2f} ATR beyond"))
    entry_zone = (near, close) if long else (close, near)
    frac = 0.6 * float(np.clip(ext / 0.5, 0, 1)) + 0.4 * float(np.clip(body / 0.6, 0, 1))
    reason = (f"close {ext:.2f} ATR beyond the broken level; directional body "
              f"{max(body, 0) * 100:.0f}% of candle range")
    return Candidate("breakout", direction, True, checks, zone, struct_level, struct_label, entry_zone,
                     confirmation_frac=frac, confirmation_reason=reason)


def detect_pullback(f: Features, i: int, p: StrategyParams, direction: Direction,
                    trigger_zones: list[Zone]) -> Candidate | None:
    pp = p.pullback
    long = direction == "LONG"
    atr = float(f.atr[i])
    trend = _trend_checks(f, i, p, long)
    trend.append(Check("close_beyond_slow_ema", f"Close {'above' if long else 'below'} EMA{p.ema_slow}",
                       bool(f.close[i] > f.ema_slow[i] if long else f.close[i] < f.ema_slow[i]),
                       f"{fmt(f.close[i])} vs {fmt(f.ema_slow[i])}"))
    if not all(c.passed for c in trend):
        return None
    o, h, lo_, c = float(f.open[i]), float(f.high[i]), float(f.low[i]), float(f.close[i])
    want = "SUPPORT" if long else "RESISTANCE"
    usable = [z for z in trigger_zones if z.zone_type == want and z.status != "INVALIDATED"]
    tol, pierce = pp.touch_tolerance_atr * atr, pp.max_pierce_atr * atr
    if long:
        touched = [z for z in usable if lo_ <= z.upper + tol and lo_ >= z.lower - pierce and h >= z.lower]
    else:
        touched = [z for z in usable if h >= z.lower - tol and h <= z.upper + pierce and lo_ <= z.upper]
    if not touched:
        ahead = [z for z in usable if (lo_ - z.upper if long else z.lower - h) > 0]
        ahead.sort(key=lambda z: (lo_ - z.upper) if long else (z.lower - h))
        if ahead and ((lo_ - ahead[0].upper) if long else (ahead[0].lower - h)) <= pp.watch_distance_atr * atr:
            z = ahead[0]
            return Candidate("pullback", direction, False, trend, z, None, "", None, {
                "code": "awaiting_retest",
                "message": (f"Price is pulling back toward the {z.timeframe} {want.lower()} zone "
                            f"{fmt(z.lower)}-{fmt(z.upper)}; waiting for a retest and a confirmed "
                            f"{'bullish' if long else 'bearish'} rejection candle."),
            })
        return None
    zone = max(touched, key=lambda z: z.upper) if long else min(touched, key=lambda z: z.lower)
    rng = h - lo_
    close_pos = ((c - lo_) / rng if long else (h - c) / rng) if rng > 0 else 0.0
    boundary = zone.upper if long else zone.lower
    beyond_ok = (c > boundary if long else c < boundary) if pp.require_close_beyond_zone else \
        (c > zone.lower if long else c < zone.upper)
    confirm = [
        Check("zone_retest", f"Retest of {zone.timeframe} {want.lower()} zone {fmt(zone.lower)}-{fmt(zone.upper)}",
              True, f"{'low' if long else 'high'} {fmt(lo_ if long else h)}"),
        Check("close_beyond_zone",
              f"Close {'above' if long else 'below'} the zone {'upper' if long else 'lower'} boundary"
              if pp.require_close_beyond_zone else "Close back inside or beyond the zone",
              bool(beyond_ok), f"close {fmt(c)} vs {fmt(boundary)}"),
        Check("rejection_close_position",
              f"Close in the {'top' if long else 'bottom'} {(1 - pp.min_close_position) * 100:.0f}% of the candle",
              close_pos >= pp.min_close_position, f"close at {close_pos * 100:.0f}% of the range"),
    ]
    if pp.require_directional_candle:
        confirm.append(Check("directional_candle", "Bullish candle (close > open)" if long
                             else "Bearish candle (close < open)", bool(c > o if long else c < o),
                             f"open {fmt(o)}, close {fmt(c)}"))
    rsi_lo, rsi_hi = (pp.rsi_min_long, p.rsi_long_max) if long else (p.rsi_short_min, pp.rsi_max_short)
    momentum = [Check("rsi_pullback_band", f"RSI between {rsi_lo:g} and {rsi_hi:g}",
                      bool(rsi_lo <= f.rsi[i] <= rsi_hi), f"RSI {f.rsi[i]:.2f}")]
    if pp.require_macd_turn:
        prev = f.macd_hist[i - 1] if i > 0 else math.nan
        turned = math.isfinite(prev) and (f.macd_hist[i] > prev if long else f.macd_hist[i] < prev)
        momentum.append(Check("macd_turn", "MACD histogram turning " + ("up" if long else "down"), bool(turned),
                              f"hist {fmt(f.macd_hist[i])} vs {fmt(prev)}"))
    checks = trend + confirm + momentum
    if not all(ch.passed for ch in confirm):
        failed = ", ".join(ch.label.lower() for ch in confirm if not ch.passed)
        return Candidate("pullback", direction, False, checks, zone, None, "", None, {
            "code": "rejection_not_confirmed",
            "message": (f"Price retested the {zone.timeframe} {want.lower()} zone {fmt(zone.lower)}-"
                        f"{fmt(zone.upper)} but the rejection is not confirmed yet (still missing: {failed})."),
        })
    wick = ((min(o, c) - lo_) if long else (h - max(o, c))) / rng if rng > 0 else 0.0
    frac = 0.5 * float(np.clip(close_pos, 0, 1)) + 0.5 * float(np.clip(wick / 0.5, 0, 1))
    reason = f"close at {close_pos * 100:.0f}% of the candle range; rejection wick {wick * 100:.0f}% of range"
    struct = zone.lower if long else zone.upper
    entry_zone = (min(zone.upper, c), c) if long else (c, max(zone.lower, c))
    return Candidate("pullback", direction, True, checks, zone, struct,
                     "support zone lower boundary" if long else "resistance zone upper boundary", entry_zone,
                     confirmation_frac=frac, confirmation_reason=reason)


@dataclass(slots=True)
class QualifiedPlan:
    status: SetupStatus
    reasons: list[dict[str, str]]
    entry_reference: float | None = None
    stop: dict[str, Any] | None = None
    targets: list[dict[str, Any]] = field(default_factory=list)
    primary_target_index: int = 1
    risk_reward: float | None = None
    net_risk_reward: float | None = None
    opposing_zone: dict[str, Any] | None = None
    room_r: float | None = None
    sizing: dict[str, Any] | None = None
    invalidation: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def qualify(
    cand: Candidate,
    f: Features,
    i: int,
    p: StrategyParams,
    opposing_pool: list[Zone],
    mtf_confirmed: bool | None,
    sizing_cfg: SizingConfig | None,
    rules: InstrumentRules | None,
) -> QualifiedPlan:
    long = cand.direction == "LONG"
    if not cand.triggered:
        return QualifiedPlan("WATCHLIST", [cand.watch_reason] if cand.watch_reason else [])
    reasons = [{"code": f"failed_{c.key}", "message": f"{c.label} not met ({c.detail})"}
               for c in cand.checks if c.mandatory and not c.passed]
    atr = float(f.atr[i])
    tick = rules.tick_size if rules else None
    entry = round_price(float(f.close[i]), tick)
    sp = p.stops
    stop = compute_stop(cand.direction, entry, atr, sp.method, cand.structural_level, sp.atr_multiplier,
                        sp.hybrid_buffer_atr, sp.min_stop_atr, tick, cand.structural_label)
    plan = QualifiedPlan("QUALIFIED", reasons, entry_reference=entry, stop=stop.to_dict(),
                         primary_target_index=p.targets.primary_index)
    if stop.distance_atr > sp.max_stop_atr:
        reasons.append({"code": "stop_too_wide",
                        "message": f"Stop distance is {stop.distance_atr:.2f} ATR, above the maximum "
                                   f"{sp.max_stop_atr:g} ATR."})
    opp = find_opposing_zone(cand.direction, entry, stop.distance, opposing_pool)
    tp = p.targets
    targets = compute_targets(cand.direction, entry, stop.distance, tp.r_multiples, tp.allocation_pct, opp, tick)
    plan.targets = [t.to_dict() for t in targets]
    primary = targets[tp.primary_index]
    plan.risk_reward = abs(primary.price - entry) / stop.distance
    if opp is not None:
        plan.opposing_zone = {**opp.zone, "boundary": opp.boundary, "room_r": opp.room_r}
        plan.room_r = opp.room_r
        q = p.qualification
        if opp.room_r < q.min_rr:
            side = "resistance" if long else "support"
            reasons.append({"code": "opposing_zone_too_close",
                            "message": f"{cand.direction} setup rejected because the nearest {side} "
                                       f"({opp.timeframe} zone {fmt(opp.zone['lower'])}-{fmt(opp.zone['upper'])}) "
                                       f"is too close to the entry, leaving only {opp.room_r:.1f}R potential reward "
                                       f"(minimum {q.min_rr:g}R)."})
        elif any(t.blocked for t in targets):
            plan.warnings.append("Some targets lie beyond the nearest opposing zone; see target notes.")
    if p.mtf.enabled and p.mtf.mode == "filter" and mtf_confirmed is False:
        reasons.append({"code": "htf_trend_disagrees",
                        "message": "Higher-timeframe trend disagrees (multi-timeframe filter mode)."})
    atr_pct = 100 * atr / float(f.close[i])
    if not p.atr_pct_min <= atr_pct <= p.atr_pct_max:
        msg = f"ATR is {atr_pct:.2f}% of price, outside {p.atr_pct_min:g}-{p.atr_pct_max:g}%."
        if p.qualification.reject_outside_volatility:
            reasons.append({"code": "volatility_out_of_range", "message": msg})
        else:
            plan.warnings.append(msg)
    cfg = sizing_cfg or SizingConfig()
    sizing = size_position(cand.direction, entry, stop.price, cfg, rules,
                           [(t.label, t.price, t.r_multiple, t.allocation_pct) for t in targets])
    plan.sizing = sizing.to_dict()
    plan.warnings += sizing.warnings
    for issue in sizing.issues:
        reasons.append({"code": issue["code"], "message": issue["message"]})
    if sizing.targets:
        net = sizing.targets[tp.primary_index].net_rr
        plan.net_risk_reward = net
        if math.isfinite(net) and net < p.qualification.min_net_rr:
            reasons.append({"code": "net_rr_below_minimum",
                            "message": f"After estimated fees and slippage the primary target pays {net:.2f}R, "
                                       f"below the minimum {p.qualification.min_net_rr:g}R."})
    z = cand.zone
    inval = []
    if cand.strategy == "breakout" and z is not None:
        inval.append(f"A candle closes back {'below' if long else 'above'} {fmt(z.lower if long else z.upper)} "
                     f"(failed {'breakout' if long else 'breakdown'}: back through the zone).")
    elif cand.strategy == "breakout":
        inval.append("A candle closes back inside the prior range (failed breakout).")
    elif z is not None:
        inval.append(f"A candle closes {'below' if long else 'above'} the zone's "
                     f"{'lower' if long else 'upper'} boundary {fmt(z.lower if long else z.upper)}.")
    inval += [
        f"Price trades at or {'below' if long else 'above'} the stop-loss {fmt(stop.price)}.",
        "The next candle opens beyond the stop-loss or TP1 (no valid entry; the setup expires).",
    ]
    plan.invalidation = inval
    plan.reasons = reasons
    plan.status = "REJECTED" if reasons else "QUALIFIED"
    return plan


def reasoning_text(cand: Candidate, plan: QualifiedPlan, htf_text: str) -> str:
    long = cand.direction == "LONG"
    passed = [c.label for c in cand.checks if c.passed]
    z = cand.zone
    if cand.strategy == "breakout":
        head = (f"{'Bullish' if long else 'Bearish'} EMA alignment with a confirmed close "
                f"{'above' if long else 'below'} "
                + (f"the {z.timeframe} {'resistance' if long else 'support'} zone {fmt(z.lower)}-{fmt(z.upper)} "
                   f"({z.tests} tests)" if z else "the prior range") + ".")
    else:
        head = (f"{'Bullish' if long else 'Bearish'} EMA alignment; price retested the "
                f"{z.timeframe if z else ''} {'support' if long else 'resistance'} zone "
                f"{fmt(z.lower) if z else ''}-{fmt(z.upper) if z else ''} and closed with a "
                f"{'bullish' if long else 'bearish'} rejection.")
    text = head + " Conditions met: " + "; ".join(passed) + "."
    if plan.stop:
        text += " " + plan.stop["reasoning"]
    if plan.room_r is not None:
        text += f" Nearest opposing zone is {plan.room_r:.1f}R away."
    else:
        text += " No opposing zone found in the analysed range."
    if htf_text:
        text += " " + htf_text
    if plan.status == "REJECTED":
        text += " Rejected: " + " ".join(r["message"] for r in plan.reasons)
    return text


def checks_to_dicts(checks: list[Check]) -> list[dict[str, Any]]:
    return [asdict(c) for c in checks]
