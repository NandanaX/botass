"""Historical candidate signals for research.

For every closed candle of every symbol/timeframe the *production* ``evaluate_at`` is run
exactly as the live scanner runs it (closed candles only, higher timeframes closed by the
decision time). Every bar whose best setup is QUALIFIED becomes a candidate, the same rule
that creates a live signal. Candidates overlap in time (the live scanner does not wait for
an earlier signal to resolve); portfolio rules are applied later, in ``portfolio.py``.

Each candidate stores the information available at its decision time (score components,
regime, entry-quality features, higher-timeframe state) and, separately, its hypothetical
cost-aware outcome under four stop methods (``simulate.py``):

* ``A_atr``: entry -/+ 1.5 ATR.
* ``B_structure``: just beyond the setup's structural level (min 0.5 ATR).
* ``C_hybrid``: the production stop (structural level -/+ 0.25 ATR, min 0.5 ATR) and the
  production primary target. This is the baseline.
* ``D_noise``: volatility-adaptive: beyond the 80th percentile of 3-bar adverse moves over
  the previous 100 bars (min 0.5 ATR), ignoring structure.

A, B and D use a 2R target so that only the stop placement changes. R is always the
planned risk, so a wider stop means a proportionally smaller position, not more risk.
Outcome fields are *labels* computed from later candles; they are never used as inputs.
"""

from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.research.entry_quality import EntryQualityParams, classify_entry, entry_features
from app.research.regime import RegimeParams, regime_arrays
from app.research.simulate import CostModel, prepare_funding, simulate_trade
from app.research.strategies import GENERATORS
from app.risk.stops import compute_stop
from app.strategy.engine import evaluate_at
from app.strategy.features import Features, compute_features
from app.strategy.params import StrategyParams
from app.timeutil import TIMEFRAMES, tf_seconds

STOP_METHODS = ["A_atr", "B_structure", "C_hybrid", "D_noise"]
ATR_SENSITIVITY = [1.0, 2.0, 2.5]


def noise_distance(f: Features, i: int, direction: str, window: int = 100, horizon: int = 3, q: float = 0.8) -> float:
    """80th percentile of adverse moves within ``horizon`` bars after each of the previous
    ``window`` closes (only windows fully closed by bar ``i``)."""
    lo_k, hi_k = max(0, i - window - horizon), i - horizon
    if hi_k - lo_k < 20:
        return math.nan
    vals = []
    for k in range(lo_k, hi_k + 1):
        if direction == "LONG":
            vals.append(f.close[k] - np.min(f.low[k + 1:k + horizon + 1]))
        else:
            vals.append(np.max(f.high[k + 1:k + horizon + 1]) - f.close[k])
    return float(np.quantile(np.maximum(vals, 0), q))


def _outcome(f: Features, i: int, direction: str, stop: float, target: float, max_bars: int, costs: CostModel,
             funding: pd.DataFrame | None, bar_ns: int) -> dict[str, Any]:
    o = simulate_trade(direction, f.open, f.high, f.low, f.close, f.open_time, bar_ns, i, stop, target, max_bars,
                       costs, funding, allow_open=False)
    d = o.to_dict()
    d["stop"] = stop
    d["target"] = target
    d["stop_atr"] = abs(f.close[i] - stop) / f.atr[i]
    d["exit_close_ns"] = int(f.open_time[o.exit_index]) + bar_ns if o.status == "FILLED" else None
    return d


def _htf_frames(htf: dict[str, Features], decision_ns: int) -> dict[str, int]:
    return {tf: int(np.searchsorted(hf.close_time, decision_ns, side="right")) - 1 for tf, hf in htf.items()}


def generate_for(symbol: str, tf: str, df: pd.DataFrame, htf_dfs: dict[str, pd.DataFrame], params: StrategyParams,
                 funding: pd.DataFrame | None, costs: CostModel, max_bars: int, start_ns: int,
                 rp: RegimeParams | None = None, ep: EntryQualityParams | None = None,
                 research_families: bool = True) -> list[dict[str, Any]]:
    f = compute_features(df, tf, params)
    htf = {k: compute_features(v, k, params) for k, v in htf_dfs.items() if len(v)}
    ra = regime_arrays(f, rp)
    hra = {k: regime_arrays(v, rp) for k, v in htf.items()}
    fund = prepare_funding(funding)
    bar_ns = tf_seconds(tf) * 1_000_000_000
    first = max(int(np.searchsorted(f.open_time, start_ns)), params.warmup_bars)
    out: list[dict[str, Any]] = []
    for i in range(first, len(f) - 1):
        ev = evaluate_at(f, i, params, htf, context=False)
        st = ev.setup
        rows: list[tuple[str, str, float, float, float | None, dict[str, Any]]] = []
        if st is not None:
            rows.append((st.strategy, st.direction, float(st.stop_loss), float(st.take_profit),
                         (st.plan.get("stop") or {}).get("structural_level"),
                         {"score": st.score, "mtf_confirmed": st.mtf_confirmed, "room_r": st.plan.get("room_r"),
                          "components": {c.key: (round(c.points / c.max_points, 4) if c.max_points else None)
                                         for c in st.score_components if c.evaluated},
                          "htf": {h.timeframe: h.trend for h in st.mtf_details},
                          "zone_tf": (st.plan.get("zone") or {}).get("timeframe"),
                          "net_rr": st.plan.get("net_risk_reward")}))
        if research_families:
            for name, gen in GENERATORS.items():
                r = gen(f, ra, i)
                if r is not None:
                    d, stop, target, level, note = r
                    rows.append((name, d, stop, target, level, {"score": None, "mtf_confirmed": None, "room_r": None,
                                                                "components": {}, "htf": {}, "note": note}))
        if not rows:
            continue
        hidx = _htf_frames(htf, int(f.close_time[i]))
        htf_regime = {k: (str(hra[k].label[j]) if j >= 0 else "INSUFFICIENT_DATA") for k, j in hidx.items()}
        for strategy, direction, stop, target, level, meta in rows:
            atr = float(f.atr[i])
            entry = float(f.close[i])
            feat = entry_features(f, ra, i, direction, meta.get("room_r"),
                                  level if strategy == "breakout" else None)
            ecls, ewhy = classify_entry(feat, direction, ep)
            outcomes: dict[str, Any] = {}
            if strategy in ("breakout", "pullback"):
                outcomes["C_hybrid"] = _outcome(f, i, direction, stop, target, max_bars, costs, fund, bar_ns)
                sign = 1 if direction == "LONG" else -1
                a = compute_stop(direction, entry, atr, "atr", None, 1.5)
                outcomes["A_atr"] = _outcome(f, i, direction, a.price, entry + sign * 2 * a.distance, max_bars, costs,
                                             fund, bar_ns)
                for mult in ATR_SENSITIVITY:  # parameter sensitivity of the ATR stop
                    am = compute_stop(direction, entry, atr, "atr", None, mult)
                    outcomes[f"A_atr_{mult:g}"] = _outcome(f, i, direction, am.price, entry + sign * 2 * am.distance,
                                                           max_bars, costs, fund, bar_ns)
                b = compute_stop(direction, entry, atr, "structure", level, min_stop_atr=0.5)
                outcomes["B_structure"] = _outcome(f, i, direction, b.price, entry + sign * 2 * b.distance, max_bars,
                                                   costs, fund, bar_ns)
                nd = noise_distance(f, i, direction)
                dist = max(0.5 * atr, nd if math.isfinite(nd) else 1.5 * atr)
                dstop = entry - sign * dist
                outcomes["D_noise"] = _outcome(f, i, direction, dstop, entry + sign * 2 * dist, max_bars, costs, fund,
                                               bar_ns)
            else:
                outcomes["C_hybrid"] = _outcome(f, i, direction, stop, target, max_bars, costs, fund, bar_ns)
            out.append({
                "symbol": symbol, "timeframe": tf, "strategy": strategy,
                "family": "production" if strategy in ("breakout", "pullback") else "research",
                "signal_ns": int(f.open_time[i]), "decision_ns": int(f.close_time[i]), "index": i,
                "direction": direction, "entry_ref": entry, "atr": atr, "atr_pct": 100 * atr / entry,
                "regime": ra.at(i), "htf_regime": htf_regime, "entry": feat, "entry_class": ecls,
                "entry_reasons": ewhy, **meta, "outcomes": outcomes,
            })
    return out


def _job(args: tuple) -> list[dict[str, Any]]:
    (symbol, tf, cache_dir, htf_tfs, params_json, costs, max_bars, start_ns, research_families) = args
    params = StrategyParams.model_validate_json(params_json)
    cache = Path(cache_dir)
    df = pd.read_pickle(cache / f"{symbol}_{tf}.pkl")
    htf_dfs = {h: pd.read_pickle(cache / f"{symbol}_{h}.pkl") for h in htf_tfs
               if TIMEFRAMES[h] > TIMEFRAMES[tf] and (cache / f"{symbol}_{h}.pkl").exists()}
    fp = cache / f"{symbol}_funding.pkl"
    funding = pd.read_pickle(fp) if fp.exists() else None
    return generate_for(symbol, tf, df, htf_dfs, params, funding, costs, max_bars, start_ns,
                        research_families=research_families)


def generate_all(symbols: list[str], timeframes: list[str], cache_dir: Path, params: StrategyParams, costs: CostModel,
                 max_bars: int, start_ns: int, workers: int = 6, research_families: bool = True
                 ) -> list[dict[str, Any]]:
    htf_tfs = sorted(set(params.zones.htf_timeframes) | set(params.mtf.timeframes), key=lambda t: TIMEFRAMES[t])
    jobs = [(s, tf, str(cache_dir), htf_tfs, params.model_dump_json(), costs, max_bars, start_ns, research_families)
            for s in symbols for tf in timeframes if (cache_dir / f"{s}_{tf}.pkl").exists()]
    out: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for rows in ex.map(_job, jobs):
            out.extend(rows)
    out.sort(key=lambda r: (r["decision_ns"], r["symbol"], r["timeframe"]))
    for k, r in enumerate(out):
        r["id"] = k
    return out
