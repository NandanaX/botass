"""Technical analysis decision engine: one timeframe at one closed bar.

``analyze_timeframe`` runs every analyzer on bars ``<= i``, turns their outputs into
``Evidence`` and aggregates it. Scenarios reuse the existing strategy engine
(``evaluate_at`` setups: breakout/pullback with stop, targets and sizing), so a scenario is
QUALIFIED exactly when the live scanner would create a signal; developing scenarios get
hypothetical levels from the same risk modules and are labelled as such.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.analysis import candles as cdl
from app.analysis import chart_patterns as cp
from app.analysis import levels as lv
from app.analysis import liquidity as lq
from app.analysis import momentum as mo
from app.analysis.evidence import Evidence, aggregate, clamp
from app.analysis.frame import AnalysisFrame
from app.analysis.futures import interpret_futures
from app.analysis.structure import structure_at
from app.indicators.zones import Zone
from app.risk.stops import compute_stop
from app.risk.targets import compute_targets, find_opposing_zone
from app.strategy.engine import Evaluation
from app.strategy.params import StrategyParams


def _f(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and math.isfinite(x) else None


def build_evidence(fr: AnalysisFrame, i: int, ctx: dict[str, Any]) -> list[Evidence]:
    f = fr.f
    t = fr.iso(i)
    ev: list[Evidence] = []
    st = ctx["structure"]
    # --- market structure
    cls = st.classification
    if cls in ("BULLISH", "BEARISH"):
        ev.append(Evidence("structure", "structure_state", "structure", 1 if cls == "BULLISH" else -1, 1.0,
                           st.explanation, t))
    elif cls.startswith("TRANSITION"):
        ev.append(Evidence("structure", "structure_state", "structure", 1 if cls.endswith("UP") else -1, 0.6,
                           st.explanation, t))
    elif cls == "CONSOLIDATION":
        ev.append(Evidence("structure", "structure_state", "structure", 0, 0.5, st.explanation, t))
    recent_ev = [e for e in st.events if e.kind != "WICK_BREAK" and i - e.index <= 20]
    if recent_ev:
        e = recent_ev[-1]
        ev.append(Evidence("structure", "structure_event", e.kind.lower(), 1 if e.direction == "BULLISH" else -1,
                           0.8 if e.kind == "BOS" else 0.6, e.label, e.time, e.level))
    # --- trend
    tr = ctx["trend"]
    if tr["ma_alignment"] != "MIXED":
        d = 1 if tr["ma_alignment"] == "BULLISH" else -1
        ev.append(Evidence("trend", "ma_alignment", "ma_stack", d, 1.0,
                           f"Moving averages fully stacked {tr['ma_alignment'].lower()} (EMA 9/20/50/100/200) with the "
                           f"close on the same side of EMA200.", t))
    else:
        net = tr["bull_pairs"] - tr["bear_pairs"]
        if net:
            ev.append(Evidence("trend", "ma_alignment", "ma_partial", 1 if net > 0 else -1,
                               clamp(abs(net) / tr["pairs"] * 0.6),
                               f"Moving averages partly aligned ({tr['bull_pairs']} bullish vs {tr['bear_pairs']} bearish "
                               f"adjacent pairs).", t))
    if tr["supertrend"]:
        ev.append(Evidence("trend", "supertrend", "supertrend", int(tr["supertrend"]), 0.7,
                           f"Supertrend ({fr.params.supertrend_mult:g}x ATR) is {'up' if tr['supertrend'] > 0 else 'down'} "
                           f"(line {tr['supertrend_line']:.6g}).", t, tr["supertrend_line"]))
    if tr["adx"] is not None and tr["plus_di"] is not None and tr["minus_di"] is not None:
        d = 1 if tr["plus_di"] > tr["minus_di"] else -1
        strength = clamp((tr["adx"] - 15) / 25)
        ev.append(Evidence("trend", "adx", "adx", d if tr["trending"] else 0, strength,
                           f"ADX {tr['adx']:.1f} ({'trending' if tr['trending'] else 'weak trend'}); "
                           f"+DI {tr['plus_di']:.1f} vs -DI {tr['minus_di']:.1f}.", t, tr["adx"]))
    # --- momentum (bounded oscillators form ONE group)
    m = ctx["momentum"]
    osc = []
    if m["rsi"] is not None:
        osc.append(("RSI", m["rsi"]))
    if m["stoch_k"] is not None:
        osc.append(("Stochastic %K", m["stoch_k"]))
    if m["stochrsi_k"] is not None:
        osc.append(("Stoch RSI %K", m["stochrsi_k"]))
    for name, v in osc:
        d = 1 if v > 55 else -1 if v < 45 else 0
        text = f"{name} {v:.1f}" + (" (above 50: positive momentum)" if d > 0 else " (below 50: negative momentum)"
                                    if d < 0 else " (neutral)")
        ev.append(Evidence("momentum", "oscillators", name.lower().replace(" ", "_"), d,
                           clamp(abs(v - 50) / 30), text, t, v))
    if m["stretch"]:
        ev.append(Evidence("momentum", "stretch", "rsi_stretch", 0, 0.5, f"RSI {m['rsi']:.1f}: {m['stretch']}.", t,
                           m["rsi"]))
    if m["macd_hist"] is not None:
        d = 1 if m["macd_hist"] > 0 else -1
        acc = m["macd_accel"]
        accelerating = acc is not None and acc * d > 0
        ev.append(Evidence("momentum", "macd", "macd_hist", d, 0.8 if accelerating else 0.45,
                           f"MACD histogram {m['macd_hist']:.4g} ({'accelerating' if accelerating else 'decelerating'}).",
                           t, m["macd_hist"]))
    for dv in ctx["divergences"]:
        ev.append(Evidence("momentum", "divergence", dv["kind"], dv["direction"], 0.6, dv["text"], dv["time"]))
    # --- support / resistance
    atr = _f(f.atr[i])
    c = float(f.close[i])
    z_s, z_r = ctx["nearest_support"], ctx["nearest_resistance"]
    if atr:
        if z_s is not None:
            dist = max(0.0, c - z_s.upper) / atr
            if dist <= 0.5:
                ev.append(Evidence("levels", "near_level", "at_support", 1, clamp(1 - dist) * 0.8,
                                   f"Price at {z_s.timeframe} support {z_s.lower:.6g}-{z_s.upper:.6g} ({z_s.tests} tests).",
                                   t, z_s.upper))
        if z_r is not None:
            dist = max(0.0, z_r.lower - c) / atr
            if dist <= 0.5:
                ev.append(Evidence("levels", "near_level", "at_resistance", -1, clamp(1 - dist) * 0.8,
                                   f"Price at {z_r.timeframe} resistance {z_r.lower:.6g}-{z_r.upper:.6g} ({z_r.tests} tests).",
                                   t, z_r.lower))
        if z_s is not None and z_r is not None:
            up, down = (z_r.lower - c) / atr, (c - z_s.upper) / atr
            if up > 0 and down > 0:
                d = 1 if up > 1.5 * down else -1 if down > 1.5 * up else 0
                ev.append(Evidence("levels", "room", "room", d, 0.4,
                                   f"Room: {up:.1f} ATR to resistance vs {down:.1f} ATR to support.", t))
    for fl in ctx["flips"]:
        if fl["retested"] and fl["held"]:
            ev.append(Evidence("levels", "flip", "flip_retest", 1 if fl["zone_type"] == "SUPPORT" else -1, 0.6,
                               f"{fl['note']} ({fl['timeframe']} {fl['lower']:.6g}-{fl['upper']:.6g}).", fl.get("retest_time")))
    # --- price action
    for p in ctx["candles"]:
        if p.direction == "NEUTRAL":
            ev.append(Evidence("price_action", "candles", p.name, 0, 0.3, p.explanation, p.end_time))
            continue
        s = 0.35 + 0.25 * p.in_context + 0.2 * p.at_level + 0.2 * p.volume_confirmed
        ev.append(Evidence("price_action", "candles", p.name, 1 if p.direction == "BULLISH" else -1, clamp(s),
                           p.explanation, p.end_time))
    for pat in ctx["patterns"]:
        if pat.status == "CONFIRMED":
            d = 1 if pat.breakout_direction == "BULLISH" else -1
            ev.append(Evidence("price_action", "chart_pattern", pat.name, d, 0.8, pat.explanation, pat.resolved_at))
        elif pat.status == "DEVELOPING" and pat.expected != "EITHER":
            ev.append(Evidence("price_action", "chart_pattern", pat.name, 1 if pat.expected == "BULLISH" else -1, 0.3,
                               pat.explanation, pat.detected_at))
    for s_ in ctx["sweeps"]:
        if i - _bar_of(fr, s_["time"]) <= 10:
            ev.append(Evidence("price_action", "liquidity", s_["kind"], 1 if s_["direction"] == "BULLISH" else -1, 0.4,
                               s_["explanation"] + " (experimental)", s_["time"]))
    # --- volume
    vo = ctx["volume"]
    if vo["rvol"] is not None and vo["candle_direction"]:
        ev.append(Evidence("volume", "rvol", "rvol", vo["candle_direction"] if vo["rvol"] >= 1.2 else 0,
                           clamp((vo["rvol"] - 0.8) / 1.5),
                           f"Last candle volume {vo['rvol']:.2f}x average on a "
                           f"{'bullish' if vo['candle_direction'] > 0 else 'bearish'} candle"
                           + (" (spike)" if vo["spike"] else "") + ".", t, vo["rvol"]))
    if vo["obv_divergence"]:
        ev.append(Evidence("volume", "obv", "obv_divergence", 1 if vo["obv_divergence"].startswith("BULLISH") else -1,
                           0.5, f"OBV divergence: {vo['obv_divergence'].lower()}.", t))
    elif vo["obv_slope"] is not None:
        ev.append(Evidence("volume", "obv", "obv_trend", 1 if vo["obv_slope"] > 0 else -1, 0.3,
                           f"OBV {'rising' if vo['obv_slope'] > 0 else 'falling'} over 10 bars.", t))
    if vo["vwap"] is not None:
        ev.append(Evidence("volume", "vwap", "vwap", 1 if vo["above_vwap"] else -1, 0.4,
                           f"Close {'above' if vo['above_vwap'] else 'below'} session VWAP {vo['vwap']:.6g}.", t, vo["vwap"]))
    # --- volatility (context only)
    va = ctx["volatility"]
    ev.append(Evidence("volatility", "volatility", "volatility", 0, 0.5,
                       f"Volatility {va['label'].lower()}" + (f", ATR {va['atr_pct']:.2f}% of price" if va["atr_pct"] else "")
                       + (f", Bollinger width {va['trend'].lower()}" if va["trend"] else "") + ".", t))
    # --- futures
    for item in (ctx.get("futures") or {}).get("items", []):
        d = {"BULLISH": 1, "BEARISH": -1}.get(item["direction"], 0)
        ev.append(Evidence("futures", item["key"], item["key"], d, 0.5, item["text"], None, item.get("value")))
    return ev


def _bar_of(fr: AnalysisFrame, iso: str) -> int:
    import numpy as np
    import pandas as pd
    ns = pd.Timestamp(iso).value
    return int(np.searchsorted(fr.f.open_time, ns, side="left"))


def _nearest(zones: list[Zone], kind: str, price: float) -> Zone | None:
    c = [z for z in zones if z.zone_type == kind and z.status != "INVALIDATED"]
    if kind == "SUPPORT":
        c.sort(key=lambda z: price - z.upper)
    else:
        c.sort(key=lambda z: z.lower - price)
    return c[0] if c else None


def _scenarios(fr: AnalysisFrame, i: int, sp: StrategyParams, ctx: dict[str, Any], ev_strategy: Evaluation | None,
               aggregate_: dict[str, Any], all_zones: list[Zone]) -> list[dict[str, Any]]:
    f = fr.f
    atr = _f(f.atr[i])
    setups = ev_strategy.setups if ev_strategy else []
    out = []
    for direction, name in (("LONG", "A. Bullish continuation / reversal"), ("SHORT", "B. Bearish continuation / reversal")):
        long = direction == "LONG"
        st = next((s for s in setups if s.direction == direction and s.status == "QUALIFIED"), None) or \
            next((s for s in setups if s.direction == direction), None)
        opposing = [e["text"] for e in (aggregate_["opposing"] if (aggregate_["net_score"] > 0) == long else
                                        aggregate_["supporting"])][:5]
        z_trig = ctx["nearest_resistance"] if long else ctx["nearest_support"]
        z_inv = ctx["nearest_support"] if long else ctx["nearest_resistance"]
        sc: dict[str, Any] = {"name": name, "direction": direction, "opposing_evidence": opposing}
        if st is not None and st.status in ("QUALIFIED", "REJECTED"):
            p = st.plan
            sc.update({
                "status": st.status,
                "source": f"strategy {st.strategy_name} ({st.strategy})",
                "trigger": st.explanation.split(". Conditions met")[0],
                "confirmation": "; ".join(cnd.label for cnd in st.conditions),
                "entry_zone": p.get("entry_zone"), "entry": st.entry_reference, "stop_loss": st.stop_loss,
                "targets": p.get("targets"), "risk_reward": st.risk_reward, "net_risk_reward": p.get("net_risk_reward"),
                "sizing": p.get("sizing") if st.status == "QUALIFIED" else None,
                "invalidation": p.get("invalidation"), "reasons": st.reasons,
                "expiration": "Valid for the next candle only: the hypothetical fill is the next open; if that opens "
                              "beyond the stop or TP1 the setup expires.",
                "levels": {"support": _zdict(ctx["nearest_support"]), "resistance": _zdict(ctx["nearest_resistance"])},
            })
        elif atr:
            trig = (z_trig.upper if long else z_trig.lower) if z_trig else None
            sc["status"] = "DEVELOPING" if trig is not None else "NOT APPLICABLE"
            sc["source"] = st.strategy_name if st else "zone structure"
            if trig is not None:
                buf = sp.zones.break_buffer_atr * atr
                entry = trig + buf if long else trig - buf
                struct = z_inv.lower if (z_inv and long) else z_inv.upper if z_inv else None
                stop = compute_stop(direction, entry, atr, sp.stops.method,
                                    struct if struct is not None and ((struct < entry) if long else (struct > entry)) else None,
                                    sp.stops.atr_multiplier, sp.stops.hybrid_buffer_atr, sp.stops.min_stop_atr)
                opp = find_opposing_zone(direction, entry, stop.distance, all_zones)
                tg = compute_targets(direction, entry, stop.distance, sp.targets.r_multiples, sp.targets.allocation_pct, opp)
                sc.update({
                    "trigger": (f"A candle closes {'above' if long else 'below'} the {z_trig.timeframe} "
                                f"{'resistance' if long else 'support'} {z_trig.lower:.6g}-{z_trig.upper:.6g} "
                                f"(by {sp.zones.break_buffer_atr:g} ATR), or a confirmed rejection at "
                                f"{'support' if long else 'resistance'} in a {'bullish' if long else 'bearish'} trend."),
                    "confirmation": (f"EMA20 {'>' if long else '<'} EMA50 {'>' if long else '<'} EMA200, RSI "
                                     f"{'50-70' if long else '30-50'}, MACD histogram {'> 0' if long else '< 0'}, "
                                     f"relative volume >= {sp.breakout.min_rvol:g}x, room >= {sp.qualification.min_rr:g}R."),
                    "entry_zone": [min(trig, entry), max(trig, entry)],
                    "hypothetical_entry": entry, "hypothetical_stop": stop.price,
                    "hypothetical_targets": [t_.to_dict() for t_ in tg],
                    "hypothetical_rr": sp.targets.r_multiples[sp.targets.primary_index],
                    "note": "Hypothetical levels for planning only. Not an active signal; no Telegram alert.",
                    "invalidation": [f"A close {'below' if long else 'above'} {stop.price:.6g} before the trigger."],
                    "expiration": "Re-evaluated on every closed candle; levels move as structure changes.",
                    "levels": {"support": _zdict(ctx["nearest_support"]), "resistance": _zdict(ctx["nearest_resistance"])},
                    "watch_reason": st.reasons[0]["message"] if st and st.reasons else None,
                })
        else:
            sc["status"] = "NOT APPLICABLE"
        out.append(sc)
    reasons = []
    if aggregate_["bias"] in ("NEUTRAL", "CONFLICTED", "INSUFFICIENT DATA"):
        reasons.append(f"Evidence is {aggregate_['bias'].lower()} (net {aggregate_['net_score']:+.2f}).")
    if ctx["structure"].classification == "CONSOLIDATION":
        reasons.append("Structure is consolidating; breakouts from ranges often fail.")
    if ctx["trend"]["adx"] is not None and ctx["trend"]["adx"] < 20:
        reasons.append(f"ADX {ctx['trend']['adx']:.1f} shows a weak trend.")
    if atr and ctx["nearest_support"] and ctx["nearest_resistance"]:
        width = (ctx["nearest_resistance"].lower - ctx["nearest_support"].upper) / atr
        if width < 3:
            reasons.append(f"Price is boxed between support and resistance only {width:.1f} ATR apart.")
    reasons += aggregate_["conflicts"]
    if not any(s.get("status") == "QUALIFIED" for s in out):
        reasons.append("No strategy setup is QUALIFIED on the last closed candle.")
    out.append({"name": "C. No trade / consolidation", "direction": "NONE",
                "status": "ACTIVE" if reasons else "NOT APPLICABLE", "reasons": reasons,
                "trigger": "Stand aside until A or B qualifies.", "confirmation": None})
    return out


def _zdict(z: Zone | None) -> dict[str, Any] | None:
    return None if z is None else {"timeframe": z.timeframe, "lower": z.lower, "upper": z.upper, "tests": z.tests,
                                   "status": z.status}


def analyze_timeframe(
    fr: AnalysisFrame, i: int, sp: StrategyParams, zones: list[Zone], htf_zones: dict[str, list[Zone]],
    strategy_eval: Evaluation | None = None, futures: dict[str, Any] | None = None, detail: bool = True,
) -> dict[str, Any]:
    f = fr.f
    c = float(f.close[i])
    st = structure_at(fr, i)
    all_z = list(zones) + [z for zs in htf_zones.values() for z in zs]
    ctx: dict[str, Any] = {
        "structure": st,
        "trend": mo.trend_class(fr, i, st.classification),
        "momentum": mo.momentum_class(fr, i),
        "volatility": mo.volatility_class(fr, i),
        "volume": mo.volume_class(fr, i),
        "divergences": mo.divergences(fr, i),
        "nearest_support": _nearest(zones, "SUPPORT", c) or _nearest(all_z, "SUPPORT", c),
        "nearest_resistance": _nearest(zones, "RESISTANCE", c) or _nearest(all_z, "RESISTANCE", c),
        "candles": [p for k in range(max(0, i - 2), i + 1) for p in cdl.detect_candles(fr, k, all_z)],
        "patterns": cp.patterns_at(fr, i),
        "sweeps": lq.sweeps_and_reclaims(fr, i),
    }
    ctx["flips"] = []
    for z in all_z:
        fl = lv.flip_retest(fr, z, i) if z.timeframe == fr.timeframe else None
        if fl:
            ctx["flips"].append({**fl, "zone_type": z.zone_type, "timeframe": z.timeframe, "lower": z.lower,
                                 "upper": z.upper})
    pc = None
    if i >= 10 and math.isfinite(f.close[i - 10]):
        pc = (c / float(f.close[i - 10]) - 1) * 100
    ctx["futures"] = interpret_futures(futures, pc) if futures else None
    evidence = build_evidence(fr, i, ctx)
    agg = aggregate(evidence, fr.params)
    bullish = [e.to_dict() for e in evidence if e.direction > 0]
    bearish = [e.to_dict() for e in evidence if e.direction < 0]
    result: dict[str, Any] = {
        "timeframe": fr.timeframe,
        "index": i,
        "candle_open_time": fr.iso(i),
        "candle_close_time": np.datetime_as_string(np.datetime64(int(f.close_time[i]), "ns"), unit="s") + "Z",
        "close": c,
        "trend": ctx["trend"]["trend"],
        "trend_detail": ctx["trend"],
        "structure": st.classification,
        "structure_detail": {"explanation": st.explanation, "trend": st.trend, "equal_highs": st.equal_highs,
                             "equal_lows": st.equal_lows, "swing_range_atr": st.swing_range_atr,
                             "swings": [s.to_dict() for s in st.swings], "events": [e.to_dict() for e in st.events]},
        "momentum": ctx["momentum"]["label"],
        "momentum_detail": ctx["momentum"],
        "volatility": ctx["volatility"]["label"],
        "volatility_detail": ctx["volatility"],
        "volume_detail": ctx["volume"],
        "nearest_support": _zdict(ctx["nearest_support"]),
        "nearest_resistance": _zdict(ctx["nearest_resistance"]),
        "candlestick_patterns": [p.to_dict() for p in ctx["candles"]],
        "chart_patterns": [p.to_dict() for p in ctx["patterns"]],
        "divergences": ctx["divergences"],
        "bias": agg["bias"],
        "alignment_score": agg["alignment_score"],
        "net_score": agg["net_score"],
        "bullish_evidence": bullish,
        "bearish_evidence": bearish,
        "conflicts": agg["conflicts"],
    }
    if detail:
        result["evidence"] = agg
        result["levels"] = [x.to_dict() for x in lv.key_levels(fr, i)]
        result["trendlines"] = [x.to_dict() for x in lv.trendlines(fr, i)]
        result["merged_zones"] = lv.merge_zones({fr.timeframe: zones, **htf_zones})
        result["flips"] = ctx["flips"]
        fib = lq.fibonacci(fr, i)
        result["fibonacci"] = fib.to_dict() if fib else None
        result["equal_levels"] = lq.equal_levels(fr, i)
        result["liquidity_events"] = ctx["sweeps"]
        result["fair_value_gaps"] = lq.fair_value_gaps(fr, i)
        result["futures"] = ctx["futures"]
        result["scenarios"] = _scenarios(fr, i, sp, ctx, strategy_eval, agg, all_z)
    return result
