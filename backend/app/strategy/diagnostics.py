"""Read-only explanation of a strategy decision: every rule with its value and threshold.

``detect_breakout`` / ``detect_pullback`` stop at the first missing precondition, so a
"no setup" result says nothing about which rules held. This module re-states their rules
for the Trade Setup page. It never feeds back into the engine: nothing here is called while
signals are generated, and the status of a strategy is always the engine's own result.
``tests/test_diagnostics.py`` checks over many bars that the rules and the engine agree.

A rule has one of three states: ``pass``, ``fail``, or ``na`` when it can't be evaluated yet
(for example net R:R before there is a trigger candle, since there is no trade plan to measure).
"""
from __future__ import annotations

import math
from typing import Any

from app.indicators.zones import Zone
from app.strategy.features import Features
from app.strategy.params import StrategyParams
from app.strategy.setups import fmt, zones_at


def _rule(key: str, group: str, label: str, state: bool | None, value: str, threshold: str = "",
          advisory: bool = False) -> dict[str, Any]:
    return {"key": key, "group": group, "label": label,
            "state": "na" if state is None else "pass" if state else "fail",
            "value": value, "threshold": threshold, "advisory": advisory}


def _finite(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


def _zone_text(z: Zone) -> str:
    return f"{fmt(z.lower)}–{fmt(z.upper)} ({z.timeframe}{', broken' if z.status == 'BROKEN' else ''})"


def _trend_rules(f: Features, i: int, p: StrategyParams, long: bool) -> list[dict[str, Any]]:
    fast, mid, slow = float(f.ema_fast[i]), float(f.ema_mid[i]), float(f.ema_slow[i])
    sym = ">" if long else "<"
    a = fast > mid if long else fast < mid
    b = mid > slow if long else mid < slow
    return [
        _rule("ema_fast_mid", "Trend", f"EMA{p.ema_fast} {sym} EMA{p.ema_mid}", bool(a), f"{fmt(fast)} vs {fmt(mid)}"),
        _rule("ema_mid_slow", "Trend", f"EMA{p.ema_mid} {sym} EMA{p.ema_slow}", bool(b), f"{fmt(mid)} vs {fmt(slow)}"),
    ]


def _breakout_rules(f: Features, i: int, p: StrategyParams, long: bool, trigger: list[Zone]) -> list[dict[str, Any]]:
    bp = p.breakout
    atr, close = float(f.atr[i]), float(f.close[i])
    rules = _trend_rules(f, i, p, long)
    near: float | None = None
    if bp.level_source == "range":
        level = float(f.prior_high[i] if long else f.prior_low[i])
        broke = close > level if long else close < level
        word = "highest high" if long else "lowest low"
        rules.append(_rule("range_break", "Trigger", f"Close {'above' if long else 'below'} the {word} of the previous "
                           f"{p.breakout_lookback} candles", bool(broke), f"close {fmt(close)}", f"{'>' if long else '<'} {fmt(level)}"))
        near = level if broke else None
    else:
        want = "resistance" if long else "support"
        usable = [z for z in trigger if z.zone_type == want.upper() and z.status != "INVALIDATED"]
        buf = p.zones.break_buffer_atr * atr
        broken = [z for z in usable if (close > z.upper + buf if long else close < z.lower - buf)]
        ahead = sorted((z for z in usable if ((z.upper - close) if long else (close - z.lower)) >= 0),
                       key=lambda z: (z.upper - close) if long else (close - z.lower))
        shown = ahead[0] if ahead else (usable[0] if usable else None)
        rules.append(_rule("zone_available", "Trigger", f"{want.capitalize()} zone to break", bool(usable),
                           _zone_text(shown) if shown else f"none on {f.timeframe}"))
        if broken:
            zone = max(broken, key=lambda z: z.upper) if long else min(broken, key=lambda z: z.lower)
            near = zone.upper if long else zone.lower
            rules.append(_rule("close_through_zone", "Trigger", f"Candle closed {'above' if long else 'below'} the zone", True,
                               f"close {fmt(close)}", f"{'>' if long else '<'} {fmt(near + buf if long else near - buf)}"))
        elif shown:
            need = shown.upper + buf if long else shown.lower - buf
            rules.append(_rule("close_through_zone", "Trigger", f"Candle closed {'above' if long else 'below'} the zone", False,
                               f"close {fmt(close)}", f"{'>' if long else '<'} {fmt(need)}"))
        else:
            rules.append(_rule("close_through_zone", "Trigger", f"Candle closed {'above' if long else 'below'} the zone", None,
                               "no zone to measure against"))
    lo, hi = (p.rsi_long_min, p.rsi_long_max) if long else (p.rsi_short_min, p.rsi_short_max)
    rsi, hist = float(f.rsi[i]), float(f.macd_hist[i])
    rules.append(_rule("rsi_band", "Confirmation", "RSI in band", bool(lo <= rsi <= hi), f"{rsi:.1f}", f"{lo:g}–{hi:g}"))
    rules.append(_rule("macd", "Confirmation", f"MACD histogram {'positive' if long else 'negative'}",
                       bool(hist > 0 if long else hist < 0), fmt(hist), "> 0" if long else "< 0"))
    if bp.min_rvol > 0:
        rv = float(f.rvol[i])
        rules.append(_rule("rvol", "Confirmation", "Relative volume", bool(_finite(rv) and rv >= bp.min_rvol),
                           f"{rv:.2f}x" if _finite(rv) else "no baseline", f"min {bp.min_rvol:g}x"))
    if bp.min_body_ratio > 0:
        rng = float(f.high[i] - f.low[i])
        body = float((f.close[i] - f.open[i]) / rng * (1 if long else -1)) if rng > 0 else 0.0
        rules.append(_rule("body", "Confirmation", "Directional candle body", bool(body >= bp.min_body_ratio),
                           f"{max(body, 0) * 100:.0f}%", f"min {bp.min_body_ratio * 100:.0f}%"))
    if bp.max_extension_atr > 0:
        if near is None:
            rules.append(_rule("extension", "Confirmation", "Not overextended past the level", None,
                               "needs a break first", f"max {bp.max_extension_atr:g} ATR"))
        else:
            ext = abs(close - near) / atr
            rules.append(_rule("extension", "Confirmation", "Not overextended past the level", bool(ext <= bp.max_extension_atr),
                               f"{ext:.2f} ATR", f"max {bp.max_extension_atr:g} ATR"))
    return rules


def _pullback_rules(f: Features, i: int, p: StrategyParams, long: bool, trigger: list[Zone]) -> list[dict[str, Any]]:
    pp = p.pullback
    atr = float(f.atr[i])
    o, h, lo_, c = float(f.open[i]), float(f.high[i]), float(f.low[i]), float(f.close[i])
    slow = float(f.ema_slow[i])
    rules = _trend_rules(f, i, p, long)
    rules.append(_rule("close_vs_slow_ema", "Trend", f"Close {'above' if long else 'below'} EMA{p.ema_slow}",
                       bool(c > slow if long else c < slow), f"{fmt(c)} vs {fmt(slow)}"))
    want = "support" if long else "resistance"
    usable = [z for z in trigger if z.zone_type == want.upper() and z.status != "INVALIDATED"]
    tol, pierce = pp.touch_tolerance_atr * atr, pp.max_pierce_atr * atr
    if long:
        touched = [z for z in usable if lo_ <= z.upper + tol and lo_ >= z.lower - pierce and h >= z.lower]
        ahead = sorted((z for z in usable if lo_ - z.upper > 0), key=lambda z: lo_ - z.upper)
    else:
        touched = [z for z in usable if h >= z.lower - tol and h <= z.upper + pierce and lo_ <= z.upper]
        ahead = sorted((z for z in usable if z.lower - h > 0), key=lambda z: z.lower - h)
    zone = (max(touched, key=lambda z: z.upper) if long else min(touched, key=lambda z: z.lower)) if touched else None
    shown = zone or (ahead[0] if ahead else (usable[0] if usable else None))
    extreme = lo_ if long else h
    rules.append(_rule("zone_available", "Trigger", f"{want.capitalize()} zone to retest", bool(usable),
                       _zone_text(shown) if shown else f"none on {f.timeframe}"))
    if shown:
        rules.append(_rule("zone_retest", "Trigger", "Candle retested the zone", bool(touched),
                           f"{'low' if long else 'high'} {fmt(extreme)}",
                           f"within {fmt(shown.lower - (pierce if long else tol))}–{fmt(shown.upper + (tol if long else pierce))}"))
    else:
        rules.append(_rule("zone_retest", "Trigger", "Candle retested the zone", None, "no zone to measure against"))
    if zone is None:
        rules.append(_rule("close_beyond", "Trigger", "Candle closed back beyond the zone", None, "needs a retest first"))
    else:
        boundary = zone.upper if long else zone.lower
        beyond = (c > boundary if long else c < boundary) if pp.require_close_beyond_zone else \
            (c > zone.lower if long else c < zone.upper)
        need = boundary if pp.require_close_beyond_zone else (zone.lower if long else zone.upper)
        rules.append(_rule("close_beyond", "Trigger", "Candle closed back beyond the zone", bool(beyond),
                           f"close {fmt(c)}", f"{'>' if long else '<'} {fmt(need)}"))
    rng = h - lo_
    close_pos = ((c - lo_) / rng if long else (h - c) / rng) if rng > 0 else 0.0
    rules.append(_rule("close_position", "Trigger", f"Close in the {'top' if long else 'bottom'} of the candle",
                       bool(close_pos >= pp.min_close_position), f"{close_pos * 100:.0f}% of range", f"min {pp.min_close_position * 100:.0f}%"))
    if pp.require_directional_candle:
        rules.append(_rule("directional", "Trigger", f"{'Bullish' if long else 'Bearish'} candle", bool(c > o if long else c < o),
                           f"open {fmt(o)}, close {fmt(c)}"))
    rsi_lo, rsi_hi = (pp.rsi_min_long, p.rsi_long_max) if long else (p.rsi_short_min, pp.rsi_max_short)
    rsi = float(f.rsi[i])
    rules.append(_rule("rsi_band", "Confirmation", "RSI in band", bool(rsi_lo <= rsi <= rsi_hi), f"{rsi:.1f}", f"{rsi_lo:g}–{rsi_hi:g}"))
    if pp.require_macd_turn:
        prev = float(f.macd_hist[i - 1]) if i > 0 else math.nan
        now = float(f.macd_hist[i])
        turned = _finite(prev) and (now > prev if long else now < prev)
        rules.append(_rule("macd_turn", "Confirmation", f"MACD histogram turning {'up' if long else 'down'}", bool(turned),
                           f"{fmt(now)} vs {fmt(prev)}"))
    return rules


def _plan_rules(f: Features, i: int, p: StrategyParams, setup: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Trade-plan checks. Pass or fail comes from the engine's own rejection reasons."""
    q, sp = p.qualification, p.stops
    plan = (setup or {}).get("plan") or {}
    evaluated = setup is not None and setup.get("status") != "WATCHLIST"
    codes = {r["code"] for r in (setup or {}).get("reasons", [])}
    no_plan = "no_valid_target" in codes
    atr_pct = 100 * float(f.atr[i]) / float(f.close[i])
    in_band = p.atr_pct_min <= atr_pct <= p.atr_pct_max
    rules: list[dict[str, Any]] = []

    def row(key: str, label: str, code: str, value: str | None, threshold: str, advisory: bool = False) -> None:
        if not evaluated or value is None:
            state, shown = None, "needs a trigger candle" if not evaluated else "not available"
        else:
            state, shown = code not in codes, value
        rules.append(_rule(key, "Trade plan", label, state, shown, threshold, advisory))

    stop = plan.get("stop") or {}
    row("stop_distance", "Stop distance", "stop_too_wide",
        f"{stop['distance_atr']:.2f} ATR{' (widened to the minimum)' if stop.get('widened') else ''}" if stop else None,
        f"{sp.min_stop_atr:g}–{sp.max_stop_atr:g} ATR")
    if evaluated and not no_plan and plan.get("room_r") is None:
        rules.append(_rule("room", "Trade plan", "Room to the opposing zone", True, "no opposing zone in range", f"min {q.min_rr:g}R"))
    else:
        row("room", "Room to the opposing zone", "opposing_zone_too_close",
            f"{plan['room_r']:.2f}R" if plan.get("room_r") is not None else None, f"min {q.min_rr:g}R")
    net = plan.get("net_risk_reward")
    row("net_rr", "Net R:R after fees and slippage", "net_rr_below_minimum",
        f"{net:.2f}" if net is not None and math.isfinite(net) else None, f"min {q.min_net_rr:g}")
    issues = (plan.get("sizing") or {}).get("issues") or []
    row("sizing", "Position size feasible", issues[0]["code"] if issues else "__none__",
        issues[0]["message"] if issues else ("yes" if plan.get("sizing") else None), "")
    if p.mtf.enabled and p.mtf.mode == "filter":
        confirmed = (setup or {}).get("mtf_confirmed")
        row("mtf", "Higher timeframes agree", "htf_trend_disagrees",
            ("yes" if confirmed else "no" if confirmed is False else "not evaluated"), ", ".join(p.mtf.timeframes))
    # Measured on every candle, so it doesn't need a trigger.
    rules.append(_rule("volatility", "Trade plan", "Volatility (ATR % of price)",
                       True if in_band else not q.reject_outside_volatility, f"{atr_pct:.2f}%",
                       f"{p.atr_pct_min:g}–{p.atr_pct_max:g}%", advisory=not q.reject_outside_volatility))
    return rules


def _trigger_lines(f: Features, i: int, p: StrategyParams, symbol: str | None, trend_long: bool, trend_short: bool) -> list[dict[str, Any]]:
    """What the next candle would need, from the zones the engine will use for it (known at this close)."""
    tf = f.timeframe
    if not (trend_long or trend_short):
        return [{"strategy": None, "direction": None,
                 "text": (f"No setup can form while the trend is mixed. Long needs EMA{p.ema_fast} > EMA{p.ema_mid} > EMA{p.ema_slow}, "
                          f"short needs the reverse (now {fmt(f.ema_fast[i])} / {fmt(f.ema_mid[i])} / {fmt(f.ema_slow[i])}).")}]
    long = trend_long
    side = "Long" if long else "Short"
    zones = zones_at(f, i, p, symbol, None)
    close, low, high, atr = float(f.close[i]), float(f.low[i]), float(f.high[i]), float(f.atr[i])
    out: list[dict[str, Any]] = []
    if p.breakout.enabled:
        if p.breakout.level_source == "range":
            level = float(f.prior_high[i] if long else f.prior_low[i])
            text = f"{side} breakout if a {tf} candle closes {'above' if long else 'below'} {fmt(level)}, the {p.breakout_lookback}-candle range."
        else:
            want = "RESISTANCE" if long else "SUPPORT"
            ahead = [z for z in zones if z.zone_type == want and z.status != "INVALIDATED"
                     and ((z.upper - close) if long else (close - z.lower)) >= 0]
            ahead.sort(key=lambda z: (z.upper - close) if long else (close - z.lower))
            if ahead:
                z = ahead[0]
                buf = p.zones.break_buffer_atr * atr
                level = z.upper + buf if long else z.lower - buf
                text = (f"{side} breakout if a {tf} candle closes {'above' if long else 'below'} about {fmt(level)}, "
                        f"through the {want.lower()} zone {_zone_text(z)}.")
            else:
                text = f"{side} breakout: no {tf} {want.lower()} zone ahead to break right now."
        out.append({"strategy": "breakout", "direction": "LONG" if long else "SHORT", "text": text})
    if p.pullback.enabled:
        want = "SUPPORT" if long else "RESISTANCE"
        pool = [z for z in zones if z.zone_type == want and z.status != "INVALIDATED"
                and (z.upper < low if long else z.lower > high)]
        pool.sort(key=lambda z: (low - z.upper) if long else (z.lower - high))
        if pool:
            z = pool[0]
            text = f"{side} pullback if price retests {_zone_text(z)} and a {tf} candle closes back {'above' if long else 'below'} it."
        else:
            text = f"{side} pullback: no {tf} {want.lower()} zone {'below' if long else 'above'} price to retest right now."
        out.append({"strategy": "pullback", "direction": "LONG" if long else "SHORT", "text": text})
    return out


def explain_decision(f: Features, i: int, p: StrategyParams, setups: list[dict[str, Any]], symbol: str | None = None,
                     ready: bool = True, not_ready_reason: str | None = None) -> dict[str, Any]:
    """Rule checklist for both strategies in both directions at closed candle ``i``.

    ``setups`` are the engine's results for that candle (``Evaluation.to_dict()["setups"]``)."""
    if not ready or i < 1:
        return {"ready": False, "reason": not_ready_reason, "trend_direction": None, "strategies": [], "triggers": []}
    trigger = zones_at(f, i - 1, p, symbol, None)  # the same zones the engine judged this candle against
    fast, mid, slow = f.ema_fast[i], f.ema_mid[i], f.ema_slow[i]
    trend_long, trend_short = bool(fast > mid > slow), bool(fast < mid < slow)
    strategies = []
    for key, label, cfg in (("breakout", "A · Breakout", p.breakout), ("pullback", "B · Pullback / retest", p.pullback)):
        for direction in ("LONG", "SHORT"):
            long = direction == "LONG"
            setup = next((s for s in setups if s["strategy"] == key and s["direction"] == direction), None)
            rules = (_breakout_rules if key == "breakout" else _pullback_rules)(f, i, p, long, trigger)
            rules += _plan_rules(f, i, p, setup)
            strategies.append({
                "strategy": key, "label": label, "direction": direction, "enabled": cfg.enabled,
                "status": "DISABLED" if not cfg.enabled else setup["status"] if setup else "NO_SETUP",
                "rules": rules,
            })
    return {
        "ready": True, "reason": None,
        "trend_direction": "LONG" if trend_long else "SHORT" if trend_short else None,
        "strategies": strategies,
        "triggers": _trigger_lines(f, i, p, symbol, trend_long, trend_short),
    }
