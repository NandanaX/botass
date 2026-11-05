"""CLI: does the production strategy treat LONG and SHORT the same?

``python -m app.research.direction_audit [--workers 6]`` writes
``research_cache/direction_audit.json``. It only reads cached candles and changes nothing.

Three independent checks on real Bybit history:

* **Mirror test.** Prices are reflected (``p -> M - p``, highs and lows swapped), which turns
  every rally into an identical fall and keeps EMA, MACD, ATR and RSI exact mirror images. Every
  LONG decision on the original must then be a SHORT decision on the mirror at the same bar, with
  mirrored levels. Costs are zero and leverage 1 here because fees and liquidation depend on the
  price *level*, which a reflection changes; they are real asymmetries of percentage costs, not of
  the signal logic. It runs twice: as is (which includes the zone detector) and with the zones
  taken from the original series and reflected, which isolates the zone detector from all
  other logic.
* **Funnel.** Per direction with the production cost settings: bars in the EMA trend, candidates
  triggered, qualified, and why the others were rejected. This separates "the market offered
  fewer longs" from "the rules reject longs more often".
* **Performance.** The historical replay split by direction and market regime.
"""

from __future__ import annotations

import argparse
import json
import pickle
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.indicators.zones import Zone
from app.jsonutil import jsonable
from app.research.data import DEFAULT_CACHE
from app.research.fetch import WATCHLIST
from app.research.metrics import summarize
from app.research.robustness import Scenario, load_series, run_variant
from app.research.stats import cluster_bootstrap
from app.risk.sizing import SizingConfig
from app.settings_store import RuntimeSettings
from app.strategy import engine as engine_module
from app.strategy.engine import evaluate_at
from app.strategy.features import compute_features
from app.strategy.params import StrategyParams

FLIP = {"LONG": "SHORT", "SHORT": "LONG"}
# Costs depend on the price level, so the mirror comparison removes them (see the module docstring).
# Risk is tiny so that margin checks never bind, whichever way a stop is rounded.
NEUTRAL_COSTS = SizingConfig(account_balance=1e9, risk_pct=0.001, fee_rate_pct=0, slippage_pct=0, leverage=1)
LEVEL_TOLERANCE = 1e-6


def mirror_frame(df: pd.DataFrame, m: float) -> pd.DataFrame:
    out = df.copy()
    out["open"], out["close"] = m - df["open"], m - df["close"]
    out["high"], out["low"] = m - df["low"], m - df["high"]
    return out


def mirror_constant(frames: list[pd.DataFrame]) -> float:
    """Keeps every reflected price positive and in the same price range as the original."""
    return float(min(d["low"].min() for d in frames) + max(d["high"].max() for d in frames))


def decisions(f, htf, p: StrategyParams, start_ns: int, sizing: SizingConfig | None) -> dict[tuple[int, str, str], dict[str, Any]]:
    """Every triggered candidate (QUALIFIED or REJECTED) by (bar, direction, strategy)."""
    out: dict[tuple[int, str, str], dict[str, Any]] = {}
    first = max(int(np.searchsorted(f.open_time, start_ns)), p.warmup_bars)
    for i in range(first, len(f)):
        ev = evaluate_at(f, i, p, htf, sizing, None, False)
        for st in ev.setups:
            if st.status in ("QUALIFIED", "REJECTED"):
                out[(i, st.direction, st.strategy)] = {
                    "status": st.status, "entry": st.entry_reference, "stop": st.stop_loss, "target": st.take_profit,
                    "codes": [r["code"] for r in st.reasons], "selected": ev.setup is st}
    return out


def reflect_zone(z: Zone, m: float) -> Zone:
    origin = {"swing_highs": "swing_lows", "swing_lows": "swing_highs"}.get(z.origin, z.origin)
    kind = "RESISTANCE" if z.zone_type == "SUPPORT" else "SUPPORT"
    return replace(z, lower=m - z.upper, upper=m - z.lower, zone_type=kind, origin=origin)  # type: ignore[arg-type]


def compare(orig: dict, mirrored: dict, m: float) -> dict[str, Any]:
    flipped = {(i, FLIP[d], s): v for (i, d, s), v in mirrored.items()}
    both = set(orig) & set(flipped)
    same_status = [k for k in both if orig[k]["status"] == flipped[k]["status"]]
    level_errors = 0
    for k in same_status:
        a, b = orig[k], flipped[k]
        for name in ("entry", "stop", "target"):
            if a[name] is not None and b[name] is not None and abs(a[name] + b[name] - m) > LEVEL_TOLERANCE * m:
                level_errors += 1
                break
    counts = {}
    for label, src in (("original", orig), ("mirror", flipped)):
        for status in ("QUALIFIED", "REJECTED"):
            for d in FLIP:
                counts[f"{label}_{d}_{status}"] = sum(v["status"] == status and k[1] == d for k, v in src.items())
    return {"original_candidates": len(orig), "mirror_candidates": len(flipped), "matched": len(both),
            "only_original": len(set(orig) - set(flipped)), "only_mirror": len(set(flipped) - set(orig)),
            "same_status": len(same_status), "status_differs": len(both) - len(same_status),
            "level_errors": level_errors, **counts}


def audit_pair(args: tuple[str, str, Path, int, list[str]]) -> dict[str, Any]:
    symbol, tf, cache, start_ns, htf_tfs = args
    p = RuntimeSettings().strategy
    df = pd.read_pickle(cache / f"{symbol}_{tf}.pkl")
    htf_dfs = {k: pd.read_pickle(cache / f"{symbol}_{k}.pkl") for k in htf_tfs}
    m = mirror_constant([df, *htf_dfs.values()])
    f = compute_features(df, tf, p)
    fm = compute_features(mirror_frame(df, m), tf, p)
    htf = {k: compute_features(v, k, p) for k, v in htf_dfs.items()}
    htfm = {k: compute_features(mirror_frame(v, m), k, p) for k, v in htf_dfs.items()}

    # Funnel with the production cost settings.
    real = decisions(f, htf, p, start_ns, None)
    first = max(int(np.searchsorted(f.open_time, start_ns)), p.warmup_bars)
    bull = int(np.sum((f.ema_fast[first:] > f.ema_mid[first:]) & (f.ema_mid[first:] > f.ema_slow[first:])))
    bear = int(np.sum((f.ema_fast[first:] < f.ema_mid[first:]) & (f.ema_mid[first:] < f.ema_slow[first:])))
    funnel: dict[str, Any] = {"LONG": {"trend_bars": bull}, "SHORT": {"trend_bars": bear}}
    for d in ("LONG", "SHORT"):
        rows = [v for k, v in real.items() if k[1] == d]
        funnel[d].update(triggered=len(rows), qualified=sum(r["status"] == "QUALIFIED" for r in rows),
                         reasons=Counter(c for r in rows for c in r["codes"]))

    # Mirror comparison with neutral costs.
    orig = decisions(f, htf, p, start_ns, NEUTRAL_COSTS)
    mirr = decisions(fm, htfm, p, start_ns, NEUTRAL_COSTS)
    # The same comparison with the zone detector taken out: zones come from the original series.
    twins = {id(fm): f, **{id(htfm[k]): htf[k] for k in htf}}
    real_zones_at = engine_module.zones_at

    def reflected_zones_at(ff, i, pp, sym=None, max_per_side=None):
        return [reflect_zone(z, m) for z in real_zones_at(twins[id(ff)], i, pp, sym, max_per_side)]

    engine_module.zones_at = reflected_zones_at
    try:
        mirr_reflected = decisions(fm, htfm, p, start_ns, NEUTRAL_COSTS)
    finally:
        engine_module.zones_at = real_zones_at
    return {
        "symbol": symbol, "timeframe": tf, "bars": len(f) - first, "funnel": funnel,
        "mirror": compare(orig, mirr, m), "mirror_zones_reflected": compare(orig, mirr_reflected, m),
    }


def _perf(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"n": 0}
    s, b = summarize(trades), cluster_bootstrap(trades)
    return {"n": s["n"], "win_rate_pct": s.get("win_rate_pct"), "avg_r": s.get("avg_r"), "profit_factor": s.get("profit_factor"),
            "ci95": b.get("avg_net_r_ci95"), "prob_positive": b.get("prob_avg_positive"), "clusters": b.get("n_clusters")}


def performance(cache: Path, max_bars: int = 96) -> dict[str, Any]:
    with open(cache / "candidates.pkl", "rb") as fh:
        data = pickle.load(fh)
    rows = [c for c in data["rows"] if c["family"] == "production"]
    series = {(s, tf): load_series(cache, s, tf) for s, tf in {(c["symbol"], c["timeframe"]) for c in rows}}
    out: dict[str, Any] = {"meta": data["meta"], "counts": dict(Counter(c["direction"] for c in rows))}
    months: dict[str, Counter] = {}
    for c in rows:
        months.setdefault(pd.Timestamp(c["decision_ns"], tz="UTC").strftime("%Y-%m"), Counter())[c["direction"]] += 1
    btc = pd.read_pickle(cache / "BTCUSDT_1h.pkl")
    btc["month"] = btc["open_time"].dt.strftime("%Y-%m")
    change = btc.groupby("month")["close"].agg(lambda s: 100 * (s.iloc[-1] / s.iloc[0] - 1))
    out["by_month"] = {k: {"LONG": v["LONG"], "SHORT": v["SHORT"], "btc_change_pct": round(float(change.get(k, float("nan"))), 1)}
                       for k, v in sorted(months.items())}
    out["variants"] = {}
    for name, key, mult in (("A", "A", None), ("B", "B", 2.5), ("C", "C", 2.5)):
        tr = run_variant(rows, series, key, mult, Scenario(), max_bars)
        no_funding = run_variant(rows, series, key, mult, Scenario(funding=False), max_bars)
        v: dict[str, Any] = {}
        for d in ("LONG", "SHORT"):
            sel = [t for t in tr if t["direction"] == d]
            v[d] = _perf(sel)
            v[d]["gross_avg_r"] = round(float(np.mean([t["gross_r"] for t in sel])), 4)
            v[d]["fee_avg_r"] = round(float(np.mean([t["fee_r"] for t in sel])), 4)
            v[d]["avg_r_without_funding"] = round(float(np.mean([t["net_r"] for t in no_funding if t["direction"] == d])), 4)
            v[d]["by_regime"] = {r: {"n": len(g), "avg_r": round(float(np.mean([t["net_r"] for t in g])), 3)}
                                 for r in sorted({t["regime"] for t in sel}) if (g := [t for t in sel if t["regime"] == r])}
        out["variants"][name] = v
    return out


def run(cache: Path = DEFAULT_CACHE, workers: int = 6, timeframes: tuple[str, ...] = ("15m", "1h", "4h")) -> dict[str, Any]:
    with open(cache / "candidates.pkl", "rb") as fh:
        start_ns = pd.Timestamp(pickle.load(fh)["meta"]["start"]).value
    s = RuntimeSettings()
    jobs = [(sym, tf, cache, start_ns, [h for h in ("1h", "4h") if h != tf and timeframes.index(h) > timeframes.index(tf)])
            for sym in WATCHLIST for tf in timeframes]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pairs = list(pool.map(audit_pair, jobs))
    result = {"strategy_version": s.strategy.version, "pairs": pairs, "performance": performance(cache)}
    (cache / "direction_audit.json").write_text(json.dumps(jsonable(result), default=dict), encoding="utf-8")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    res = run(workers=ap.parse_args().workers)
    print(f"audited {len(res['pairs'])} symbol/timeframe pairs")
