"""Historical robustness and stress testing of the three forward variants.

**Everything here is HISTORICAL.** Every period of this history (including the Phase 5
"unseen test") has now been inspected, so none of it is an untouched test any more; only
forward paper trading produces genuinely new evidence. Results are therefore reported for
parameter *stability* and *sensitivity*, never to pick a new "best" value.

Inputs: the Phase 5 candidate set (production QUALIFIED signals with decision-time context)
and the cached candles. Each signal is re-simulated with ``simulate()``:

* next-open entry (optionally delayed by ``entry_delay`` bars; skipped if that open is
  already beyond the stop or target), OHLC touches, stop-first on ambiguous bars;
* fees (taker, both sides, x ``fee_mult``), entry and stop slippage (x ``slip_mult``), an
  extra spread (``spread_pct``, paid on entry and on market exits), volatility-scaled
  slippage (``vol_slip_frac`` x ATR%%), delayed stop execution (exit at the next bar's open
  after a touch), and Bybit funding history;
* risk per trade is fixed (R = planned risk), so a wider stop means a smaller position.

Variants: ``A`` production plan; ``B@m`` m x ATR stop with a 2R target; ``C@m`` = ``B@m`` and
higher-timeframe confirmation. Repeated testing is acknowledged: the number of
configurations evaluated is reported and multiplier comparisons are Holm-adjusted.
"""

from __future__ import annotations

import json
import pickle
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.jsonutil import jsonable
from app.research.data import DEFAULT_CACHE
from app.research.experiments import day_block_bootstrap, make_split
from app.research.metrics import by_group, summarize
from app.research.stats import assign_clusters, cluster_bootstrap, drawdown_distribution, holm, period_consistency
from app.timeutil import tf_seconds

MULTIPLIERS = [1.5, 2.0, 2.5, 3.0, 3.5]
ROBUSTNESS_FILE = "robustness_latest.json"


@dataclass(frozen=True)
class Scenario:
    name: str = "base"
    fee_pct: float = 0.055
    entry_slip_pct: float = 0.02
    stop_slip_pct: float = 0.05
    fee_mult: float = 1.0
    slip_mult: float = 1.0
    spread_pct: float = 0.0
    entry_delay: int = 0
    stop_delay: bool = False
    vol_slip_frac: float = 0.0
    funding: bool = True


@dataclass
class Series:
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    t: np.ndarray
    bar_ns: int
    fund_t: np.ndarray
    fund_r: np.ndarray


def load_series(cache: Path, symbol: str, tf: str) -> Series:
    df = pd.read_pickle(cache / f"{symbol}_{tf}.pkl")
    fp = cache / f"{symbol}_funding.pkl"
    fu = pd.read_pickle(fp) if fp.exists() else pd.DataFrame(columns=["funding_time", "rate"])
    return Series(df["open"].to_numpy(float), df["high"].to_numpy(float), df["low"].to_numpy(float),
                  df["close"].to_numpy(float), df["open_time"].astype("int64").to_numpy(), tf_seconds(tf) * 10**9,
                  pd.to_datetime(fu["funding_time"], utc=True).astype("int64").to_numpy() if len(fu) else np.array([]),
                  fu["rate"].to_numpy(float) if len(fu) else np.array([]))


def simulate(s: Series, i: int, direction: str, stop: float, target: float, max_bars: int, sc: Scenario,
             atr_pct: float) -> dict[str, Any] | None:
    """One trade under scenario ``sc``. ``i`` is the signal bar. Returns None if no entry."""
    long = direction == "LONG"
    sign = 1.0 if long else -1.0
    e = i + 1 + sc.entry_delay
    n = len(s.c)
    if e >= n:
        return None
    raw = float(s.o[e])
    if (long and not stop < raw < target) or (not long and not target < raw < stop):
        return None
    extra = sc.vol_slip_frac * atr_pct
    entry_slip = (sc.entry_slip_pct * sc.slip_mult + sc.spread_pct / 2 + extra) / 100
    fill = raw * (1 + sign * entry_slip)
    last = min(n - 1, e + max_bars - 1)
    exit_px, reason, k_exit = None, None, last
    for k in range(e, last + 1):
        if k > e:
            ok = s.o[k]
            if (long and ok <= stop) or (not long and ok >= stop):
                exit_px, reason, k_exit = float(ok), "STOP_GAP", k
                break
            if (long and ok >= target) or (not long and ok <= target):
                exit_px, reason, k_exit = float(ok), "TARGET_GAP", k
                break
        hs = s.lo[k] <= stop if long else s.h[k] >= stop
        ht = s.h[k] >= target if long else s.lo[k] <= target
        if hs:
            reason, k_exit = ("AMBIGUOUS_STOP" if ht else "STOP"), k
            if sc.stop_delay and k + 1 < n:
                exit_px, k_exit = float(s.o[k + 1]), k + 1  # executed at the next bar's open
                exit_px = min(exit_px, stop) if long else max(exit_px, stop)
            else:
                exit_px = float(stop)
            break
        if ht:
            exit_px, reason, k_exit = float(target), "TARGET", k
            break
    if exit_px is None:
        if last < e + max_bars - 1:
            return None  # still open at the end of the data
        exit_px, reason, k_exit = float(s.c[last]), "TIME_EXIT", last
    market = not reason.startswith("TARGET")
    if market:
        exit_slip = (sc.stop_slip_pct * sc.slip_mult + sc.spread_pct / 2 + extra) / 100
        exit_fill = exit_px * (1 - sign * exit_slip)
    else:
        exit_fill = exit_px
    risk = abs(raw - stop)
    fees = sc.fee_pct * sc.fee_mult / 100 * (fill + exit_fill)
    fund = 0.0
    exit_close = int(s.t[k_exit]) + s.bar_ns
    if sc.funding and len(s.fund_t):
        m = (s.fund_t > s.t[e]) & (s.fund_t <= exit_close)
        fund = fill * float(s.fund_r[m].sum()) * sign
    net = (sign * (exit_fill - fill) - fees - fund) / risk
    return {"net_r": net, "gross_r": sign * (exit_px - raw) / risk, "reason": reason, "bars_held": k_exit - e + 1,
            "exit_close_ns": exit_close, "fee_r": fees / risk, "stopped": reason in ("STOP", "STOP_GAP", "AMBIGUOUS_STOP")}


def variant_levels(c: dict[str, Any], key: str, mult: float | None) -> tuple[float, float] | None:
    if key == "A":
        o = c["outcomes"].get("C_hybrid") or {}
        return (o["stop"], o["target"]) if "stop" in o else None
    sign = 1 if c["direction"] == "LONG" else -1
    d = mult * c["atr"]
    return c["entry_ref"] - sign * d, c["entry_ref"] + sign * 2 * d


def run_variant(rows: list[dict[str, Any]], series: dict[tuple[str, str], Series], key: str, mult: float | None,
                sc: Scenario, max_bars: int) -> list[dict[str, Any]]:
    out = []
    for c in rows:
        if key == "C" and c.get("mtf_confirmed") is not True:
            continue
        lv = variant_levels(c, key, mult)
        if lv is None:
            continue
        r = simulate(series[(c["symbol"], c["timeframe"])], c["index"], c["direction"], lv[0], lv[1], max_bars, sc,
                     c["atr_pct"])
        if r is None:
            continue
        out.append({**r, "id": c["id"], "symbol": c["symbol"], "timeframe": c["timeframe"], "direction": c["direction"],
                    "decision_ns": c["decision_ns"], "regime": c["regime"]["regime"]})
    return out


def describe(tr: list[dict[str, Any]], sp, days: float) -> dict[str, Any]:
    s = summarize(tr)
    per = {p: summarize([t for t in tr if sp.period(t["decision_ns"]) == p]) for p in ("dev", "val", "test")}
    return {
        "summary": s, "periods": {k: {"n": v["n"], "avg_r": v.get("avg_r")} for k, v in per.items()},
        "trades_per_day": round(len(tr) / days, 3) if days else None,
        "stop_out_pct": round(100 * float(np.mean([t["stopped"] for t in tr])), 2) if tr else None,
        "avg_holding_bars": round(float(np.mean([t["bars_held"] for t in tr])), 2) if tr else None,
        "avg_fee_r": round(float(np.mean([t["fee_r"] for t in tr])), 4) if tr else None,
        "gross_avg_r": round(float(np.mean([t["gross_r"] for t in tr])), 4) if tr else None,
        "by_symbol": {k: {"n": v["n"], "avg_r": v.get("avg_r")} for k, v in by_group(tr, lambda t: t["symbol"]).items()},
        "by_timeframe": {k: {"n": v["n"], "avg_r": v.get("avg_r")} for k, v in by_group(tr, lambda t: t["timeframe"]).items()},
        "by_regime": {k: {"n": v["n"], "avg_r": v.get("avg_r")} for k, v in by_group(tr, lambda t: t["regime"]).items()},
    }


def _mean(tr):
    return round(float(np.mean([t["net_r"] for t in tr])), 4) if tr else None


def correlated_reversal(tr: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adverse assumption: inside every cluster of >= 2 same-direction trades, every trade
    gets the cluster's worst outcome (a market-wide reversal hits all of them)."""
    ids = assign_clusters(tr)
    worst: dict[int, float] = {}
    size: dict[int, int] = {}
    for t, c in zip(tr, ids, strict=True):
        worst[c] = min(worst.get(c, 1e9), t["net_r"])
        size[c] = size.get(c, 0) + 1
    return [{**t, "net_r": worst[c] if size[c] >= 2 else t["net_r"]} for t, c in zip(tr, ids, strict=True)]


def portfolio_sim(tr: list[dict[str, Any]], limits=None, corr=None, starting_equity: float = 10_000.0
                  ) -> dict[str, Any]:
    """Historical paper-portfolio replay with the forward risk engine (``app.forward.risk``):
    trades are taken in decision order only if every limit holds at that moment; each risks
    ``risk_per_trade_pct`` of current equity; equity drawdown is measured on closed trades."""
    from datetime import UTC, datetime

    from app.forward.risk import AccountState, Position, check_entry
    from app.forward.settings import RiskLimits
    limits = limits or RiskLimits()
    st = AccountState(starting_equity, [], [])
    open_: list[tuple[int, dict[str, Any], Position, float]] = []
    taken, reasons = [], {}
    curve = [starting_equity]
    peak, max_dd = starting_equity, 0.0
    for t in sorted(tr, key=lambda x: x["decision_ns"]):
        now = t["decision_ns"]
        for item in sorted([o for o in open_ if o[0] <= now], key=lambda o: o[0]):
            open_.remove(item)
            st.open.remove(item[2])
            pnl = item[1]["net_r"] * item[3]
            st.realized.append((datetime.fromtimestamp(item[0] / 1e9, UTC), pnl))
            eq = st.equity()
            curve.append(eq)
            peak = max(peak, eq)
            max_dd = max(max_dd, 100 * (peak - eq) / peak)
        risk = st.equity() * limits.risk_per_trade_pct / 100
        c = (lambda a, b, at=now: corr.corr(a, b, at)) if corr is not None else None
        ok, code, _why = check_entry(st, t["symbol"], t["timeframe"], t["direction"], risk, limits,
                              datetime.fromtimestamp(now / 1e9, UTC), True, True, c)
        if not ok:
            reasons[code] = reasons.get(code, 0) + 1
            continue
        pos = Position(t["symbol"], t["timeframe"], t["direction"], risk, 0.0)
        st.open.append(pos)
        open_.append((t["exit_close_ns"], t, pos, risk))
        taken.append(t)
    for item in sorted(open_, key=lambda o: o[0]):
        st.realized.append((datetime.fromtimestamp(item[0] / 1e9, UTC), item[1]["net_r"] * item[3]))
        eq = st.equity()
        curve.append(eq)
        peak = max(peak, eq)
        max_dd = max(max_dd, 100 * (peak - eq) / peak)
    return {"taken": len(taken), "rejected": reasons, "avg_net_r": _mean(taken),
            "final_equity": round(st.equity(), 2), "return_pct": round(100 * (st.equity() / starting_equity - 1), 2),
            "max_drawdown_pct": round(max_dd, 2), "limits": limits.model_dump(),
            "equity_curve": [round(x, 2) for x in curve[:: max(1, len(curve) // 300)]]}


def run(cache: Path = DEFAULT_CACHE, max_bars: int = 96, seed: int = 21) -> dict[str, Any]:
    t0 = time.time()
    with open(cache / "candidates.pkl", "rb") as fh:
        data = pickle.load(fh)
    meta = data["meta"]
    rows = [c for c in data["rows"] if c["family"] == "production"]
    series = {(s, tf): load_series(cache, s, tf) for s, tf in {(c["symbol"], c["timeframe"]) for c in rows}}
    sp = make_split(rows)
    days = (sp.end - sp.start) / 86_400e9
    base = Scenario()
    configs: dict[str, list[dict[str, Any]]] = {"A": run_variant(rows, series, "A", None, base, max_bars)}
    for m in MULTIPLIERS:
        configs[f"B@{m:g}"] = run_variant(rows, series, "B", m, base, max_bars)
        configs[f"C@{m:g}"] = run_variant(rows, series, "C", m, base, max_bars)
    variants = {k: {**describe(v, sp, days), "bootstrap": cluster_bootstrap(v)} for k, v in configs.items()}
    a = configs["A"]
    p_imp = {k: day_block_bootstrap(a, v)["p_improvement"] for k, v in configs.items() if k != "A"}
    holm_p = holm({k: 1 - (p or 0) for k, p in p_imp.items()})
    stability = {}
    for fam in ("B", "C"):
        curve = {f"{m:g}": variants[f"{fam}@{m:g}"]["summary"].get("avg_r") for m in MULTIPLIERS}
        cells_total = cells_better = 0
        for m in MULTIPLIERS:
            v = variants[f"{fam}@{m:g}"]
            for grp in ("by_symbol", "by_timeframe", "periods"):
                for g, st in v[grp].items():
                    ref = variants["A"][grp].get(g, {}).get("avg_r")
                    if st.get("avg_r") is not None and ref is not None and st["n"] >= 10:
                        cells_total += 1
                        cells_better += st["avg_r"] > ref
        neighbours = [curve[f"{m:g}"] for m in (2.0, 2.5, 3.0)]
        stability[fam] = {
            "avg_r_by_multiplier": curve,
            "cells_better_than_A": cells_better, "cells_total": cells_total,
            "plateau_2_to_3_all_above_A": all(x is not None and x > (variants["A"]["summary"].get("avg_r") or 0)
                                              for x in neighbours),
            "plateau_2_to_3_all_positive": all(x is not None and x > 0 for x in neighbours),
            "monotonic_increasing": all(curve[f"{a_:g}"] <= curve[f"{b_:g}"]
                                        for a_, b_ in zip(MULTIPLIERS, MULTIPLIERS[1:], strict=False)),
        }

    # ---------------- stress tests for A, B@2.5, C@2.5
    stress_keys = {"A": ("A", None), "B@2.5": ("B", 2.5), "C@2.5": ("C", 2.5)}
    scenarios = [
        Scenario("base"),
        Scenario("fees x1.5", fee_mult=1.5), Scenario("fees x2", fee_mult=2.0), Scenario("fees x3", fee_mult=3.0),
        Scenario("slippage x2", slip_mult=2.0), Scenario("slippage x4", slip_mult=4.0),
        Scenario("spread +0.05%", spread_pct=0.05), Scenario("spread +0.10%", spread_pct=0.10),
        Scenario("entry delayed 1 bar", entry_delay=1), Scenario("entry delayed 2 bars", entry_delay=2),
        Scenario("stop executed next open", stop_delay=True),
        Scenario("volatility slippage 0.1 x ATR%", vol_slip_frac=0.1),
        Scenario("no funding", funding=False),
        Scenario("combined adverse", fee_mult=1.5, slip_mult=2.0, spread_pct=0.05, entry_delay=1, stop_delay=True),
    ]
    stress: dict[str, Any] = {}
    rng = np.random.default_rng(seed)
    for label, (key, m) in stress_keys.items():
        res = {}
        for sc in scenarios:
            tr = configs[label] if sc.name == "base" else run_variant(rows, series, key, m, sc, max_bars)
            b = cluster_bootstrap(tr, reps=1000, gains_losses=False)
            res[sc.name] = {"n": len(tr), "avg_r": _mean(tr), "ci95": b.get("avg_net_r_ci95"),
                            "prob_positive": b.get("prob_avg_positive"), "total_r": round(sum(t["net_r"] for t in tr), 2)}
        tr = configs[label]
        hv = [t for t in tr if t["regime"] == "HIGH_VOL_EXPANSION"]
        res["high-volatility regime only"] = {"n": len(hv), "avg_r": _mean(hv)}
        rev = correlated_reversal(tr)
        res["correlated reversal (clusters take their worst outcome)"] = {"n": len(rev), "avg_r": _mean(rev),
                                                                         "total_r": round(sum(t["net_r"] for t in rev), 2)}
        for frac in (0.1, 0.25):
            means = []
            for _ in range(300):
                keep = rng.random(len(tr)) >= frac
                sub = [t for t, k in zip(tr, keep, strict=True) if k]
                means.append(np.mean([t["net_r"] for t in sub]))
            res[f"missing data: {int(frac * 100)}% of signals lost"] = {
                "avg_r": round(float(np.mean(means)), 4), "p05": round(float(np.quantile(means, 0.05)), 4),
                "p95": round(float(np.quantile(means, 0.95)), 4)}
        # Break-even: scale all costs together.
        curve = []
        for k in (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0):
            sc = replace(base, name=f"costs x{k:g}", fee_mult=k, slip_mult=k, funding=k > 0)
            tr_k = configs[label] if k == 1.0 else run_variant(rows, series, key, m, sc, max_bars)
            curve.append({"cost_multiplier": k, "avg_r": _mean(tr_k),
                          "round_trip_cost_pct": round(k * (2 * base.fee_pct + base.entry_slip_pct + base.stop_slip_pct), 4)})
        be = None
        for p, q in zip(curve, curve[1:], strict=False):
            if p["avg_r"] is not None and q["avg_r"] is not None and p["avg_r"] > 0 >= q["avg_r"]:
                be = p["cost_multiplier"] + (q["cost_multiplier"] - p["cost_multiplier"]) * p["avg_r"] / (p["avg_r"] - q["avg_r"])
        res["_cost_curve"] = curve
        res["_break_even_cost_multiplier"] = round(be, 3) if be is not None else (
            None if (curve[0]["avg_r"] or 0) <= 0 else "> 3")
        stress[label] = res

    from app.research.study import correlation_book
    corr = correlation_book(cache, meta["symbols"])
    from app.forward.settings import RiskLimits
    portfolio = {k: portfolio_sim(configs[k], corr=corr) for k in ("A", "B@2.5", "C@2.5")}
    portfolio_no_dd_breaker = {k: portfolio_sim(configs[k], RiskLimits(max_drawdown_pct=100), corr)
                               for k in ("A", "B@2.5", "C@2.5")}
    tested = len(configs) + len(stress_keys) * (len(scenarios) + 9)
    out = {
        "meta": {"label": "HISTORICAL re-simulation; all historical periods have already been inspected, so none is an "
                          "untouched test. Only forward paper trading provides new evidence.",
                 "generated_at": pd.Timestamp.now(tz="UTC").isoformat(), "seconds": round(time.time() - t0, 1),
                 "strategy_version": meta["strategy_version"], "data_start": pd.Timestamp(sp.start, tz="UTC").isoformat(),
                 "data_end": pd.Timestamp(sp.end, tz="UTC").isoformat(), "days": round(days, 1),
                 "signals": len(rows), "max_bars": max_bars, "base_costs": asdict(base),
                 "configurations_evaluated": tested, "split": sp.to_dict()},
        "variants": variants,
        "p_improvement_vs_A": p_imp, "holm_adjusted_p": holm_p,
        "stability": stability,
        "drawdown": {k: drawdown_distribution(configs[k]) for k in ("A", "B@2.5", "C@2.5")},
        "consistency": {k: period_consistency(configs[k]) for k in ("A", "B@2.5", "C@2.5")},
        "stress": stress,
        "portfolio": portfolio,
        "portfolio_no_drawdown_breaker": portfolio_no_dd_breaker,
    }
    out = jsonable(out)
    (cache / ROBUSTNESS_FILE).write_text(json.dumps(out), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = run()
    for k, v in r["variants"].items():
        s = v["summary"]
        print(f"{k:7} n={s['n']:5} avg={s.get('avg_r')} pf={s.get('profit_factor')} dd={s.get('max_drawdown_r')} "
              f"P+={v['bootstrap'].get('prob_avg_positive')} clusters={v['bootstrap'].get('n_clusters')} "
              f"periods={ {p: x['avg_r'] for p, x in v['periods'].items()} }")
    print("holm", r["holm_adjusted_p"])
    print("stability", json.dumps(r["stability"]))
    for k, v in r["stress"].items():
        print(k, {s: (x.get("avg_r")) for s, x in v.items() if not s.startswith("_")}, "BE", v["_break_even_cost_multiplier"])
    print("dd", {k: (v["dd_quantiles_r"], v["prob_dd_beyond"]) for k, v in r["drawdown"].items()})
    print("cons", {k: (v["best_month_share_of_positive_r"], v["positive_months"], v["months_total"], v["positive_thirds"]) for k, v in r["consistency"].items()})
    print("portfolio", {k: {x: v[x] for x in ("taken", "avg_net_r", "return_pct", "max_drawdown_pct", "rejected")} for k, v in r["portfolio"].items()})
    print("no-dd-breaker", {k: {x: v[x] for x in ("taken", "avg_net_r", "return_pct", "max_drawdown_pct")} for k, v in r["portfolio_no_drawdown_breaker"].items()})
    print("seconds", r["meta"]["seconds"])
