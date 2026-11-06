"""Why does Strategy D take about four times as many 15m trades as Strategy C? Research only.

Replays both on the same candles, period, 2.5 ATR stop, costs and simulator, matches their trades by
coin, candle and direction, and classifies D's extra trades. Nothing here feeds back into D: it doesn't
change D's rules, and it isn't a D v2. **All of this history has been inspected before**, and the many
groups below are descriptive, so a good-looking group is a question for forward testing, not a finding.

``python -m app.research.p10_excess`` (needs ``p10_study`` and ``p9_candidates`` to have been run)
"""

from __future__ import annotations

import json
import pickle
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd

from app.forward.settings import RiskLimits
from app.research.data import DEFAULT_CACHE
from app.research.experiments import make_split
from app.research.metrics import summarize
from app.research.p9_candidates import CANDIDATE_FILE, live_settings
from app.research.p9_study import BASE, jsonable, run_config
from app.research.p10_study import PARTIALS, ROWS_FILE, simulate
from app.research.robustness import load_series, portfolio_sim
from app.research.stats import cluster_bootstrap
from app.research.study import correlation_book
from app.strategy.engine import evaluate_at
from app.strategy.features import compute_features
from app.strategy.params import StrategyParams
from app.strategy_d.confluence import alignment
from app.strategy_d.params import D_PARAMS

OUT_FILE = "p10_excess.json"
FULL_2R = "full_2R"
NO_HALT = RiskLimits(max_drawdown_pct=100, daily_loss_limit_pct=50)  # how often D would trade if the account never halted


def key_of(t: dict[str, Any]) -> tuple[str, int, str]:
    return t["symbol"], t["index"], t["direction"]


def engine_reason(entry_type: str, setups: list[dict[str, Any]]) -> str:
    """Why the existing engine didn't give C this trade, from its triggered setups in this direction."""
    if not setups:
        return f"No engine setup: {entry_type} is D's own entry" if entry_type in ("retest", "continuation") else "No engine setup under live settings"
    qualified = [s for s in setups if s["status"] == "QUALIFIED"]
    if qualified:
        if not any(s["is_best"] for s in qualified):
            return "Engine qualified, but it keeps one signal per candle"
        if any(s["mtf_confirmed"] is True for s in qualified if s["is_best"]):
            return "Engine qualified and C's check passed (not scored for C)"
        return "Engine qualified, but C's check refused: 30m, 1H and 4H trends not all aligned"
    codes = [s["codes"][0] for s in setups if s["codes"]]
    return f"Engine rejected: {Counter(codes).most_common(1)[0][0]}" if codes else "Engine rejected: no reason recorded"


def htf_bucket(direction: str, states: dict[str, dict[str, Any]] | None) -> str:
    """The alignment of 1D, 4H and 1H with the trade, as known at the decision."""
    if not states:
        return "No D context"
    a = {tf: alignment(direction, (states.get(tf) or {}).get("bias", "INSUFFICIENT_DATA")) for tf in ("1d", "4h", "1h")}
    if all(v == "aligned" for v in a.values()):
        return "All three aligned"
    if a["4h"] == "aligned" and a["1h"] == "aligned":
        return f"4H and 1H aligned, 1D {a['1d']}"
    if a["4h"] == "aligned":
        return f"4H aligned, 1H {a['1h']}"
    return f"4H {a['4h']}, 1H {a['1h']}"


def exposure_tags(trades: list[dict[str, Any]], corr, threshold: float) -> dict[tuple[str, int, str], str]:
    """Each trade against the same strategy's earlier trades still open at its decision, assuming every
    one was held. It reads only what was open at that moment, never how any trade later turned out."""
    held: list[dict[str, Any]] = []
    out = {}
    for t in sorted(trades, key=lambda x: (x["decision_ns"], x["symbol"], x["direction"])):
        now = t["decision_ns"]
        held = [h for h in held if h["exit_close_ns"] > now]
        if any(h["symbol"] == t["symbol"] and h["direction"] == t["direction"] for h in held):
            tag = "Repeat: same coin and direction still open"
        elif any(h["symbol"] == t["symbol"] for h in held):
            tag = "Same coin open in the opposite direction"
        elif any(h["direction"] == t["direction"] and corr.corr(h["symbol"], t["symbol"], now) >= threshold for h in held):
            tag = "Correlated: another coin open in the same direction"
        else:
            tag = "Independent at the decision"
        out[key_of(t)] = tag
        held.append(t)
    return out


def _engine_job(args: tuple[str, str, str, list[int]]) -> dict[tuple[str, int, str], list[dict[str, Any]]]:
    symbol, cache_dir, params_json, indices = args
    params = StrategyParams.model_validate_json(params_json)
    cache = Path(cache_dir)
    f = compute_features(pd.read_pickle(cache / f"{symbol}_15m.pkl"), "15m", params)
    tfs = {*params.zones.htf_timeframes, *params.mtf.timeframes} - {"15m", "5m"}
    htf = {tf: compute_features(pd.read_pickle(cache / f"{symbol}_{tf}.pkl"), tf, params) for tf in tfs}
    out: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for i in indices:
        ev = evaluate_at(f, i, params, htf, context=False)
        for st in ev.setups:
            if (st.plan or {}).get("confirmation_candle") is not None:
                out[(symbol, i, st.direction)].append({"strategy": st.strategy, "status": st.status, "mtf_confirmed": st.mtf_confirmed,
                                                       "codes": [r.get("code") for r in st.reasons], "is_best": st is ev.setup})
    return dict(out)


def stats(tr: list[dict[str, Any]], sp) -> dict[str, Any]:
    if not tr:
        return {"n": 0}
    s = summarize(tr)
    n = len(tr)
    gross, net = sum(t["gross_r"] for t in tr) / n, sum(t["net_r"] for t in tr) / n
    boot = cluster_bootstrap(tr, gains_losses=False) if n >= 20 else {}
    periods = {p: summarize([t for t in tr if sp.period(t["decision_ns"]) == p]).get("avg_r") for p in ("dev", "val", "test")}
    return {"n": n, "win_rate_pct": s.get("win_rate_pct"), "avg_gross_r": round(gross, 4), "avg_cost_r": round(gross - net, 4), "avg_net_r": round(net, 4),
            "total_net_r": round(net * n, 1), "profit_factor": s.get("profit_factor"), "ci95": boot.get("avg_net_r_ci95"), "periods": periods,
            "avg_stop_pct": round(sum(abs(t["raw_entry"] - t["stop"]) / t["raw_entry"] for t in tr) / n * 100, 3), "avg_atr_pct": round(sum(t["atr_pct"] for t in tr) / n, 3),
            "stop_out_pct": round(100 * sum(t["stopped"] for t in tr) / n, 1), "avg_bars_held": round(sum(t["bars_held"] for t in tr) / n, 1)}


def table(tr: list[dict[str, Any]], label, sp) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in tr:
        groups[label(t)].append(t)
    return {k: stats(groups[k], sp) for k in sorted(groups, key=lambda g: -len(groups[g]))}


def run(cache: Path = DEFAULT_CACHE) -> dict[str, Any]:
    t0 = time.time()
    p = D_PARAMS
    with open(cache / CANDIDATE_FILE, "rb") as fh:
        c9 = pickle.load(fh)
    with open(cache / ROWS_FILE, "rb") as fh:
        rows = pickle.load(fh)
    symbols = c9["meta"]["symbols"]
    rows9 = [r for r in c9["rows"] if r["kind"] == "QUALIFIED"]
    r15 = [r for r in rows9 if r["timeframe"] == "15m"]
    sp = make_split(rows9)
    days = (sp.end - sp.start) / 86_400e9
    series = {(s, "15m"): load_series(cache, s, "15m") for s in symbols}
    corr = correlation_book(cache, symbols)
    conf = lambda r: r["ctx"]["mtf_confirmed"] is True  # noqa: E731
    by_id9 = {r["id"]: r for r in r15}
    rows_by_key = {(r["symbol"], r["index"], r["direction"]): r for r in rows}
    by_id = {r["id"]: r for r in rows}

    def as_d(tr):  # what each trade needs for matching, from the row that produced it
        return [{**t, "index": by_id[t["id"]]["index"], "atr_pct": by_id[t["id"]]["atr_pct"], "entry_type": by_id[t["id"]]["entry_type"], "states": by_id[t["id"]]["states"]} for t in tr]

    def as_c(tr):
        out = []
        for t in tr:
            r9 = by_id9[t["id"]]
            d_row = rows_by_key.get((t["symbol"], r9["index"], t["direction"]))
            out.append({**t, "index": r9["index"], "atr_pct": r9["atr_pct"], "entry_type": t["strategy"], "states": d_row["states"] if d_row else None})
        return out

    # Same stop, costs and simulator for both, under each of the two exit plans.
    d_tr = {PARTIALS: as_d(simulate(rows, series, "d_default", "atr_2_5", PARTIALS, BASE, p)[0]), FULL_2R: as_d(simulate(rows, series, "d_default", "atr_2_5", FULL_2R, BASE, p)[0])}
    c_tr = {e: as_c(run_config(r15, series, "B_atr_2_5", e, BASE, p.max_bars, conf)) for e in (PARTIALS, FULL_2R)}
    d_keys, c_keys = {key_of(t) for t in d_tr[PARTIALS]}, {key_of(t) for t in c_tr[PARTIALS]}
    shared, d_only_keys, c_only_keys = d_keys & c_keys, d_keys - c_keys, c_keys - d_keys

    settings = live_settings()
    params = settings.strategy
    need: dict[str, set[int]] = defaultdict(set)
    for s, i, _ in d_keys:
        need[s].add(i)
    engine: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    with ProcessPoolExecutor(max_workers=6) as ex:
        for part in ex.map(_engine_job, [(s, str(cache), params.model_dump_json(), sorted(v)) for s, v in need.items()]):
            engine.update(part)

    threshold = RiskLimits().corr_threshold
    tags = {"D": exposure_tags(d_tr[PARTIALS], corr, threshold), "C": exposure_tags(c_tr[PARTIALS], corr, threshold)}
    for t in d_tr[PARTIALS]:
        t["engine_reason"] = "Also in C" if key_of(t) in shared else engine_reason(t["entry_type"], [s for s in engine.get(key_of(t), [])])
        t["exposure"] = tags["D"][key_of(t)]
    d_only = [t for t in d_tr[PARTIALS] if key_of(t) in d_only_keys]
    d_shared = [t for t in d_tr[PARTIALS] if key_of(t) in shared]
    c_part = {key_of(t): t for t in c_tr[PARTIALS]}
    c_shared = [c_part[k] for k in shared]
    c_only = [c_part[k] for k in c_only_keys]
    for t in c_only:
        t["exposure"] = tags["C"][key_of(t)]
    why_c_only = Counter()
    last_taken: dict[tuple[str, str], list[int]] = defaultdict(list)
    for t in d_tr[PARTIALS]:
        last_taken[(t["symbol"], t["direction"])].append(t["index"])
    for k in c_only_keys:
        r = rows_by_key.get(k)
        if r is None:
            why_c_only["D had no trigger on this candle"] += 1
        elif r["default_decision"] == "QUALIFIED":
            near = any(0 < k[1] - i <= p.cooldown_bars for i in last_taken[(k[0], k[2])])
            why_c_only["D's cooldown after an earlier trade" if near else "D qualified it, but the trade couldn't be scored"] += 1
        else:
            why_c_only[f"D {r['default_decision'].lower()}: {r['default_reason']}"] += 1

    def attempts(tr):
        keys = ("taken", "rejected", "avg_net_r", "return_pct", "max_drawdown_pct")
        ordered = sorted(tr, key=lambda t: (t["decision_ns"], t["symbol"]))
        return {"standard_limits": {k: portfolio_sim(ordered, corr=corr)[k] for k in keys}, "no_halts": {k: portfolio_sim(ordered, NO_HALT, corr)[k] for k in keys}}

    same_exit_check = [abs(t["net_r"] - c_part[key_of(t)]["net_r"]) for t in d_shared]
    d2 = {key_of(t): t for t in d_tr[FULL_2R]}
    c2 = {key_of(t): t for t in c_tr[FULL_2R]}
    out = {
        "meta": {"version": p.version, "c_strategy_version": c9["meta"]["strategy_version"], "live_strategy_version": params.version,
                 "start": pd.Timestamp(sp.start, tz="UTC").isoformat(), "end": pd.Timestamp(sp.end, tz="UTC").isoformat(), "days": round(days, 1), "symbols": symbols,
                 "evidence": "Unvalidated", "label": "Historical research data, all previously inspected. Hypothetical results, not forward trades. Groups are descriptive and not corrected for multiple comparisons.",
                 "execution": "2.5 ATR stop, next-open entry, fee 0.055% per side, entry slippage 0.02%, stop slippage 0.05%, funding, 96 bar limit, same simulator for both.", "seconds": None},
        "counts": {"c": len(c_keys), "d": len(d_keys), "shared": len(shared), "d_only": len(d_only_keys), "c_only": len(c_only_keys),
                   "c_per_week": round(7 * len(c_keys) / days, 1), "d_per_week": round(7 * len(d_keys) / days, 1)},
        "whole": {"D_partials": stats(d_tr[PARTIALS], sp), "C_partials": stats(c_tr[PARTIALS], sp), "D_full_2R": stats(d_tr[FULL_2R], sp), "C_full_2R": stats(c_tr[FULL_2R], sp)},
        "sets": {"shared_in_D": stats(d_shared, sp), "shared_in_C": stats(c_shared, sp), "d_only": stats(d_only, sp), "c_only": stats(c_only, sp),
                 "shared_full_2R": stats([d2[k] for k in shared if k in d2], sp), "d_only_full_2R": stats([d2[k] for k in d_only_keys if k in d2], sp), "c_only_full_2R": stats([c2[k] for k in c_only_keys if k in c2], sp)},
        "shared_trade_difference": {"max_abs_net_r": round(max(same_exit_check), 6) if same_exit_check else None, "mean_abs_net_r": round(sum(same_exit_check) / len(same_exit_check), 6) if same_exit_check else None},
        "d_only": {"engine_reason": table(d_only, lambda t: t["engine_reason"], sp), "entry_pattern": table(d_only, lambda t: t["entry_type"], sp),
                   "regime": table(d_only, lambda t: t["regime"], sp), "htf_alignment": table(d_only, lambda t: htf_bucket(t["direction"], t["states"]), sp),
                   "exposure": table(d_only, lambda t: t["exposure"], sp)},
        "shared": {"entry_pattern": table(d_shared, lambda t: t["entry_type"], sp), "regime": table(d_shared, lambda t: t["regime"], sp),
                   "htf_alignment": table(d_shared, lambda t: htf_bucket(t["direction"], t["states"]), sp), "exposure": table(d_shared, lambda t: t["exposure"], sp)},
        "c_only": {"why_d_skipped": dict(why_c_only.most_common()), "exposure": table(c_only, lambda t: t["exposure"], sp), "entry_pattern": table(c_only, lambda t: t["entry_type"], sp),
                   "htf_alignment": table(c_only, lambda t: htf_bucket(t["direction"], t["states"]), sp)},
        "all_d_exposure": table(d_tr[PARTIALS], lambda t: t["exposure"], sp),
        "c_exposure_counts": dict(Counter(tags["C"].values()).most_common()),
        "engine_reason_codes_any": dict(Counter(c for t in d_only for s in engine.get(key_of(t), []) for c in s["codes"]).most_common()),
        "portfolio_attempts": {"D": attempts(d_tr[PARTIALS]), "C": attempts(c_tr[PARTIALS])},
    }
    out["meta"]["seconds"] = round(time.time() - t0)
    out = jsonable(out)
    (cache / OUT_FILE).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    res = run()
    print(json.dumps({"meta": res["meta"], "counts": res["counts"]}, indent=1))
