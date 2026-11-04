"""Optional secondary signal-quality model (research, shadow only).

A plain L2-regularised logistic regression (numpy; no extra dependency) estimates the
probability that a candidate signal ends with ``net_r > 0`` after costs. It never creates
signals; it can only be evaluated as a filter on top of the deterministic strategy.

Leakage controls:

* Features are the decision-time fields of each candidate (no outcome fields).
* Time-ordered splits only. Training rows whose label horizon (``exit_close_ns``) reaches
  into the evaluation window minus an ``embargo`` are **purged**.
* Standardisation statistics come from the training rows only.

It is compared with a climatology baseline (always predict the training win rate) using the
Brier score, and with the unfiltered strategy on net R. If there are fewer than
``min_train`` training rows the model is reported as disabled for insufficient data.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

FEATURES = [
    "score", "c_trend", "c_momentum", "c_breakout", "c_volume", "c_higher_timeframe", "c_zone", "c_room",
    "ext_fast_atr", "ext_mid_atr", "run", "accel_atr", "rsi_dir", "rvol", "room_r_cap", "swing_room_cap",
    "momentum_fading", "sweep", "adx", "er", "atr_rank", "atr_ratio", "regime_with", "regime_against",
    "htf4h_with", "htf4h_against", "mtf_confirmed", "btc_with", "btc_against", "is_pullback", "is_short",
    "tf_15m", "tf_1h", "atr_pct",
]


def _num(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def feature_vector(c: dict[str, Any]) -> list[float]:
    e, r, comp = c["entry"], c["regime"], c.get("components") or {}
    short = c["direction"] == "SHORT"
    rsi = _num(e.get("rsi"), 50)
    h4 = c.get("htf_alignment_4h", "NEUTRAL")
    return [
        _num(c.get("score")) / 100,
        _num(comp.get("trend")), _num(comp.get("momentum")), _num(comp.get("breakout")), _num(comp.get("volume")),
        _num(comp.get("higher_timeframe")), _num(comp.get("zone")), _num(comp.get("room")),
        _num(e.get("ext_fast_atr")), _num(e.get("ext_mid_atr")), _num(e.get("run")), _num(e.get("accel_atr")),
        (50 - rsi) / 50 if short else (rsi - 50) / 50, _num(e.get("rvol"), 1),
        min(_num(e.get("room_r"), 10), 10), min(max(_num(e.get("swing_room_atr"), 10), -5), 10),
        float(bool(e.get("momentum_fading"))), float(bool(e.get("sweep"))),
        _num(r.get("adx")) / 50, _num(r.get("efficiency")), _num(r.get("atr_rank"), 0.5), _num(r.get("atr_ratio"), 1),
        float(c.get("regime_alignment") == "WITH"), float(c.get("regime_alignment") == "AGAINST"),
        float(h4 == "WITH"), float(h4 == "AGAINST"), float(bool(c.get("mtf_confirmed"))),
        float(c.get("btc_alignment") == "WITH"), float(c.get("btc_alignment") == "AGAINST"),
        float(c["strategy"] == "pullback"), float(short), float(c["timeframe"] == "15m"), float(c["timeframe"] == "1h"),
        _num(c.get("atr_pct")),
    ]


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 50) -> np.ndarray:
    """Newton-Raphson for L2-regularised logistic regression (intercept unpenalised)."""
    n, d = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    w = np.zeros(d + 1)
    reg = np.full(d + 1, l2)
    reg[0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Xb @ w, -30, 30)))
        g = Xb.T @ (p - y) + reg * w
        H = (Xb * (p * (1 - p))[:, None]).T @ Xb + np.diag(reg)
        step = np.linalg.solve(H + 1e-9 * np.eye(d + 1), g)
        w -= step
        if np.max(np.abs(step)) < 1e-6:
            break
    return w


def predict(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(np.hstack([np.ones((len(X), 1)), X]) @ w, -30, 30)))


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def auc(p: np.ndarray, y: np.ndarray) -> float | None:
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return None
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def calibration_table(p: np.ndarray, y: np.ndarray, bins: int = 5) -> list[dict[str, Any]]:
    edges = np.quantile(p, np.linspace(0, 1, bins + 1)) if len(p) else []
    out = []
    for k in range(bins):
        m = (p >= edges[k]) & ((p <= edges[k + 1]) if k == bins - 1 else (p < edges[k + 1]))
        if m.sum():
            out.append({"bin": k + 1, "n": int(m.sum()), "mean_predicted": round(float(p[m].mean()), 4),
                        "observed_win_rate": round(float(y[m].mean()), 4)})
    return out


def train_and_eval(train: list[dict[str, Any]], test: list[dict[str, Any]], embargo_ns: int, min_train: int = 300,
                   l2: float = 3.0) -> dict[str, Any]:
    if not test:
        return {"enabled": False, "reason": "no evaluation rows"}
    t0 = min(c["decision_ns"] for c in test)
    tr = [c for c in train if c["exit_close_ns"] is not None and c["exit_close_ns"] < t0 - embargo_ns]
    purged = len(train) - len(tr)
    if len(tr) < min_train:
        return {"enabled": False, "reason": f"insufficient training data ({len(tr)} < {min_train} rows after purge)",
                "purged": purged}
    Xtr = np.array([feature_vector(c) for c in tr])
    ytr = np.array([1.0 if c["net_r"] > 0 else 0.0 for c in tr])
    mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0)
    sd[sd == 0] = 1
    w = fit_logistic((Xtr - mu) / sd, ytr, l2)
    # Threshold chosen inside the training window only (last 30% of it, by time).
    k = int(len(tr) * 0.7)
    w_in = fit_logistic((Xtr[:k] - mu) / sd, ytr[:k], l2)
    p_in = predict(w_in, (Xtr[k:] - mu) / sd)
    r_in = np.array([c["net_r"] for c in tr[k:]])
    best_t, best = None, float(np.mean(r_in)) if len(r_in) else -1e9
    for t in np.quantile(p_in, [0.3, 0.4, 0.5, 0.6, 0.7]) if len(p_in) else []:
        m = p_in >= t
        if m.sum() >= 30 and float(np.mean(r_in[m])) > best:
            best_t, best = float(t), float(np.mean(r_in[m]))
    Xte = np.array([feature_vector(c) for c in test])
    yte = np.array([1.0 if c["net_r"] > 0 else 0.0 for c in test])
    p = predict(w, (Xte - mu) / sd)
    clim = np.full(len(yte), ytr.mean())
    coefs = sorted(zip(FEATURES, w[1:].tolist(), strict=True), key=lambda x: -abs(x[1]))[:10]
    return {
        "enabled": True, "train_rows": len(tr), "purged_rows": purged, "test_rows": len(test),
        "brier_model": round(brier(p, yte), 5), "brier_climatology": round(brier(clim, yte), 5),
        "brier_skill": round(1 - brier(p, yte) / brier(clim, yte), 4) if brier(clim, yte) > 0 else None,
        "auc": round(auc(p, yte), 4) if auc(p, yte) is not None else None,
        "threshold": best_t, "calibration": calibration_table(p, yte),
        "top_coefficients": [{"feature": f, "weight": round(v, 4)} for f, v in coefs],
        "probabilities": p.tolist(),
    }
