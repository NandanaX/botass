"""Builds the Trade Setup card: levels, gate result and plain-language reasoning for each triggered setup.

Sizing is a separate call (``sizing.plan_position``) so that editing equity or risk recalculates
immediately without re-running the analysis. The technical score is deliberately left out, because it
isn't a probability. Evidence stays "Unvalidated" until a variant has forward paper-trading results.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from app.planning.context import build_context
from app.planning.gate import GATE_DEFINITIONS, classify_candidate, correlated_count
from app.planning.sizing import decimals_of
from app.planning.stops import STOP_METHODS, plan_all
from app.planning.targets import Costs, build_targets, structural_targets
from app.research.data import DEFAULT_CACHE
from app.research.regime import regime_arrays

UNVALIDATED_NOTE = ("Unvalidated: this variant has no forward paper-trading results. Historical figures come from data "
                    "that has already been inspected, so they can't confirm an edge.")
_study_cache: dict[str, Any] = {"mtime": None, "data": None}


def load_study(cache: Path = DEFAULT_CACHE) -> dict[str, Any] | None:
    p = cache / "p9_latest.json"
    if not p.exists():
        return None
    mtime = p.stat().st_mtime
    if _study_cache["mtime"] != mtime:
        _study_cache.update(mtime=mtime, data=json.loads(p.read_text(encoding="utf-8")))
    return _study_cache["data"]


def gate_support(status: str, method: str, study: dict[str, Any] | None) -> str | None:
    """What history says about skipping this class. Without this, a warning reads as more proven than it is."""
    if status == "QUALIFIED":
        return None
    gate = ((study or {}).get("gate") or {}).get(method) or ((study or {}).get("gate") or {}).get("A_original")
    row = ((gate or {}).get("filters") or {}).get(f"reject_{status}")
    if not row or not row.get("n"):
        return "This check hasn't been tested on past data with this stop."
    verdict = (row.get("vs_baseline") or {}).get("research_verdict")
    if verdict == "IMPROVES_IN_ALL_PERIODS":
        return "On past data, skipping this class improved results in every period. That is still unconfirmed on new data."
    if verdict == "PROMISING":
        return "On past data, skipping this class looked promising but not stable. That is still unconfirmed on new data."
    return "On past data, skipping this class did not reliably improve results, so treat this as information rather than a reason to skip."


def evidence_for(method: str, study: dict[str, Any] | None, gate_status: str = "QUALIFIED") -> dict[str, Any]:
    out: dict[str, Any] = {"status": "Unvalidated", "note": UNVALIDATED_NOTE, "historical": None, "gate_support": gate_support(gate_status, method, study)}
    row = (study or {}).get("stops", {}).get(method)
    if row:
        s = row.get("summary") or {}
        out["historical"] = {"label": "Historical simulation, not forward results", "trades": row.get("n"), "avg_net_r": s.get("avg_r"),
                             "avg_net_r_ci95": row.get("avg_net_r_ci95"), "win_rate_pct": s.get("win_rate_pct"),
                             "max_drawdown_r": s.get("max_drawdown_r"), "stop_out_pct": row.get("stop_out_pct")}
    return out


def _fmt(x: float | None, d: int = 2) -> str:
    return "n/a" if x is None or not math.isfinite(x) else f"{x:.{d}f}"


def _reasoning(ctx, stop, targets, gate, corr_detail, same_open) -> dict[str, Any]:
    ft = ctx.features
    trends = ", ".join(f"{k} {v.lower()}" for k, v in ctx.htf_trends.items()) or "no higher timeframe configured"
    evidence = [f"Setup: {ctx.strategy} {ctx.direction.lower()} on a confirmed closed candle.",
                f"Higher timeframes: {trends}.",
                f"Relative volume {_fmt(ctx.rvol)}x, RSI {_fmt(ft.get('rsi'), 1)}, {_fmt(ft.get('ext_fast_atr'))} ATR from EMA-fast."]
    if ctx.regime:
        evidence.append(f"Market regime: {ctx.regime.replace('_', ' ').lower()}.")
    if ft.get("momentum_fading"):
        evidence.append("MACD momentum has faded against the trade for two bars.")
    valid = [t for t in targets if t.get("valid")]
    blocked = [t["label"] for t in valid if t["feasibility"] == "blocked"]
    if ctx.opposing_boundary is None:
        tp = "No opposing zone in range, so structure doesn't limit these targets, and nothing confirms them either."
    elif blocked:
        tp = f"{', '.join(blocked)} lie beyond the nearest opposing zone at {ctx.opposing_boundary:.6g}. Price may react first."
    else:
        tp = f"All targets are in front of the nearest opposing zone at {ctx.opposing_boundary:.6g}."
    primary = next((t for t in valid if t["r_multiple"] == 2.0), valid[0] if valid else None)
    if same_open:
        corr = "A position in this coin is already open."
    elif corr_detail:
        corr = "; ".join(f"{c['symbol']} {c['direction'].lower()}: correlation {_fmt(c['correlation'])}" for c in corr_detail)
    else:
        corr = "No same-direction paper positions open in other coins."
    return {"technical_evidence": evidence, "entry_quality": GATE_DEFINITIONS[gate["status"]], "stop_justification": stop["justification"],
            "take_profit_feasibility": tp,
            "net_risk_reward": f"{primary['label']} pays about {primary['net_r']:.2f}R after estimated fees and slippage." if primary else "No valid target.",
            "correlation_exposure": corr, "rejection_reasons": gate["reasons"]}


def plan_setup(symbol: str, f, ra, i: int, st: Any, method: str, r_multiples: list[float], costs: Costs, tick: float | None,
               open_positions: list[dict[str, Any]], corr_fn, corr_threshold: float, study: dict[str, Any] | None) -> dict[str, Any]:
    ctx = build_context(f, ra, i, st)
    same, n_corr, corr_detail = correlated_count(symbol, st.direction, open_positions, corr_fn, corr_threshold)
    plans = plan_all(ctx.stop_context(tick))
    variants = []
    for pl in plans.values():
        g = classify_candidate(ctx.gate_input(pl.price, None if pl.valid else pl.justification, same, n_corr), costs=costs)
        variants.append({**pl.to_dict(), "gate_status": g.status, "room_r": g.room_r, "net_r_at_primary": g.net_r_at_primary})
    chosen = plans[method]
    gate = classify_candidate(ctx.gate_input(chosen.price, None if chosen.valid else chosen.justification, same, n_corr), costs=costs).to_dict()
    targets: list[dict[str, Any]] = []
    levels: list[dict[str, Any]] = []
    if chosen.valid:
        targets = build_targets(st.direction, ctx.entry, chosen.price, ctx.atr, r_multiples, ctx.opposing_boundary, costs, tick)
        levels = structural_targets(st.direction, ctx.entry, chosen.price, ctx.atr, [
            ("opposing_zone", f"Nearest opposing zone{f' ({ctx.opposing_timeframe})' if ctx.opposing_timeframe else ''}", ctx.opposing_boundary),
            ("major_swing", "Furthest confirmed swing in the last 300 bars", ctx.major_swing),
            ("measured_move", "Breakout measured move", ctx.measured_move)], costs)
    plan = st.plan or {}
    return {
        "direction": st.direction, "strategy": st.strategy, "production_status": st.status, "research_version": STOP_METHODS[method],
        "stop_method": method, "entry_reference": ctx.entry, "entry_zone": plan.get("entry_zone"), "atr": ctx.atr,
        "stop": chosen.to_dict(), "stop_variants": variants, "targets": targets, "structural_levels": levels,
        "gate": gate, "invalidation": plan.get("invalidation") or [],
        "reasoning": _reasoning(ctx, chosen.to_dict(), targets, gate, corr_detail, same),
        "evidence": evidence_for(method, study, gate["status"]),
    }


def build_card(symbol: str, timeframe: str, f, i: int, ev, rules, method: str, r_multiples: list[float], costs: Costs,
               open_positions: list[dict[str, Any]], corr_fn, corr_threshold: float, study: dict[str, Any] | None) -> dict[str, Any]:
    ra = regime_arrays(f)
    tick = rules.tick_size if rules else None
    setups = []
    for st in ev.setups:
        if st.status not in ("QUALIFIED", "REJECTED") or st.entry_reference is None:
            continue
        setups.append(plan_setup(symbol, f, ra, i, st, method, r_multiples, costs, tick, open_positions, corr_fn, corr_threshold, study))
    return {
        "symbol": symbol, "timeframe": timeframe, "candle_close_time": ev.close_time, "setups": setups,
        "price_decimals": decimals_of(tick), "gate_definitions": GATE_DEFINITIONS, "stop_methods": STOP_METHODS,
        "label": "Experimental research plan. It doesn't change live signals or strategies A, B and C, and nothing is executed.",
    }
