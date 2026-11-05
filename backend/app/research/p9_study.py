"""Phase 9 study: stops, exits, entry gate, retest entries, duplicates and portfolio effects.

**All of this history has been inspected before, so none of it is an untouched test.** Results here
can say whether a change is stable and worth a forward test. They can't validate a strategy, so every
variant's ``evidence`` stays ``Unvalidated``. New evidence comes only from forward paper trading.

Method:

* Same entries and one change at a time, against a baseline that uses the same stop (A unless stated).
* Costs: taker fees, entry and stop slippage, Bybit funding (``robustness.Scenario``).
* Time split dev / val / test (50 / 25 / 25%). Nothing is fitted, so all three are read as separate
  periods; ``verdict`` needs improvement in val and test, and no worsening in dev.
* Walk-forward: at each fold the method with the best average net R on earlier closed trades is applied
  to the next fold. That measures whether *choosing by past results* would have worked.
* Uncertainty comes from cluster and day-block bootstraps, because same-hour trades aren't independent.
  The number of configurations tested is reported and improvement p-values are Holm-adjusted.

``python -m app.research.p9_study``
"""

from __future__ import annotations

import json
import pickle
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.forward.settings import RiskLimits
from app.jsonutil import jsonable
from app.planning.context import DecisionContext
from app.planning.gate import GATE_DEFINITIONS, GATE_STATUSES, GateParams, classify_candidate
from app.planning.stops import STOP_METHODS, plan_stop
from app.planning.targets import Costs
from app.research.data import DEFAULT_CACHE
from app.research.experiments import day_block_bootstrap, make_split, verdict
from app.research.metrics import by_group, summarize
from app.research.p9_candidates import CANDIDATE_FILE
from app.research.p9_exits import RETEST_WAIT_BARS, find_retest_fill, retest_plan, simulate_legs
from app.research.robustness import Scenario, Series, load_series, portfolio_sim
from app.research.stats import assign_clusters, cluster_bootstrap, holm, period_consistency
from app.research.study import correlation_book

STUDY_FILE = "p9_latest.json"
BASE = Scenario()
FOLDS = 5
FRESH_FROM = "2026-11-05T00:00:00+00:00"  # first candle after the data earlier phases looked at

EXITS: dict[str, dict[str, Any]] = {
    "full_2R": {"label": "Full exit at 2R", "legs": [(2.0, 1.0)]},
    "full_3R": {"label": "Full exit at 3R", "legs": [(3.0, 1.0)]},
    "partial_50_50": {"label": "50% at 1R, 50% at 2R", "legs": [(1.0, 0.5), (2.0, 0.5)]},
    "partial_30_40_30": {"label": "30% at 1R, 40% at 2R, 30% at 3R", "legs": [(1.0, 0.3), (2.0, 0.4), (3.0, 0.3)]},
    "structural": {"label": "Structural: the opposing zone, else 3R", "legs": None},
}
STRESS = [
    Scenario("base"),
    Scenario("fees x2", fee_mult=2.0),
    Scenario("slippage x4", slip_mult=4.0),
    Scenario("spread +0.10%", spread_pct=0.10),
    Scenario("entry delayed 1 bar", entry_delay=1),
    Scenario("stop executed next open", stop_delay=True),
    Scenario("volatility slippage 0.1 x ATR%", vol_slip_frac=0.1),
    Scenario("combined adverse", fee_mult=1.5, slip_mult=2.0, spread_pct=0.05, entry_delay=1, stop_delay=True),
]


def ctx_of(row: dict[str, Any]) -> DecisionContext:
    return DecisionContext(**row["ctx"])


def stop_price(row: dict[str, Any], method: str) -> float | None:
    p = row.setdefault("_stops", {})
    if method not in p:
        p[method] = plan_stop(method, ctx_of(row).stop_context()).price
    return p[method]


def legs_for(row: dict[str, Any], stop: float, exit_key: str) -> list[tuple[float, float]] | None:
    c = row["ctx"]
    entry, sign = c["entry"], 1.0 if row["direction"] == "LONG" else -1.0
    risk = abs(entry - stop)
    spec = EXITS[exit_key]["legs"]
    if spec is not None:
        legs = [(entry + sign * m * risk, f) for m, f in spec]
    else:
        boundary = c.get("opposing_boundary")
        if boundary is None:
            legs = [(entry + sign * 3.0 * risk, 1.0)]
        else:
            # Stop just in front of the zone, and skip the trade if that pays less than 1R.
            tgt = boundary - sign * 0.1 * c["atr"]
            if sign * (tgt - entry) < risk:
                return None
            legs = [(tgt, 1.0)]
    return None if any(px <= 0 for px, _ in legs) else legs


def simulate_row(row: dict[str, Any], series: dict[tuple[str, str], Series], stop_method: str, exit_key: str, sc: Scenario,
                 max_bars: int) -> dict[str, Any] | None:
    stop = stop_price(row, stop_method)
    if stop is None:
        return None
    legs = legs_for(row, stop, exit_key)
    if legs is None:
        return None
    r = simulate_legs(series[(row["symbol"], row["timeframe"])], row["index"], row["direction"], stop, legs, max_bars, sc,
                      row["atr_pct"])
    if r is None:
        return None
    return {**r, "id": row["id"], "symbol": row["symbol"], "timeframe": row["timeframe"], "direction": row["direction"],
            "strategy": row["strategy"], "decision_ns": row["decision_ns"], "regime": row["ctx"].get("regime"),
            "stop_atr": abs(row["ctx"]["entry"] - stop) / row["ctx"]["atr"], "stop": stop, "target": legs[-1][0]}


def run_config(rows, series, stop_method: str = "A_original", exit_key: str = "full_2R", sc: Scenario = BASE, max_bars: int = 96,
               keep=None) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if keep is not None and not keep(row):
            continue
        t = simulate_row(row, series, stop_method, exit_key, sc, max_bars)
        if t is not None:
            out.append(t)
    return out


def retest_trades(breakouts, series, sc: Scenario, through_atr: float, max_bars: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Breakouts entered by a resting limit at the broken zone's near edge, with a 2R target from there."""
    out, no_fill = [], Counter()
    for r in breakouts:
        c = r["ctx"]
        plan = retest_plan(r["direction"], c["zone_upper"] if r["direction"] == "LONG" else c["zone_lower"], c["original_stop"], c["atr"])
        if plan is None:
            no_fill["no_level"] += 1
            continue
        level, stop, target = plan
        risk = abs(level - stop)
        s = series[(r["symbol"], r["timeframe"])]
        fill = find_retest_fill(s, r["index"], r["direction"], level, stop, target, RETEST_WAIT_BARS, through_atr * c["atr"])
        if fill is None:
            no_fill["not_filled"] += 1
            continue
        o = simulate_legs(s, r["index"], r["direction"], stop, [(target, 1.0)], max_bars, sc, r["atr_pct"], limit_entry=fill)
        if o is None:
            no_fill["unscored"] += 1
            continue
        out.append({**o, "id": r["id"], "symbol": r["symbol"], "timeframe": r["timeframe"], "direction": r["direction"], "strategy": "breakout",
                    "decision_ns": r["decision_ns"], "regime": c.get("regime"), "stop_atr": risk / c["atr"], "stop": stop, "target": target})
    return out, dict(no_fill)


def gate_status(row: dict[str, Any], stop_method: str, gp: GateParams = GateParams()) -> str:
    key = f"_gate_{stop_method}"
    if key not in row:
        ctx = ctx_of(row)
        plan = plan_stop(stop_method, ctx.stop_context())
        row[key] = classify_candidate(ctx.gate_input(plan.price, None if plan.valid else plan.justification), gp).status
    return row[key]


# ------------------------------------------------------------------------------------ reporting

def describe(tr: list[dict[str, Any]], sp) -> dict[str, Any]:
    if not tr:
        return {"summary": summarize([]), "periods": {}, "n": 0}
    per = {p: [t for t in tr if sp.period(t["decision_ns"]) == p] for p in ("dev", "val", "test")}
    oos = per["val"] + per["test"]
    boot = cluster_bootstrap(tr, gains_losses=False)
    stops = sum(t["stopped"] for t in tr)
    return {
        "n": len(tr), "summary": summarize(tr), "periods": {p: summarize(v) for p, v in per.items()}, "later_periods": summarize(oos),
        "clusters": boot.get("n_clusters"), "avg_net_r_ci95": boot.get("avg_net_r_ci95"), "prob_avg_positive": boot.get("prob_avg_positive"),
        "stop_out_pct": round(100 * stops / len(tr), 2),
        "avg_stop_atr": round(float(np.mean([t["stop_atr"] for t in tr])), 3),
        "avg_fee_r": round(float(np.mean([t["fee_r"] for t in tr])), 4),
        "avg_gross_r": round(float(np.mean([t["gross_r"] for t in tr])), 4),
        "avg_holding_bars": round(float(np.mean([t["bars_held"] for t in tr])), 1),
        "avg_mfe_r": round(float(np.mean([t["mfe_r"] for t in tr])), 3), "avg_mae_r": round(float(np.mean([t["mae_r"] for t in tr])), 3),
    }


def groups(tr: list[dict[str, Any]]) -> dict[str, Any]:
    pick = lambda d: {k: {x: v.get(x) for x in ("n", "avg_r", "win_rate_pct", "profit_factor", "total_r")} for k, v in d.items()}  # noqa: E731
    return {"by_direction": pick(by_group(tr, lambda t: t["direction"])), "by_timeframe": pick(by_group(tr, lambda t: t["timeframe"])),
            "by_symbol": pick(by_group(tr, lambda t: t["symbol"])), "by_regime": pick(by_group(tr, lambda t: t.get("regime"))),
            "by_strategy": pick(by_group(tr, lambda t: t["strategy"]))}


def compare(base: list[dict[str, Any]], var: list[dict[str, Any]], sp) -> dict[str, Any]:
    """Compare with the baseline. ``research_verdict`` describes stability only; it is not validation."""
    def rep(x):
        per = {p: [t for t in x if sp.period(t["decision_ns"]) == p] for p in ("dev", "val", "test")}
        return {**{p: summarize(v) for p, v in per.items()}, "oos": summarize(per["val"] + per["test"])}
    rb, rv = rep(base), rep(var)
    pb = [t for t in base if sp.period(t["decision_ns"]) != "dev"]
    pv = [t for t in var if sp.period(t["decision_ns"]) != "dev"]
    boot = day_block_bootstrap(pb, pv)
    v, notes = verdict(rb, rv, boot, fitted=False)
    return {"research_verdict": "IMPROVES_IN_ALL_PERIODS" if v == "VALIDATED" else v, "notes": notes,
            "p_improvement": boot.get("p_improvement"), "delta_ci95": boot.get("delta_ci95"), "evidence": "Unvalidated"}


def walk_forward_choice(configs: dict[str, list[dict[str, Any]]], reference: str, folds: int = FOLDS) -> dict[str, Any]:
    """Each fold uses the option with the best average net R on trades closed before it starts."""
    all_t = sorted(t["decision_ns"] for tr in configs.values() for t in tr)
    lo, hi = all_t[0], all_t[-1] + 1
    edges = [lo + (hi - lo) * k // folds for k in range(folds + 1)]
    rows, chosen_trades, ref_trades = [], [], []
    for k in range(1, folds):
        best, best_v, table = None, -1e9, {}
        for name, tr in configs.items():
            past = [t["net_r"] for t in tr if t["exit_close_ns"] < edges[k]]
            if len(past) >= 30:
                table[name] = round(float(np.mean(past)), 4)
                if table[name] > best_v:
                    best, best_v = name, table[name]
        best = best or reference
        fold = lambda tr: [t for t in tr if edges[k] <= t["decision_ns"] < edges[k + 1]]  # noqa: B023, E731
        c, r = fold(configs[best]), fold(configs[reference])
        chosen_trades += c
        ref_trades += r
        rows.append({"fold": k, "from": pd.Timestamp(edges[k], tz="UTC").isoformat(), "to": pd.Timestamp(edges[k + 1], tz="UTC").isoformat(),
                     "chosen": best, "train_avg_r": table, "chosen_avg_r": summarize(c).get("avg_r"), "chosen_n": len(c),
                     "reference_avg_r": summarize(r).get("avg_r"), "reference_n": len(r)})
    return {"folds": rows, "chosen_total": summarize(chosen_trades), "reference_total": summarize(ref_trades),
            "reference": reference, "picks": dict(Counter(r["chosen"] for r in rows))}


def fold_stability(tr: list[dict[str, Any]], base: list[dict[str, Any]], folds: int = FOLDS) -> list[dict[str, Any]]:
    ts = sorted(t["decision_ns"] for t in base)
    lo, hi = ts[0], ts[-1] + 1
    edges = [lo + (hi - lo) * k // folds for k in range(folds + 1)]
    out = []
    for k in range(folds):
        a = [t for t in tr if edges[k] <= t["decision_ns"] < edges[k + 1]]
        b = [t for t in base if edges[k] <= t["decision_ns"] < edges[k + 1]]
        out.append({"fold": k, "n": len(a), "avg_r": summarize(a).get("avg_r"), "base_n": len(b), "base_avg_r": summarize(b).get("avg_r")})
    return out


# ------------------------------------------------------------------------------------ tightness

def tightness(rows, series, a: list[dict[str, Any]], configs: dict[str, list[dict[str, Any]]], max_bars: int) -> dict[str, Any]:
    """Are original stops inside normal movement? Measured three ways, none proving wider is better."""
    atr_d = np.array([t["stop_atr"] for t in a])
    by_id = {r["id"]: r for r in rows}
    inside_noise = []
    for t in a:
        nd = by_id[t["id"]]["ctx"].get("noise_distance")
        if nd:
            inside_noise.append(abs(by_id[t["id"]]["ctx"]["entry"] - t["stop"]) < nd)
    stopped = [t for t in a if t["stopped"]]
    reached = 0
    for t in stopped:
        s = series[(t["symbol"], t["timeframe"])]
        k = t["exit_index"]
        end = min(len(s.c) - 1, t["entry_index"] + max_bars - 1)
        if k + 1 <= end:
            reached += bool(np.max(s.h[k + 1:end + 1]) >= t["target"]) if t["direction"] == "LONG" else bool(np.min(s.lo[k + 1:end + 1]) <= t["target"])
    winners = [t for t in a if t["net_r"] > 0]
    return {
        "stop_distance_atr_quantiles": {str(q): round(float(np.quantile(atr_d, q / 100)), 3) for q in (10, 25, 50, 75, 90)},
        "share_under_1_atr_pct": round(100 * float((atr_d < 1).mean()), 2),
        "share_inside_recent_noise_pct": round(100 * float(np.mean(inside_noise)), 2) if inside_noise else None,
        "stopped_then_reached_target_pct": round(100 * reached / len(stopped), 2) if stopped else None,
        "winner_mae_r_quantiles": {str(q): round(float(np.quantile([t["mae_r"] for t in winners], q / 100)), 3) for q in (50, 75, 90)} if winners else None,
        "by_stop_method": {m: {"stop_out_pct": round(100 * float(np.mean([t["stopped"] for t in tr])), 2), "avg_fee_r": round(float(np.mean([t["fee_r"] for t in tr])), 4),
                               "avg_gross_r": round(float(np.mean([t["gross_r"] for t in tr])), 4), "avg_net_r": round(float(np.mean([t["net_r"] for t in tr])), 4)}
                           for m, tr in configs.items() if tr},
    }


# ------------------------------------------------------------------------------------ duplicates

def duplicates(tr: list[dict[str, Any]], rows, corr) -> dict[str, Any]:
    """Do repeated signals add independent opportunities? ``tr``: every QUALIFIED signal, original stop."""
    by_id = {r["id"]: r for r in rows}
    ordered = sorted(tr, key=lambda t: t["decision_ns"])
    flags: dict[int, dict[str, bool]] = {}
    pairs: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for k, t in enumerate(ordered):
        f = {"same_symbol_other_tf": False, "same_zone": False, "correlated_coin": False, "any_position_open": False}
        z = by_id[t["id"]]["ctx"]
        for p in ordered[:k]:
            if p["exit_close_ns"] <= t["decision_ns"] or p["direction"] != t["direction"]:
                continue
            f["any_position_open"] = True
            if p["symbol"] == t["symbol"]:
                if p["timeframe"] != t["timeframe"]:
                    f["same_symbol_other_tf"] = True
                    pairs["same_symbol_other_tf"].append((p["net_r"], t["net_r"]))
                pz = by_id[p["id"]]["ctx"]
                if None not in (z["zone_lower"], z["zone_upper"], pz["zone_lower"], pz["zone_upper"]) and \
                        z["zone_lower"] <= pz["zone_upper"] and pz["zone_lower"] <= z["zone_upper"]:
                    f["same_zone"] = True
                    pairs["same_zone"].append((p["net_r"], t["net_r"]))
            elif corr is not None:
                c = corr.corr(p["symbol"], t["symbol"], t["decision_ns"])
                if c == c and c >= 0.7:
                    f["correlated_coin"] = True
                    pairs["correlated_coin"].append((p["net_r"], t["net_r"]))
        flags[t["id"]] = f
    groups_out = {}
    for key in ("same_symbol_other_tf", "same_zone", "correlated_coin", "any_position_open"):
        w = [t for t in ordered if flags[t["id"]][key]]
        wo = [t for t in ordered if not flags[t["id"]][key]]
        xy = pairs.get(key, [])
        rho = float(np.corrcoef(np.array(xy).T)[0, 1]) if len(xy) >= 30 and np.std(xy, axis=0).min() > 0 else None
        groups_out[key] = {"with": summarize(w), "without": summarize(wo), "share_pct": round(100 * len(w) / len(ordered), 2),
                           "pairs": len(xy), "outcome_correlation": round(rho, 3) if rho is not None else None}
    ids = assign_clusters(ordered)
    sizes = np.array(list(Counter(ids).values()))
    vals = defaultdict(list)
    for t, c in zip(ordered, ids, strict=True):
        vals[c].append(t["net_r"])
    grand = np.mean([t["net_r"] for t in ordered])
    k_ = len(vals)
    N = len(ordered)
    n0 = (N - float((sizes ** 2).sum()) / N) / max(k_ - 1, 1)
    msb = sum(len(v) * (np.mean(v) - grand) ** 2 for v in vals.values()) / max(k_ - 1, 1)
    msw = sum(((np.array(v) - np.mean(v)) ** 2).sum() for v in vals.values()) / max(N - k_, 1)
    icc = float((msb - msw) / (msb + (n0 - 1) * msw)) if msb + (n0 - 1) * msw > 0 else None
    mean_m = float(sizes.mean())
    return {"signals": N, "clusters": k_, "mean_cluster_size": round(mean_m, 2), "multi_trade_cluster_share_pct": round(100 * float(sizes[sizes > 1].sum()) / N, 2),
            "within_cluster_correlation": round(icc, 3) if icc is not None else None,
            "effective_independent_signals": round(N / (1 + (mean_m - 1) * max(icc or 0, 0)), 0),
            "groups": groups_out}


def plan_rank(row: dict[str, Any], stop_method: str) -> tuple:
    """Portfolio-aware ranking for candidates at one decision time. Never uses the technical score."""
    order = {s: k for k, s in enumerate(GATE_STATUSES)}
    c = row["ctx"]
    plan = plan_stop(stop_method, ctx_of(row).stop_context())
    room = None
    if plan.distance and c.get("opposing_boundary") is not None:
        room = abs(c["opposing_boundary"] - c["entry"]) / plan.distance
    return (order.get(gate_status(row, stop_method), 9), -(room if room is not None else 10.0), row["symbol"])


def portfolio_views(rows, series, corr, stop_method: str, exit_key: str, keep, max_bars: int) -> dict[str, Any]:
    tr = run_config(rows, series, stop_method, exit_key, BASE, max_bars, keep)
    by_id = {r["id"]: r for r in rows}
    fifo = sorted(tr, key=lambda t: (t["decision_ns"], t["symbol"], t["timeframe"]))
    ranked = sorted(tr, key=lambda t: (t["decision_ns"], plan_rank(by_id[t["id"]], stop_method)))
    keys = ("taken", "avg_net_r", "return_pct", "max_drawdown_pct", "rejected")
    view = lambda r: {k: r[k] for k in keys}  # noqa: E731
    return {"fifo": view(portfolio_sim(fifo, corr=corr)), "ranked": view(portfolio_sim(ranked, corr=corr)),
            "no_drawdown_breaker": view(portfolio_sim(fifo, RiskLimits(max_drawdown_pct=100), corr)),
            "limits_removed": view(portfolio_sim(fifo, RiskLimits(max_open_risk_pct=50, max_correlated_positions=0, daily_loss_limit_pct=50, max_drawdown_pct=100), corr)),
            "ties_at_same_decision_pct": round(100 * sum(1 for _, n in Counter(t["decision_ns"] for t in tr).items() if n > 1) / max(len({t["decision_ns"] for t in tr}), 1), 2)}


def run(cache: Path = DEFAULT_CACHE) -> dict[str, Any]:
    t0 = time.time()
    with open(cache / CANDIDATE_FILE, "rb") as fh:
        data = pickle.load(fh)
    meta, allrows = data["meta"], data["rows"]
    max_bars = meta["max_bars"]
    rows = [r for r in allrows if r["kind"] == "QUALIFIED"]
    room_only = [r for r in allrows if r["kind"] == "ROOM_ONLY"]
    series = {(s, tf): load_series(cache, s, tf) for s, tf in {(r["symbol"], r["timeframe"]) for r in allrows}}
    sp = make_split(rows)
    days = (sp.end - sp.start) / 86_400e9
    corr = correlation_book(cache, meta["symbols"])
    tested = 0

    def go(stop="A_original", exit_key="full_2R", sc=BASE, keep=None, pop=None):
        nonlocal tested
        tested += 1
        return run_config(pop if pop is not None else rows, series, stop, exit_key, sc, max_bars, keep)

    # Module 3: stops, every method on the same entries with a 2R target.
    stop_cfg = {m: go(m) for m in STOP_METHODS}
    a = stop_cfg["A_original"]
    stops = {m: {"label": STOP_METHODS[m], **describe(tr, sp), **groups(tr), "refused_or_no_entry": len(rows) - len(tr),
                 "folds": fold_stability(tr, a), "vs_original": compare(a, tr, sp) if m != "A_original" else None}
             for m, tr in stop_cfg.items()}
    tight = tightness(rows, series, a, stop_cfg, max_bars)
    stop_wf = walk_forward_choice(stop_cfg, "A_original")

    # Module 4: exits, on the original stop and on a 2.5 ATR stop.
    exits: dict[str, Any] = {}
    exit_cfg: dict[str, dict[str, list]] = {}
    for sm in ("A_original", "B_atr_2_5"):
        cfg = {e: go(sm, e) for e in EXITS}
        exit_cfg[sm] = cfg
        ref = cfg["full_2R"]
        exits[sm] = {e: {"label": EXITS[e]["label"], **describe(tr, sp), "by_direction": groups(tr)["by_direction"],
                         "vs_full_2R": compare(ref, tr, sp) if e != "full_2R" else None}
                     for e, tr in cfg.items()}
    exit_wf = {sm: walk_forward_choice(cfg, "full_2R") for sm, cfg in exit_cfg.items()}

    # Module 2: the entry gate.
    gate: dict[str, Any] = {"definitions": GATE_DEFINITIONS, "params": GateParams().model_dump()}
    for sm in ("A_original", "B_atr_2_5"):
        base = stop_cfg[sm]
        status_of = {r["id"]: gate_status(r, sm) for r in rows}
        classes = {s: describe([t for t in base if status_of[t["id"]] == s], sp) for s in GATE_STATUSES}
        filters = {}
        for s in GATE_STATUSES[1:]:
            kept = [t for t in base if status_of[t["id"]] != s]
            tested += 1
            filters[f"reject_{s}"] = {**describe(kept, sp), "vs_baseline": compare(base, kept, sp)}
        only = [t for t in base if status_of[t["id"]] == "QUALIFIED"]
        tested += 1
        gate[sm] = {"class_counts": dict(Counter(status_of.values())), "classes": classes, "filters": filters,
                    "qualified_only": {**describe(only, sp), **groups(only), "vs_baseline": compare(base, only, sp)}}

    # Module 7: other single changes.
    row_by_id = {r["id"]: r for r in rows}
    htf_all = lambda t: row_by_id[t["id"]]["ctx"]["mtf_confirmed"] is True  # noqa: E731
    htf_not_against = lambda t: gate_status(row_by_id[t["id"]], "A_original") != "CONFLICTING_TIMEFRAMES"  # noqa: E731
    singles = {}
    for sm in ("A_original", "B_atr_2_5"):
        base = stop_cfg[sm]
        for name, fn in (("htf_all_agree", htf_all), ("htf_not_against", htf_not_against)):
            kept = [t for t in base if fn(t)]
            tested += 1
            singles[f"{sm}|{name}"] = {**describe(kept, sp), **groups(kept), "vs_baseline": compare(base, kept, sp)}

    # Retest entries: breakouts only, against the same breakouts entered at the next open.
    breakouts = [r for r in rows if r["strategy"] == "breakout"]
    base_bo = run_config(breakouts, series, "A_original", "full_2R", BASE, max_bars)
    rt, no_fill = retest_trades(breakouts, series, BASE, 0.0, max_bars)
    tested += 2
    filled = {t["id"] for t in rt}
    # Only some limits fill, so the fair comparison is the next-open entry on those same signals.
    paired = [t for t in base_bo if t["id"] in filled]
    rt_paired = [t for t in rt if t["id"] in {p["id"] for p in paired}]
    retest_stress: dict[str, Any] = {}
    for name, sc, through in (("base", BASE, 0.0), ("fees x2", Scenario("fees x2", fee_mult=2.0), 0.0), ("slippage x4", Scenario("slippage x4", slip_mult=4.0), 0.0),
                              ("stop executed next open", Scenario("stop executed next open", stop_delay=True), 0.0),
                              ("volatility slippage 0.1 x ATR%", Scenario("volatility slippage 0.1 x ATR%", vol_slip_frac=0.1), 0.0),
                              ("must trade 0.1 ATR past the limit", BASE, 0.1), ("must trade 0.25 ATR past the limit", BASE, 0.25),
                              ("combined adverse", Scenario("combined adverse", fee_mult=1.5, slip_mult=2.0, spread_pct=0.05, stop_delay=True), 0.1)):
        tr_s = rt if name == "base" else retest_trades(breakouts, series, sc, through, max_bars)[0]
        tested += 1
        b = cluster_bootstrap(tr_s, reps=1000, gains_losses=False)
        retest_stress[name] = {"n": len(tr_s), "avg_r": summarize(tr_s).get("avg_r"), "ci95": b.get("avg_net_r_ci95"), "prob_positive": b.get("prob_avg_positive"),
                               "total_r": round(sum(t["net_r"] for t in tr_s), 1)}
    keys = ("taken", "avg_net_r", "return_pct", "max_drawdown_pct", "rejected")
    retest = {"definition": f"Limit order at the broken zone's near edge for up to {RETEST_WAIT_BARS} bars; cancelled if the stop or the 2R target trades first.",
              "signals": len(breakouts), "not_taken": dict(no_fill), "fill_rate_pct": round(100 * len(rt) / max(len(breakouts), 1), 1),
              "next_open": describe(base_bo, sp), "retest": describe(rt, sp), "retest_groups": groups(rt), "folds": fold_stability(rt, base_bo),
              "vs_next_open": compare(base_bo, rt, sp),
              "same_signals_next_open": describe(paired, sp), "vs_same_signals": compare(paired, rt_paired, sp),
              "stress": retest_stress, "survives_all_stress": all((v["avg_r"] or -1) > 0 for v in retest_stress.values()),
              "consistency": {k: v for k, v in period_consistency(rt).items() if k in ("positive_months", "months_total", "best_month_share_of_positive_r", "positive_thirds", "thirds")},
              "trades_per_day": round(len(rt) / days, 2),
              "portfolio": {k: portfolio_sim(rt, corr=corr)[k] for k in keys}, "bootstrap_vs_zero": cluster_bootstrap(rt, gains_losses=False)}

    # Rejected for room only: the rule and the short lead, tested over the whole history.
    room_tr = run_config(room_only, series, "A_original", "full_2R", BASE, max_bars)
    tested += 1
    room = {"note": "Setups the engine refused only because an opposing zone was close. They aren't signals.",
            "all": describe(room_tr, sp), **groups(room_tr),
            "short": describe([t for t in room_tr if t["direction"] == "SHORT"], sp),
            "long": describe([t for t in room_tr if t["direction"] == "LONG"], sp)}

    # Module 8.
    dup = duplicates(a, rows, corr)

    # Shortlist: pre-declared combinations, reported once the single changes are known.
    keep_htf = lambda r: r["ctx"]["mtf_confirmed"] is True  # noqa: E731
    keep_gate = lambda r, sm: gate_status(r, sm) == "QUALIFIED"  # noqa: E731
    shortlist = {
        "baseline_A": ("A_original", "full_2R", None),
        "stop_B": ("B_atr_2_5", "full_2R", None),
        "stop_C": ("C_atr_3_0", "full_2R", None),
        "stop_F": ("F_hybrid", "full_2R", None),
        "B_gate": ("B_atr_2_5", "full_2R", lambda r: keep_gate(r, "B_atr_2_5")),
        "B_htf": ("B_atr_2_5", "full_2R", keep_htf),
        "C_htf": ("C_atr_3_0", "full_2R", keep_htf),
        "F_htf": ("F_hybrid", "full_2R", keep_htf),
        "B_htf_partial": ("B_atr_2_5", "partial_30_40_30", keep_htf),
    }
    short_out: dict[str, Any] = {}
    p_vs_a: dict[str, float] = {}
    for name, (sm, ex, keep) in shortlist.items():
        tr = go(sm, ex, keep=keep)
        res = {"stop": sm, "exit": ex, "filter": name.split("_", 1)[1] if keep else None, **describe(tr, sp), **groups(tr),
               "consistency": {k: v for k, v in period_consistency(tr).items() if k in ("positive_months", "months_total", "best_month_share_of_positive_r", "positive_thirds", "thirds")},
               "trades_per_day": round(len(tr) / days, 2), "folds": fold_stability(tr, a), "vs_baseline_A": compare(a, tr, sp) if name != "baseline_A" else None}
        stress = {}
        for sc in STRESS:
            trs = tr if sc.name == "base" else go(sm, ex, sc, keep)
            b = cluster_bootstrap(trs, reps=1000, gains_losses=False)
            stress[sc.name] = {"n": len(trs), "avg_r": summarize(trs).get("avg_r"), "ci95": b.get("avg_net_r_ci95"), "prob_positive": b.get("prob_avg_positive"),
                               "total_r": round(sum(t["net_r"] for t in trs), 1)}
        res["stress"] = stress
        res["survives_all_stress"] = all((v["avg_r"] or -1) > 0 for v in stress.values())
        res["portfolio"] = portfolio_views(rows, series, corr, sm, ex, keep, max_bars)
        short_out[name] = res
        if name != "baseline_A":
            p_vs_a[name] = 1 - (res["vs_baseline_A"]["p_improvement"] or 0)

    fresh_ns = pd.Timestamp(FRESH_FROM).value
    fresh = {name: summarize([t for t in go(s, e, keep=k) if t["decision_ns"] >= fresh_ns]) for name, (s, e, k) in shortlist.items()}
    out = {
        "meta": {"label": "HISTORICAL research on data that has already been inspected. Nothing here is validated.",
                 "evidence": "Unvalidated", "generated_at": pd.Timestamp.now(tz="UTC").isoformat(), "seconds": round(time.time() - t0, 1),
                 "strategy_version": meta["strategy_version"], "symbols": meta["symbols"], "timeframes": meta["timeframes"],
                 "data_start": pd.Timestamp(sp.start, tz="UTC").isoformat(), "data_end": pd.Timestamp(sp.end, tz="UTC").isoformat(),
                 "days": round(days, 1), "signals": len(rows), "room_only_setups": len(room_only), "max_bars": max_bars, "split": sp.to_dict(),
                 "costs": asdict(BASE), "costs_card": asdict(Costs()), "configurations_tested": tested, "folds": FOLDS, "fresh_from": FRESH_FROM},
        "stops": stops, "stop_walk_forward": stop_wf, "tightness": tight, "exits": exits, "exit_walk_forward": exit_wf,
        "gate": gate, "single_changes": singles, "retest": retest, "room_only": room, "duplicates": dup, "shortlist": short_out,
        "holm_adjusted_vs_baseline": holm(p_vs_a), "fresh_data_since_cutoff": fresh,
    }
    out = jsonable(out)
    (cache / STUDY_FILE).write_text(json.dumps(out), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = run()
    print(json.dumps(r["meta"], indent=0)[:900])
    line = lambda n, d: print(f"{n:26} n={d['n']:5} avg={d['summary'].get('avg_r')} later={d['later_periods'].get('avg_r')} pf={d['summary'].get('profit_factor')} dd={d['summary'].get('max_drawdown_r')} wr={d['summary'].get('win_rate_pct')} stop%={d.get('stop_out_pct')} fee={d.get('avg_fee_r')} P+={d.get('prob_avg_positive')}")  # noqa: E731, E501
    for k, v in r["stops"].items():
        line(k, v)
    for sm, ex in r["exits"].items():
        for k, v in ex.items():
            line(f"{sm[:1]}|{k}", v)
    for k, v in r["shortlist"].items():
        line(k, v)
    print("seconds", r["meta"]["seconds"])
