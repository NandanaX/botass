"""Historical validation of analysis signals.

Event study
-----------
Every detected event (candlestick pattern, confirmed chart-pattern breakout, BOS/CHoCH)
at bar ``b`` with direction ``d`` is evaluated on the next ``horizon`` closed candles,
*only after the fact* (the detection itself used bars ``<= b``):

* ``hit``: price moves ``+1 ATR`` in ``d`` before ``-1 ATR`` against it (if both happen
  in one candle, it counts as a miss: conservative);
* ``fwd_r``: ``d x (close[b + horizon] - close[b]) / ATR[b]``.

The series is split chronologically: the first ``in_sample_frac`` of bars is in-sample,
the rest out-of-sample (unseen). Groups with fewer than ``min_events`` events report
``insufficient data`` instead of statistics. A baseline (every bar, both directions)
shows what "no edge" looks like on the same data. No thresholds are fitted here.

Ablation (walk-forward)
-----------------------
The backtest is re-run with one strategy component removed at a time, on consecutive
folds. A component helps only if removing it makes results worse *consistently* across
folds, not just in total.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from statistics import mean, median
from typing import Any

import numpy as np
import pandas as pd

from app.analysis.candles import scan_candles
from app.analysis.chart_patterns import pattern_log, resolve
from app.analysis.frame import AnalysisFrame
from app.analysis.structure import all_structure_events
from app.backtest.engine import BacktestConfig, run_backtest
from app.risk.sizing import InstrumentRules
from app.strategy.params import StrategyParams

MIN_EVENTS = 30


def _outcome(fr: AnalysisFrame, b: int, d: int, horizon: int) -> tuple[bool, float] | None:
    f = fr.f
    n = len(f)
    atr = f.atr[b]
    if b + horizon >= n or not math.isfinite(atr) or atr <= 0:
        return None
    entry = float(f.close[b])
    up, dn = entry + d * atr, entry - d * atr
    hit = False
    for k in range(b + 1, b + horizon + 1):
        fav = f.high[k] >= up if d > 0 else f.low[k] <= up
        adv = f.low[k] <= dn if d > 0 else f.high[k] >= dn
        if adv:
            break
        if fav:
            hit = True
            break
    return hit, float(d * (f.close[b + horizon] - entry) / atr)


def _regime(fr: AnalysisFrame, b: int, d: int) -> str:
    f = fr.f
    adx = fr.adx[b]
    trending = math.isfinite(adx) and adx >= fr.params.adx_trend
    if not (math.isfinite(f.ema_fast[b]) and math.isfinite(f.ema_mid[b])):
        return "unknown"
    with_trend = (f.ema_fast[b] > f.ema_mid[b]) == (d > 0)
    return f"{'trending' if trending else 'ranging'}, {'with' if with_trend else 'against'} EMA trend"


def _stats(rows: list[tuple[bool, float]]) -> dict[str, Any]:
    if len(rows) < MIN_EVENTS:
        return {"n": len(rows), "insufficient_data": True,
                "note": f"insufficient data (n={len(rows)} < {MIN_EVENTS}); no statistical conclusion"}
    hits = [h for h, _ in rows]
    rs = [r for _, r in rows]
    return {"n": len(rows), "insufficient_data": False, "hit_rate": round(sum(hits) / len(hits), 3),
            "mean_fwd_r": round(mean(rs), 3), "median_fwd_r": round(median(rs), 3)}


def event_study(fr: AnalysisFrame, horizon: int = 10, in_sample_frac: float = 0.7, start_index: int = 0
                ) -> dict[str, Any]:
    n = len(fr)
    split = start_index + int((n - start_index) * in_sample_frac)
    events: list[tuple[str, str, int, int]] = []  # (group, name, bar, direction)
    for p in scan_candles(fr, start_index, n - 1):
        if p.direction == "NEUTRAL":
            continue
        d = 1 if p.direction == "BULLISH" else -1
        events.append(("candlestick", p.name, p.end_index, d))
        if p.in_context:
            events.append(("candlestick (in context)", p.name, p.end_index, d))
    for e in all_structure_events(fr):
        if e.index >= start_index:
            events.append(("structure", f"{e.kind} {e.direction.lower()}", e.index, 1 if e.direction == "BULLISH" else -1))
    for inst in pattern_log(fr):
        cp = resolve(fr, inst, n - 1)
        if cp.status == "CONFIRMED" and cp.resolved_index is not None and cp.resolved_index >= start_index:
            events.append(("chart pattern", f"{cp.name} breakout {cp.breakout_direction.lower()}", cp.resolved_index,
                           1 if cp.breakout_direction == "BULLISH" else -1))
    groups: dict[tuple[str, str], dict[str, list]] = defaultdict(lambda: {"in": [], "out": [], "regimes": defaultdict(list)})
    for g, name, b, d in events:
        o = _outcome(fr, b, d, horizon)
        if o is None:
            continue
        bucket = groups[(g, name)]
        bucket["in" if b < split else "out"].append(o)
        bucket["regimes"][_regime(fr, b, d)].append(o)
    base = {"in": [], "out": []}
    for b in range(max(start_index, 50), n - horizon, 3):
        for d in (1, -1):
            o = _outcome(fr, b, d, horizon)
            if o:
                base["in" if b < split else "out"].append(o)
    rows = []
    for (g, name), v in sorted(groups.items()):
        rows.append({"group": g, "name": name, "in_sample": _stats(v["in"]), "out_of_sample": _stats(v["out"]),
                     "by_regime": {k: _stats(x) for k, x in v["regimes"].items()}})
    return {
        "timeframe": fr.timeframe, "candles": n - start_index, "horizon_bars": horizon,
        "split_time": fr.iso(min(split, n - 1)), "min_events": MIN_EVENTS,
        "baseline": {"in_sample": _stats(base["in"]), "out_of_sample": _stats(base["out"]),
                     "note": "every 3rd bar, both directions: what no edge looks like"},
        "events": rows,
        "method": (f"Outcome over the next {horizon} closed candles: hit = +1 ATR before -1 ATR (ties count as misses); "
                   "fwd R = signed close-to-close move in ATR. Chronological split, no parameter fitting."),
    }


ABLATIONS: dict[str, tuple[str, dict[str, Any]]] = {
    "baseline": ("Current settings", {}),
    "no_volume_filter": ("Breakout without the relative-volume requirement", {"breakout": {"min_rvol": 0}}),
    "no_room_filter": ("Without the opposing-zone room filter", {"qualification": {"min_rr": 0}}),
    "no_net_rr_filter": ("Without the net R:R filter", {"qualification": {"min_net_rr": 0}}),
    "breakout_only": ("Breakout strategy only", {"pullback": {"enabled": False}}),
    "pullback_only": ("Pullback strategy only", {"breakout": {"enabled": False}}),
    "atr_stop": ("ATR stop instead of the configured stop", {"stops": {"method": "atr"}}),
    "mtf_filter": ("Higher-timeframe trend as a hard filter", {"mtf": {"enabled": True, "mode": "filter"}}),
}


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def ablation(symbol: str, timeframe: str, candles: pd.DataFrame, htf: dict[str, pd.DataFrame], start: datetime,
             end: datetime, strategy: StrategyParams, base_cfg: dict[str, Any], rules: InstrumentRules | None,
             folds: int = 3, variants: list[str] | None = None) -> dict[str, Any]:
    edges = pd.date_range(start, end, periods=folds + 1)
    out = []
    base_dump = strategy.model_dump(mode="json")
    for key in variants or list(ABLATIONS):
        label, change = ABLATIONS[key]
        try:
            sp = StrategyParams.model_validate(_merge(base_dump, change))
        except ValueError as exc:
            out.append({"variant": key, "label": label, "error": str(exc)})
            continue
        fold_rows = []
        for k in range(folds):
            cfg = BacktestConfig(symbol=symbol, timeframe=timeframe, start=edges[k].to_pydatetime(),
                                 end=edges[k + 1].to_pydatetime(), strategy=sp, include_funding=False, **base_cfg)
            m = run_backtest(cfg, candles, htf, None, rules).metrics
            fold_rows.append({"fold": k + 1, "start": edges[k].isoformat(), "end": edges[k + 1].isoformat(),
                              "trades": m["trades"], "return_pct": round(m["net_return_pct"], 2),
                              "profit_factor": None if m["profit_factor"] is None else round(m["profit_factor"], 2),
                              "win_rate_pct": None if m["win_rate_pct"] is None else round(m["win_rate_pct"], 1)})
        rets = [r["return_pct"] for r in fold_rows]
        out.append({"variant": key, "label": label, "folds": fold_rows, "total_trades": sum(r["trades"] for r in fold_rows),
                    "mean_fold_return_pct": round(float(np.mean(rets)), 2), "worst_fold_return_pct": min(rets)})
    base = next((v for v in out if v["variant"] == "baseline" and "folds" in v), None)
    if base:
        for v in out:
            if v is base or "folds" not in v:
                continue
            better = sum(1 for a, b in zip(base["folds"], v["folds"], strict=True) if a["return_pct"] > b["return_pct"])
            v["baseline_better_in_folds"] = f"{better}/{folds}"
            v["verdict"] = ("removing it made results worse in every fold" if better == folds else
                            "removing it made results better in every fold" if better == 0 else
                            "mixed across folds: no consistent effect")
            if v["total_trades"] < MIN_EVENTS or base["total_trades"] < MIN_EVENTS:
                v["verdict"] += f" (insufficient trades for a conclusion: < {MIN_EVENTS})"
    return {"symbol": symbol, "timeframe": timeframe, "folds": folds, "variants": out,
            "note": "Walk-forward: each fold is evaluated independently; thresholds are the configured defaults, "
                    "not optimised on these folds."}
