"""Phase 10 study: Strategy D against the baselines on the same data, costs, execution and risk limits.

**All of this history has been inspected before, so none of it is an untouched test.** D's parameters
were fixed before this ran and aren't tuned from it. Alternative rules are reported for comparison and
don't replace the default. Results can say whether D is worth forward testing, not that it works.

``python -m app.research.p10_study``
"""

from __future__ import annotations

import json
import pickle
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.forward.settings import RiskLimits
from app.research.data import DEFAULT_CACHE
from app.research.experiments import make_split
from app.research.metrics import summarize
from app.research.p9_candidates import CANDIDATE_FILE
from app.research.p9_exits import simulate_legs
from app.research.p9_study import BASE, STRESS, compare, describe, groups, jsonable, run_config
from app.research.regime import regime_arrays
from app.research.robustness import Scenario, Series, load_series, portfolio_sim
from app.research.study import correlation_book
from app.strategy.setups import htf_index
from app.strategy_d.confluence import RULES, rule_passes
from app.strategy_d.context import prepare
from app.strategy_d.engine import STOP_ATR, STOP_LABEL, decide, stop_for
from app.strategy_d.params import D_PARAMS, DParams

ROWS_FILE = "p10_rows.pkl"
OUT_FILE = "p10_latest.json"
PARTIALS = "partial_30_40_30"
EXIT_LEGS = {"full_2R": ((2.0, 1.0),), PARTIALS: ((1.0, 0.3), (2.0, 0.4), (3.0, 0.3))}
EXTENSION_LIMIT_ATR = 2.0


def _job(args: tuple[str, str, int]) -> list[dict[str, Any]]:
    symbol, cache_dir, start_ns = args
    p = D_PARAMS
    cache = Path(cache_dir)
    data = {tf: prepare(pd.read_pickle(cache / f"{symbol}_{tf}.pkl"), tf, p) for tf in ("1d", "4h", "1h", "15m")}
    m5 = cache / f"{symbol}_5m.pkl"
    d5 = prepare(pd.read_pickle(m5), "5m", p) if m5.exists() else None
    f = data["15m"].f
    ra = regime_arrays(f)
    out = []
    for i in range(max(int(np.searchsorted(f.open_time, start_ns)), p.min_bars), len(f) - 1):
        for row in decide(data, i, p, symbol):
            decision_ns = int(f.close_time[i])
            m5_ok = None
            if d5 is not None:
                j = htf_index(d5.f, decision_ns)
                if j >= 0 and decision_ns - int(d5.f.close_time[j]) < 300 * 10**9:
                    above = d5.f.close[j] > d5.f.ema_fast[j]
                    m5_ok = bool(above if row["direction"] == "LONG" else not above)
            out.append({"symbol": symbol, "timeframe": "15m", "index": i, "decision_ns": decision_ns, "direction": row["direction"], "entry_type": row["entry_type"],
                        "strategy": row["entry_type"], "states": {k: {"bias": v["bias"], "last_break": v["last_break"], "last_break_dir": v["last_break_dir"]} for k, v in row["states"].items()},
                        "score": row["confluence"]["score"], "mtf_confirmed": row["mtf_confirmed"], "blocked": row["blocked"], "zone_level": row["zone_level"], "swing_level": row["swing_level"],
                        "boundary": row["opposing_boundary"], "entry": row["entry"], "atr": row["atr"], "atr_pct": 100 * row["atr"] / row["entry"], "extension_atr": row["extension_atr"], "rvol": row["rvol"],
                        "m5_ok": m5_ok, "default_decision": row["decision"], "default_reason": row["reason_code"], "regime": str(ra.label[i])})
    return out


def build(cache: Path, symbols: list[str], start_ns: int, workers: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(_job, [(s, str(cache), start_ns) for s in symbols]):
            rows.extend(r)
    rows.sort(key=lambda r: (r["decision_ns"], r["symbol"], r["direction"]))
    for k, r in enumerate(rows):
        r["id"] = k
    with open(cache / ROWS_FILE, "wb") as fh:
        pickle.dump(rows, fh)
    return rows


def qualifies(r: dict[str, Any], rule: str, stop_method: str, p: DParams) -> float | None:
    """The stop, if this candidate passes ``rule`` with ``stop_method``. Mirrors ``engine.decide``."""
    if r["blocked"]:
        return None
    stop, _ = stop_for(stop_method, r["direction"], r["entry"], r["atr"], r["swing_level"], r["zone_level"])
    if stop is None or stop <= 0:
        return None
    distance = abs(r["entry"] - stop)
    if not p.min_stop_atr <= distance / r["atr"] <= p.max_stop_atr:
        return None
    if r["boundary"] is not None and abs(r["boundary"] - r["entry"]) / distance < p.min_room_r:
        return None
    return stop if rule_passes(rule, r["direction"], r["states"], r["score"], r["mtf_confirmed"], p) else None


def simulate(rows, series: dict[tuple[str, str], Series], rule: str, stop_method: str, exit_key: str, sc: Scenario, p: DParams, keep=None) -> tuple[list[dict[str, Any]], int]:
    """Decision-time filter, then the same cooldown as the live service, then the shared simulator."""
    last: dict[tuple[str, str], int] = {}
    out, signals = [], 0
    for r in rows:
        stop = qualifies(r, rule, stop_method, p)
        if stop is None or (keep is not None and not keep(r)):
            continue
        key = (r["symbol"], r["direction"])
        if key in last and r["index"] - last[key] <= p.cooldown_bars:
            continue
        last[key] = r["index"]
        signals += 1
        sign = 1 if r["direction"] == "LONG" else -1
        risk = abs(r["entry"] - stop)
        legs = [(r["entry"] + sign * m * risk, w) for m, w in EXIT_LEGS[exit_key]]
        if any(px <= 0 for px, _ in legs):
            continue
        t = simulate_legs(series[(r["symbol"], "15m")], r["index"], r["direction"], stop, legs, p.max_bars, sc, r["atr_pct"])
        if t is not None:
            out.append({**t, "id": r["id"], "symbol": r["symbol"], "timeframe": "15m", "direction": r["direction"], "strategy": r["entry_type"],
                        "decision_ns": r["decision_ns"], "regime": r["regime"], "stop_atr": risk / r["atr"], "stop": stop, "target": legs[-1][0]})
    return out, signals


def report_for(tr: list[dict[str, Any]], signals: int | None, sp, corr, days: float) -> dict[str, Any]:
    keys = ("taken", "avg_net_r", "return_pct", "max_drawdown_pct", "rejected")
    pf = portfolio_sim(sorted(tr, key=lambda t: (t["decision_ns"], t["symbol"], t["timeframe"])), corr=corr) if tr else None
    return {"signals": signals if signals is not None else len(tr), "filled": len(tr), "trades_per_week": round(7 * len(tr) / days, 2), **describe(tr, sp),
            "groups": groups(tr) if tr else {}, "portfolio": {k: pf[k] for k in keys} if pf else None}


def stress(fn) -> dict[str, Any]:
    out = {}
    for sc in STRESS:
        tr = fn(sc)
        out[sc.name] = {"n": len(tr), "avg_r": summarize(tr).get("avg_r"), "total_r": round(sum(t["net_r"] for t in tr), 1)}
    return out


def run(cache: Path = DEFAULT_CACHE, rebuild: bool = True) -> dict[str, Any]:
    t0 = time.time()
    p = D_PARAMS
    with open(cache / CANDIDATE_FILE, "rb") as fh:
        c9 = pickle.load(fh)
    meta9, rows9 = c9["meta"], [r for r in c9["rows"] if r["kind"] == "QUALIFIED"]
    symbols = meta9["symbols"]
    start_ns = int(pd.Timestamp(meta9["start"]).value)
    rows = build(cache, symbols, start_ns) if rebuild or not (cache / ROWS_FILE).exists() else pickle.load(open(cache / ROWS_FILE, "rb"))
    series = {(s, tf): load_series(cache, s, tf) for s, tf in {(r["symbol"], r["timeframe"]) for r in rows9} | {(s, "15m") for s in symbols}}
    sp = make_split(rows9)
    days = (sp.end - sp.start) / 86_400e9
    corr = correlation_book(cache, symbols)
    tested = 0

    def d(rule="d_default", stop="atr_2_5", exit_key=PARTIALS, sc=BASE, keep=None):
        nonlocal tested
        tested += 1
        return simulate(rows, series, rule, stop, exit_key, sc, p, keep)

    # Baselines: the locked rules replayed by the same simulator, not their forward records.
    r15 = [r for r in rows9 if r["timeframe"] == "15m"]
    conf9 = lambda r: r["ctx"]["mtf_confirmed"] is True  # noqa: E731
    base = {"A_all": run_config(rows9, series, "A_original", "full_2R", BASE, p.max_bars), "A_15m": run_config(r15, series, "A_original", "full_2R", BASE, p.max_bars),
            "C_all": run_config(rows9, series, "B_atr_2_5", "full_2R", BASE, p.max_bars, conf9), "C_15m": run_config(r15, series, "B_atr_2_5", "full_2R", BASE, p.max_bars, conf9)}
    tested += 4
    c15 = base["C_15m"]
    d_tr, d_sig = d()
    out_base = {k: report_for(v, None, sp, corr, days) for k, v in base.items()}

    rules_out = {}
    for rule, label in RULES.items():
        tr, sig = d(rule)
        rules_out[rule] = {"label": label, **report_for(tr, sig, sp, corr, days), "vs_C_15m": compare(c15, tr, sp) if tr else None}
    stops_out = {}
    for m in (*STOP_ATR, "structural"):
        tr, sig = d(stop=m)
        stops_out[m] = {"label": STOP_LABEL[m], "signals": sig, "filled": len(tr), **describe(tr, sp)}
    exits_out = {}
    for e in EXIT_LEGS:
        tr, sig = d(exit_key=e)
        exits_out[e] = {"signals": sig, "filled": len(tr), **describe(tr, sp)}
    filters = {"extension_limit": (f"Skip entries more than {EXTENSION_LIMIT_ATR:g} ATR from EMA20", lambda r: r["extension_atr"] <= EXTENSION_LIMIT_ATR),
               "volume_confirmed": ("Require relative volume of at least 1.2", lambda r: (r["rvol"] or 0) >= 1.2),
               "m5_refinement": ("Require the 5m close on the trade's side of its EMA20 at the decision", lambda r: r["m5_ok"] is True),
               "long_only": ("LONG only", lambda r: r["direction"] == "LONG"), "short_only": ("SHORT only", lambda r: r["direction"] == "SHORT")}
    filters_out = {}
    for k, (label, keep) in filters.items():
        tr, sig = d(keep=keep)
        filters_out[k] = {"label": label, "signals": sig, "filled": len(tr), **describe(tr, sp), "vs_default": compare(d_tr, tr, sp) if tr else None}
    per_entry = {e: describe([t for t in d_tr if t["strategy"] == e], sp) for e in ("breakout", "retest", "pullback", "continuation")}
    keys = ("taken", "avg_net_r", "return_pct", "max_drawdown_pct", "rejected")
    ordered = sorted(d_tr, key=lambda t: (t["decision_ns"], t["symbol"]))
    limits_view = {"standard": {k: portfolio_sim(ordered, corr=corr)[k] for k in keys},
                   "limits_removed": {k: portfolio_sim(ordered, RiskLimits(max_open_risk_pct=50, max_correlated_positions=0, daily_loss_limit_pct=50, max_drawdown_pct=100), corr)[k] for k in keys},
                   "no_drawdown_breaker": {k: portfolio_sim(ordered, RiskLimits(max_drawdown_pct=100), corr)[k] for k in keys}}
    ordered_c = sorted(c15, key=lambda t: (t["decision_ns"], t["symbol"]))
    limits_c = {"standard": {k: portfolio_sim(ordered_c, corr=corr)[k] for k in keys},
                "limits_removed": {k: portfolio_sim(ordered_c, RiskLimits(max_open_risk_pct=50, max_correlated_positions=0, daily_loss_limit_pct=50, max_drawdown_pct=100), corr)[k] for k in keys}}
    stress_out = {"D_default": stress(lambda sc: d(sc=sc)[0]), "C_15m": stress(lambda sc: run_config(r15, series, "B_atr_2_5", "full_2R", sc, p.max_bars, conf9))}
    tested += 2 * len(STRESS)
    funnel = {"candidates": len(rows), "by_decision": dict(Counter(r["default_decision"] for r in rows)), "by_reason": dict(Counter(r["default_reason"] for r in rows)),
              "by_entry_type": dict(Counter(r["entry_type"] for r in rows)), "by_direction": dict(Counter(r["direction"] for r in rows))}
    biases = {tf: dict(Counter(r["states"][tf]["bias"] for r in rows)) for tf in ("1d", "4h", "1h")}
    out = {"meta": {"version": p.version, "start": pd.Timestamp(sp.start, tz="UTC").isoformat(), "end": pd.Timestamp(sp.end, tz="UTC").isoformat(), "days": round(days, 1), "symbols": symbols,
                    "split": sp.to_dict(), "configurations_tested": tested, "evidence": "Unvalidated", "seconds": round(time.time() - t0),
                    "label": "Historical research data, all previously inspected. Hypothetical results, not forward trades.", "funding_included": True},
           "funnel": funnel, "biases_at_candidates": biases, "strategy_d": {**report_for(d_tr, d_sig, sp, corr, days), "vs_C_15m": compare(c15, d_tr, sp), "vs_C_all": compare(base["C_all"], d_tr, sp)},
           "baselines": out_base, "rules": rules_out, "stops": stops_out, "exits": exits_out, "filters": filters_out, "entry_types": per_entry,
           "portfolio_limits": {"strategy_d": limits_view, "C_15m": limits_c}, "stress": stress_out}
    out = jsonable(out)
    (cache / OUT_FILE).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    res = run()
    print(json.dumps({"meta": res["meta"], "funnel": res["funnel"]}, indent=1))
