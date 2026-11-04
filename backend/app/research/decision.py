"""Real-time Signal Decision Engine (runs on every QUALIFIED production setup).

Twelve ordered checks; each is typed so that a rejection never claims more than the
evidence supports:

* ``risk_control``: portfolio/data rules that limit exposure. They are justified by risk,
  not by a claim that they raise expectancy.
* ``validated_filter``: a change that passed the experiment framework (``experiments.py``)
  and is listed in the latest study's validated or combined set. Only these may be
  described as performance-improving.
* ``diagnostic``: measured context (entry timing, regime, levels). Shown, never blocking,
  because history did not show that blocking on it improves results.

Outcomes: ``QUALIFIED`` (all blocking checks pass), ``WATCHLIST``/``REJECTED`` (production
status passes through, or a blocking check failed), ``NO_TRADE`` (the data cannot support
a decision, e.g. insufficient history for the regime classifier).

Mode ``shadow`` (default): the decision is stored with the signal and shown in Telegram,
but nothing is blocked. Mode ``enforce``: Telegram alerts are sent only for QUALIFIED
decisions. Changing the mode is a manual settings change; the study never promotes itself.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.research.data import DEFAULT_CACHE
from app.research.entry_quality import classify_entry, entry_features
from app.research.regime import regime_alignment, regime_arrays
from app.research.settings import DecisionSettings
from app.strategy.engine import Evaluation
from app.strategy.features import Features

ENGINE_VERSION = "decision-1.0.0"
COST_PCT_DEFAULT = 2 * 0.055 + 0.02 + 0.05


# ----------------------------------------------------------------------------- study filters

def _filter(key: str, params: dict[str, Any]):
    """Decision-time predicates for each hypothesis key (same definitions as experiments)."""
    p = params or {}
    table = {
        "H1a": lambda t: t["entry_class"] not in ("EXTENDED", "EXHAUSTION_RISK"),
        "H1b": lambda t: t["entry_class"] != "EXHAUSTION_RISK",
        "H1c": lambda t: p.get("threshold_atr") is None or (t["entry"]["ext_fast_atr"] or 0) < p["threshold_atr"],
        "H1d": lambda t: p.get("min_swing_room_atr") is None or not (
            t["entry"]["swing_room_atr"] is not None and 0 <= t["entry"]["swing_room_atr"] < p["min_swing_room_atr"]),
        "H4": lambda t: t.get("mtf_confirmed") is True,
        "H4e": lambda t: t.get("mtf_confirmed") is not False,
        "H4b": lambda t: t["htf_alignment_4h"] != "AGAINST",
        "H4c": lambda t: t["btc_alignment"] != "AGAINST",
        "H4d": lambda t: t.get("cluster_prior", 0) == 0,
        "H6": lambda t: p.get("max_cost_r") is None or t["est_cost_r"] <= p["max_cost_r"],
        "H7": lambda t: p.get("min_score") is None or (t.get("score") or 0) >= p["min_score"],
        "H8": lambda t: t["timeframe"] != "4h",
        "H5": lambda t: f"{t['strategy']} in {t['regime']['regime']}" in set(p.get("allowed_cells") or []),
    }
    return table.get(key)


_STUDY_CACHE: dict[str, Any] = {"mtime": None, "data": None}


def active_study_filters(cache: Path = DEFAULT_CACHE) -> dict[str, Any]:
    """Changes from the latest study that may be applied in shadow mode: the combined set
    when its verdict is VALIDATED, else the individually VALIDATED changes."""
    path = cache / "study_latest.json"
    if not path.exists():
        return {"available": False, "keys": [], "params": {}, "stop_method": None}
    m = path.stat().st_mtime
    if _STUDY_CACHE["mtime"] != m:
        study = json.loads(path.read_text(encoding="utf-8"))
        params = {h["key"]: {"params": h["params"], "title": h["title"], "verdict": h["verdict"]}
                  for h in study.get("hypotheses", [])}
        comb = study.get("combined") or {}
        keys = comb.get("keys", []) if comb.get("verdict") == "VALIDATED" else study.get("validated_changes", [])
        stop = next((params[k]["params"].get("method") for k in keys if k.startswith("H2") and k in params), None)
        _STUDY_CACHE.update(mtime=m, data={
            "available": True, "keys": keys, "params": params, "stop_method": stop,
            "combined_verdict": comb.get("verdict"),
            "positive_expectancy": bool((comb.get("results") or {}).get("positive_expectancy")),
            "generated_at": study.get("meta", {}).get("generated_at"),
        })
    return _STUDY_CACHE["data"]


# ----------------------------------------------------------------------------- live context

def live_correlation(closes: dict[str, tuple[np.ndarray, np.ndarray]], a: str, b: str, window: int) -> float:
    """Correlation of hourly log returns over the last ``window`` common closed candles."""
    if a == b:
        return 1.0
    if a not in closes or b not in closes:
        return math.nan
    ta, ca = closes[a]
    tb, cb = closes[b]
    common, ia, ib = np.intersect1d(ta, tb, return_indices=True)
    if len(common) < window + 1:
        return math.nan
    ra = np.diff(np.log(ca[ia][-(window + 1):]))
    rb = np.diff(np.log(cb[ib][-(window + 1):]))
    if ra.std() == 0 or rb.std() == 0:
        return math.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def decide(
    ev: Evaluation, f: Features, symbol: str, cfg: DecisionSettings, *, data_ok: bool,
    open_positions: list[dict[str, Any]], realized_today_r: float, losing_streak: int, last_loss_ns: int | None,
    corr_fn, btc_features: Features | None, htf4h_features: Features | None,
    study: dict[str, Any] | None = None, cost_pct: float = COST_PCT_DEFAULT, now_ns: int | None = None,
    cluster_prior: int = 0,
) -> dict[str, Any]:
    st = ev.setup or (ev.setups[0] if ev.setups else None)
    checks: list[dict[str, Any]] = []

    def chk(step: int, name: str, status: str, rule: str, detail: str) -> None:
        checks.append({"step": step, "check": name, "status": status, "rule_type": rule, "detail": detail})

    def result(outcome: str, reason: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"engine_version": ENGINE_VERSION, "mode": cfg.mode, "outcome": outcome, "reason": reason,
                "checks": checks, **(extra or {})}

    i = ev.index
    chk(1, "Data integrity", "pass" if data_ok else "fail", "risk_control",
        "closed candles current and without recent gaps" if data_ok else "gaps or stale data near the signal candle")
    if not data_ok:
        return result("REJECTED", "data integrity check failed")
    if st is None:
        return result("NO_TRADE", "no setup")
    direction = st.direction
    ra = regime_arrays(f, cfg.regime)
    reg = ra.at(i)
    align = regime_alignment(reg["regime"], direction)
    if reg["regime"] == "INSUFFICIENT_DATA":
        chk(2, "Market regime", "fail", "risk_control", "not enough history to classify the regime")
        return result("NO_TRADE", "insufficient data to classify the market regime")
    chk(2, "Market regime", "warn" if align == "AGAINST" or reg["regime"] == "CONFLICTED" else "pass", "diagnostic",
        f"{reg['label']} (ADX {reg['adx']}, efficiency {reg['efficiency']}); {align.lower()} the {direction}")
    if st.status != "QUALIFIED":
        chk(3, "Strategy eligibility", "fail", "risk_control", f"production setup status {st.status}")
        return result(st.status, st.reasons[0]["message"] if st.reasons else f"setup {st.status}")
    study = study if study is not None else (active_study_filters() if cfg.use_study_filters else {"keys": []})
    keys = study.get("keys", []) if cfg.use_study_filters else []
    chk(3, "Strategy eligibility", "pass", "diagnostic",
        f"{st.strategy} QUALIFIED by the production rules"
        + ("" if "H5" in keys else "; regime-based strategy selection was not validated, so all regimes are eligible"))
    # 4 higher timeframes
    h4 = "NEUTRAL"
    if htf4h_features is not None:
        j = int(np.searchsorted(htf4h_features.close_time, int(f.close_time[i]), side="right")) - 1
        if j >= 0:
            h4 = regime_alignment(str(regime_arrays(htf4h_features, cfg.regime).label[j]), direction)
    btc_align = "SELF" if symbol == "BTCUSDT" else "NEUTRAL"
    if btc_features is not None and symbol != "BTCUSDT":
        j = int(np.searchsorted(btc_features.close_time, int(f.close_time[i]), side="right")) - 1
        if j >= 0:
            btc_align = regime_alignment(str(regime_arrays(btc_features, cfg.regime).label[j]), direction)
    mtf = st.mtf_confirmed
    chk(4, "Higher-timeframe analysis", "pass" if mtf else "warn",
        "validated_filter" if ("H4" in keys or "H4e" in keys) else "diagnostic",
        f"EMA trend agreement {'yes' if mtf else 'no' if mtf is False else 'n/a (no higher timeframe)'}; "
        f"4h regime {h4.lower()}; BTC {btc_align.lower()}")
    s = f.structure
    struct_ok = int(s.structure[i]) == (1 if direction == "LONG" else -1)
    chk(5, "Market structure", "pass" if struct_ok else "warn", "diagnostic",
        "swing structure confirms the direction" if struct_ok else "swing structure does not confirm (mixed)")
    room = st.plan.get("room_r")
    feat = entry_features(f, ra, i, direction, room,
                          (st.plan.get("stop") or {}).get("structural_level") if st.strategy == "breakout" else None)
    ecls, ewhy = classify_entry(feat, direction, cfg.entry_quality)
    chk(6, "Entry timing", "warn" if ecls in ("EXTENDED", "EXHAUSTION_RISK", "INVALID") else "pass",
        "validated_filter" if any(k.startswith("H1") for k in keys) else "diagnostic",
        f"{ecls}: {'; '.join(ewhy)}"
        + ("" if any(k.startswith("H1") for k in keys) else " (filtering late entries did not improve historical "
                                                              "results, so this is not blocking)"))
    sw = feat.get("swing_room_atr")
    chk(7, "Nearby support/resistance", "warn" if (sw is not None and 0 <= sw < 1.0) or (room is not None and room < 1.5)
        else "pass", "diagnostic",
        f"room to opposing zone {f'{room:.1f}R' if room is not None else 'unbounded'}; last opposing swing "
        f"{f'{sw:.2f} ATR' if sw is not None else 'n/a'}")
    stop = st.plan.get("stop") or {}
    atr = float(f.atr[i])
    stop_pct = 100 * abs(st.entry_reference - st.stop_loss) / st.entry_reference
    est_cost = cost_pct / stop_pct if stop_pct > 0 else 99.0
    chk(8, "Stop-loss feasibility", "warn" if est_cost > 0.3 else "pass",
        "validated_filter" if "H6" in keys else "diagnostic",
        f"{stop.get('method')} stop {stop.get('distance_atr', 0):.2f} ATR ({stop_pct:.2f}% of price); estimated "
        f"round-trip cost {est_cost:.2f}R if stopped")
    nrr = st.plan.get("net_risk_reward")
    chk(9, "Reward/risk", "pass", "risk_control",
        f"primary target {st.risk_reward:.2f}R gross, {nrr:.2f}R after estimated costs" if nrr is not None
        else f"primary target {st.risk_reward:.2f}R")
    # 10/11 portfolio
    pp = cfg.portfolio
    same = [p for p in open_positions if p["direction"] == direction]
    corr_hits = []
    for p in same:
        c = corr_fn(symbol, p["symbol"]) if corr_fn else math.nan
        if p["symbol"] == symbol or (math.isfinite(c) and c >= pp.corr_threshold):
            corr_hits.append(f"{p['symbol']} {p['timeframe']} ({'same coin' if p['symbol'] == symbol else f'corr {c:.2f}'})")
    blocking: list[str] = []
    if pp.max_correlated and len(corr_hits) >= pp.max_correlated:
        blocking.append(f"correlated exposure: already {direction} in {', '.join(corr_hits)}")
    chk(10, "Correlation exposure", "fail" if blocking else "pass", "risk_control",
        blocking[0] if blocking else f"{len(same)} open {direction} position(s), none correlated >= {pp.corr_threshold:g}")
    pf = []
    if pp.one_per_symbol and any(p["symbol"] == symbol for p in open_positions):
        pf.append(f"{symbol} already has an open shadow position (repeated signal, not an independent trade)")
    if pp.max_positions and len(open_positions) >= pp.max_positions:
        pf.append(f"{len(open_positions)} open positions (max {pp.max_positions})")
    if pp.max_same_direction and len(same) >= pp.max_same_direction:
        pf.append(f"{len(same)} open {direction} positions (max {pp.max_same_direction})")
    if pp.max_daily_loss_r and realized_today_r <= -pp.max_daily_loss_r:
        pf.append(f"daily loss limit reached ({realized_today_r:.2f}R today, limit -{pp.max_daily_loss_r:g}R)")
    if (pp.max_consecutive_losses and losing_streak >= pp.max_consecutive_losses and last_loss_ns is not None
            and now_ns is not None and now_ns - last_loss_ns < pp.cooldown_hours * 3_600_000_000_000):
        pf.append(f"{losing_streak} consecutive losses; cooling down for {pp.cooldown_hours} h")
    chk(11, "Portfolio risk limits", "fail" if pf else "pass", "risk_control",
        "; ".join(pf) if pf else f"{len(open_positions)} open shadow position(s); today {realized_today_r:+.2f}R")
    blocking += pf
    # 12 validated filters
    ctx = {"entry_class": ecls, "entry": feat, "mtf_confirmed": mtf, "htf_alignment_4h": h4, "btc_alignment": btc_align,
           "timeframe": f.timeframe, "est_cost_r": est_cost, "score": st.score, "strategy": st.strategy,
           "regime": {"regime": reg["regime"]}, "cluster_prior": cluster_prior}
    failed_filters = []
    for k in keys:
        fn = _filter(k, (study.get("params", {}).get(k) or {}).get("params", {}))
        if fn is not None and not fn(ctx):
            failed_filters.append(f"{k} {(study['params'].get(k) or {}).get('title', '')}")
    proposed = None
    sm = study.get("stop_method")
    if sm and sm.startswith("A_atr"):
        mult = {"A_atr": 1.5, "A_atr_1": 1.0, "A_atr_2": 2.0, "A_atr_2.5": 2.5}.get(sm, 1.5)
        sign = 1 if direction == "LONG" else -1
        d = mult * atr
        proposed = {"stop_method": f"ATR stop ({mult:g} x ATR)", "stop_loss": st.entry_reference - sign * d,
                    "take_profit": st.entry_reference + sign * 2 * d,
                    "note": "shadow plan from the validated combination; position size must be recalculated for the "
                            "wider stop so the risk stays the same; the production plan is unchanged"}
    chk(12, "Validated strategy-quality filters", "fail" if failed_filters else "pass", "validated_filter",
        ("failed: " + "; ".join(failed_filters)) if failed_filters else
        (f"passes {', '.join(keys)}" if keys else "no validated filter is active"))
    extra = {"context": {"regime": reg, "entry_class": ecls, "est_cost_r": round(est_cost, 3), "htf_4h": h4,
                         "btc": btc_align}, "study_keys": keys, "proposed_plan": proposed}
    if blocking:
        return result("REJECTED", f"{direction} rejected (risk control): {blocking[0]}", extra)
    if failed_filters:
        return result("REJECTED", f"{direction} rejected by validated filter: {failed_filters[0]}", extra)
    return result("QUALIFIED", "all blocking checks passed", extra)


def shadow_state(signals: list[dict[str, Any]], now_ns: int) -> dict[str, Any]:
    """Open shadow positions and realized results from stored signals whose decision was
    QUALIFIED (the shadow portfolio)."""
    taken = [s for s in signals if ((s.get("plan") or {}).get("decision") or {}).get("outcome") == "QUALIFIED"]
    open_pos = [{"symbol": s["symbol"], "timeframe": s["timeframe"], "direction": s["direction"],
                 "opened_ns": pd.Timestamp(s["candle_close_time"]).value} for s in taken
                if s["evaluation_status"] == "PENDING"]
    closed = []
    for s in taken:
        ev = s.get("evaluation") or {}
        r = ev.get("net_r_multiple", ev.get("r_multiple"))
        if s["evaluation_status"] != "PENDING" and r is not None and ev.get("exit_time"):
            closed.append((pd.Timestamp(ev["exit_time"]).value, float(r)))
    closed.sort()
    day = pd.Timestamp(now_ns, tz="UTC").floor("D").value
    today = sum(r for t, r in closed if t >= day)
    streak = 0
    last_loss = None
    for t, r in reversed(closed):
        if r > 0:
            break
        streak += 1
        last_loss = last_loss or t
    return {"open": open_pos, "realized_today_r": today, "losing_streak": streak, "last_loss_ns": last_loss}
