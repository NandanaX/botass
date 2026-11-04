"""Statistics for dependent, correlated trade outcomes (historical and forward).

Trades are grouped into **clusters**: same-direction trades whose decision times are
chained within ``window`` (default 60 minutes) of each other. Several coins reacting to one
BTC move become one cluster, so they count as one observation, not many.

* ``cluster_bootstrap``: resamples whole clusters to get the uncertainty of the average net
  R, the win rate and the profit factor, plus P(average net R > 0).
* ``drawdown_distribution``: circular block bootstrap over the time-ordered sequence of
  cluster results (blocks of ``block`` clusters keep losing streaks together). It gives
  drawdown quantiles in R and P(max drawdown > threshold) **under the stated assumption**
  that the future resembles resampled blocks of this history.
* ``period_consistency``: monthly/weekly results, the share of total positive R produced by
  the best month, and results in three equal time sub-periods.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

HOUR_NS = 3_600_000_000_000


def assign_clusters(trades: list[dict[str, Any]], window_ns: int = HOUR_NS) -> list[int]:
    order = sorted(range(len(trades)), key=lambda k: trades[k]["decision_ns"])
    ids = [0] * len(trades)
    last: dict[str, tuple[int, int]] = {}  # direction -> (last decision ns, cluster id)
    nxt = 0
    for k in order:
        t = trades[k]
        d = t["direction"]
        prev = last.get(d)
        if prev is not None and t["decision_ns"] - prev[0] <= window_ns:
            cid = prev[1]
        else:
            cid, nxt = nxt, nxt + 1
        ids[k] = cid
        last[d] = (t["decision_ns"], cid)
    return ids


def _cluster_arrays(trades: list[dict[str, Any]], window_ns: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[int]]:
    ids = assign_clusters(trades, window_ns)
    sums: dict[int, float] = defaultdict(float)
    cnt: dict[int, int] = defaultdict(int)
    wins: dict[int, int] = defaultdict(int)
    first: dict[int, int] = {}
    for t, c in zip(trades, ids, strict=True):
        sums[c] += t["net_r"]
        cnt[c] += 1
        wins[c] += t["net_r"] > 0
        first[c] = min(first.get(c, t["decision_ns"]), t["decision_ns"])
    keys = sorted(sums, key=lambda c: first[c])
    return (np.array([sums[c] for c in keys]), np.array([cnt[c] for c in keys]),
            np.array([wins[c] for c in keys]), keys)


def cluster_bootstrap(trades: list[dict[str, Any]], reps: int = 2000, seed: int = 3, window_ns: int = HOUR_NS,
                      gains_losses: bool = True) -> dict[str, Any]:
    if not trades:
        return {"n_trades": 0, "n_clusters": 0}
    s, n, w, _ = _cluster_arrays(trades, window_ns)
    rs = np.array([t["net_r"] for t in trades])
    C = len(s)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, C, size=(reps, C))
    mean = s[pick].sum(1) / np.maximum(n[pick].sum(1), 1)
    wr = w[pick].sum(1) / np.maximum(n[pick].sum(1), 1)
    out = {
        "n_trades": len(trades), "n_clusters": int(C),
        "avg_net_r": round(float(rs.mean()), 4),
        "avg_net_r_ci95": [round(float(np.quantile(mean, 0.025)), 4), round(float(np.quantile(mean, 0.975)), 4)],
        "prob_avg_positive": round(float((mean > 0).mean()), 4),
        "win_rate_pct": round(100 * float((rs > 0).mean()), 2),
        "win_rate_ci95": [round(100 * float(np.quantile(wr, 0.025)), 2), round(100 * float(np.quantile(wr, 0.975)), 2)],
    }
    if gains_losses:
        # Profit factor needs per-trade gains/losses inside each cluster.
        ids = assign_clusters(trades, window_ns)
        g: dict[int, float] = defaultdict(float)
        lo: dict[int, float] = defaultdict(float)
        for t, c in zip(trades, ids, strict=True):
            if t["net_r"] > 0:
                g[c] += t["net_r"]
            else:
                lo[c] -= t["net_r"]
        _, _, _, keys = _cluster_arrays(trades, window_ns)
        ga = np.array([g[c] for c in keys])
        la = np.array([lo[c] for c in keys])
        pf = ga[pick].sum(1) / np.maximum(la[pick].sum(1), 1e-12)
        tot_l = la.sum()
        out["profit_factor"] = round(float(ga.sum() / tot_l), 3) if tot_l > 0 else None
        out["profit_factor_ci95"] = [round(float(np.quantile(pf, 0.025)), 3), round(float(np.quantile(pf, 0.975)), 3)]
    return out


def drawdown_distribution(trades: list[dict[str, Any]], thresholds: list[float] = (10, 20, 30, 50), reps: int = 2000,
                          block: int = 5, seed: int = 9, window_ns: int = HOUR_NS) -> dict[str, Any]:
    if not trades:
        return {"n_clusters": 0}
    s, _, _, _ = _cluster_arrays(trades, window_ns)
    C = len(s)
    rng = np.random.default_rng(seed)
    nblocks = math.ceil(C / block)
    starts = rng.integers(0, C, size=(reps, nblocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(reps, -1)[:, :C] % C
    paths = np.cumsum(s[idx], axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.zeros((reps, 1)), paths], axis=1), axis=1)[:, 1:]
    dd = (peak - paths).max(axis=1)
    hist_curve = np.cumsum(s)
    hist_peak = np.maximum.accumulate(np.concatenate([[0.0], hist_curve]))[1:]
    return {
        "n_clusters": int(C), "block_clusters": block, "paths": reps,
        "historical_max_dd_r": round(float((hist_peak - hist_curve).max()), 3),
        "dd_quantiles_r": {q: round(float(np.quantile(dd, q / 100)), 3) for q in (50, 90, 95, 99)},
        "prob_dd_beyond": {f"{t:g}R": round(float((dd > t).mean()), 4) for t in thresholds},
        "assumption": (f"circular block bootstrap of {C} time-ordered cluster results in blocks of {block}; "
                       "assumes future clusters resemble resampled blocks of this sample"),
    }


def period_consistency(trades: list[dict[str, Any]], key: str = "exit_close_ns") -> dict[str, Any]:
    rows = [t for t in trades if t.get(key) is not None]
    if not rows:
        return {"months": {}, "weeks": {}}
    df = pd.DataFrame({"t": pd.to_datetime([int(t[key]) for t in rows], utc=True), "r": [t["net_r"] for t in rows]})
    month = df.groupby(df["t"].dt.strftime("%Y-%m"))["r"].agg(["sum", "count", "mean"])
    week = df.groupby(df["t"].dt.strftime("%G-W%V"))["r"].agg(["sum", "count", "mean"])
    pos = month["sum"][month["sum"] > 0]
    best_share = float(pos.max() / pos.sum()) if len(pos) else None
    ts = df["t"].astype("int64").to_numpy()
    edges = np.linspace(ts.min(), ts.max() + 1, 4)
    thirds = []
    for a, b in zip(edges[:-1], edges[1:], strict=False):
        m = (ts >= a) & (ts < b)
        r = df["r"].to_numpy()[m]
        thirds.append({"from": pd.Timestamp(int(a), tz="UTC").isoformat(), "n": int(m.sum()),
                       "avg_net_r": round(float(r.mean()), 4) if m.sum() else None,
                       "total_r": round(float(r.sum()), 3)})
    return {
        "months": {k: {"total_r": round(float(v["sum"]), 3), "n": int(v["count"]), "avg_r": round(float(v["mean"]), 4)}
                   for k, v in month.iterrows()},
        "weeks": {k: {"total_r": round(float(v["sum"]), 3), "n": int(v["count"])} for k, v in week.iterrows()},
        "positive_months": int((month["sum"] > 0).sum()), "months_total": int(len(month)),
        "best_month_share_of_positive_r": round(best_share, 4) if best_share is not None else None,
        "thirds": thirds,
        "positive_thirds": sum(1 for x in thirds if x["avg_net_r"] is not None and x["avg_net_r"] > 0),
    }


def full_report(trades: list[dict[str, Any]], dd_thresholds: list[float] = (10, 20, 30, 50)) -> dict[str, Any]:
    from app.research.metrics import summarize
    return {"summary": summarize(trades), "bootstrap": cluster_bootstrap(trades),
            "drawdown": drawdown_distribution(trades, dd_thresholds), "consistency": period_consistency(trades)}


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values (for repeated testing of several variants)."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    out, running = {}, 0.0
    for k, (name, p) in enumerate(items):
        running = max(running, min(1.0, (m - k) * p))
        out[name] = round(running, 4)
    return out
