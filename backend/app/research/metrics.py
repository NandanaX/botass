"""Performance statistics on R-multiple trade lists (net of costs unless stated).

Every function takes trades as dicts with ``net_r`` and ``exit_close_ns`` (and optionally
``symbol``). Max drawdown is computed on the cumulative net-R curve ordered by exit time,
i.e. in units of "risk per trade"; with fixed fractional risk of ``x``%% per trade a drawdown
of ``D`` R is roughly ``D * x``%% of equity.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np

MIN_TRADES = 30


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(100 * (c - h), 2), round(100 * (c + h), 2))


def bootstrap_mean_ci(x: np.ndarray, reps: int = 2000, seed: int = 7) -> tuple[float, float] | None:
    if len(x) < 2:
        return None
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(reps, len(x)), replace=True).mean(axis=1)
    return (round(float(np.quantile(means, 0.025)), 4), round(float(np.quantile(means, 0.975)), 4))


def summarize(trades: list[dict[str, Any]], key: str = "net_r") -> dict[str, Any]:
    rs = np.array([t[key] for t in trades if t.get(key) is not None and math.isfinite(t[key])], float)
    n = len(rs)
    if n == 0:
        return {"n": 0, "insufficient_data": True}
    order = np.argsort([t.get("exit_close_ns") or 0 for t in trades if t.get(key) is not None])
    seq = rs[order]
    curve = np.cumsum(seq)
    peak = np.maximum.accumulate(np.concatenate([[0.0], curve]))[1:]
    dd = float(np.max(peak - curve)) if n else 0.0
    wins = int((rs > 0).sum())
    gp, gl = float(rs[rs > 0].sum()), float(-rs[rs < 0].sum())
    streak = worst = 0
    for r in seq:
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
    return {
        "n": n,
        "insufficient_data": n < MIN_TRADES,
        "win_rate_pct": round(100 * wins / n, 2),
        "win_rate_ci95": wilson(wins, n),
        "avg_r": round(float(rs.mean()), 4),
        "avg_r_ci95": bootstrap_mean_ci(rs),
        "median_r": round(float(np.median(rs)), 4),
        "total_r": round(float(rs.sum()), 3),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
        "max_drawdown_r": round(dd, 3),
        "worst_losing_streak": worst,
        "std_r": round(float(rs.std(ddof=1)), 4) if n > 1 else None,
    }


def by_group(trades: list[dict[str, Any]], keyfn, key: str = "net_r") -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        groups[str(keyfn(t))].append(t)
    return {g: summarize(v, key) for g, v in sorted(groups.items())}


def monthly(trades: list[dict[str, Any]]) -> dict[str, float]:
    import pandas as pd
    out: dict[str, float] = defaultdict(float)
    for t in trades:
        if t.get("net_r") is None or t.get("exit_close_ns") is None:
            continue
        m = pd.Timestamp(int(t["exit_close_ns"]), tz="UTC").strftime("%Y-%m")
        out[m] += t["net_r"]
    return {k: round(v, 3) for k, v in sorted(out.items())}
