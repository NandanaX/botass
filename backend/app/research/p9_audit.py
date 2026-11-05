"""Module 1: audit of every stored signal. Reads the database read-only and writes nothing to it.

Each signal is rebuilt from candles available at its decision time, then scored three ways, which are
kept apart in the output because they aren't the same kind of evidence:

* ``stored``: the recorded hypothetical outcome (gross, no costs);
* ``replay``: the same levels, re-simulated with fees, slippage and funding;
* ``paper``: forward paper trades actually filled at live prices, by account.

``python -m app.research.p9_audit``
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from app.jsonutil import jsonable
from app.planning.context import build_context
from app.planning.gate import classify_candidate
from app.planning.stops import STOP_METHODS, plan_stop
from app.research.data import DEFAULT_CACHE
from app.research.forensics import LIVE_WINDOW, reconstruct
from app.research.metrics import summarize
from app.research.p9_candidates import DB_PATH, live_settings
from app.research.p9_exits import simulate_legs
from app.research.regime import regime_arrays
from app.research.robustness import Scenario, load_series
from app.timeutil import TIMEFRAMES, tf_seconds

AUDIT_FILE = "p9_audit.json"
BASE = Scenario()


def _ns(x: Any) -> int:
    t = pd.Timestamp(x)
    return (t.tz_localize("UTC") if t.tzinfo is None else t).value


def load_rows(db: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        sigs = [dict(r) for r in con.execute("select * from signals order by candle_close_time")]
        paper = [dict(r) for r in con.execute("select * from paper_trades")]
    finally:
        con.close()
    for s in sigs:
        for k in ("plan", "evaluation"):
            if isinstance(s.get(k), str):
                s[k] = json.loads(s[k])
    return sigs, paper


def audit_signal(sig: dict[str, Any], paper: list[dict[str, Any]], cache: Path, settings) -> dict[str, Any]:
    tf, symbol, direction = sig["timeframe"], sig["symbol"], sig["direction"]
    params = settings.strategy
    t_open = pd.Timestamp(sig["candle_open_time"])
    t_open = t_open.tz_localize("UTC") if t_open.tzinfo is None else t_open
    df = pd.read_pickle(cache / f"{symbol}_{tf}.pkl")
    htf_tfs = sorted(set(params.zones.htf_timeframes) | set(params.mtf.timeframes), key=lambda t: TIMEFRAMES[t])
    htf = {h: pd.read_pickle(cache / f"{symbol}_{h}.pkl") for h in htf_tfs if TIMEFRAMES[h] > TIMEFRAMES[tf]}
    probe = {"timeframe": tf, "symbol": symbol, "candle_open_time": t_open, "direction": direction,
             "strategy_type": sig.get("strategy_type"), "entry_reference": sig["entry_reference"], "stop_loss": sig["stop_loss"],
             "take_profit": sig["take_profit"], "score": sig["score"]}
    rec = reconstruct(probe, df, htf, params, LIVE_WINDOW)
    entry, stop, target = float(sig["entry_reference"]), float(sig["stop_loss"]), float(sig["take_profit"])
    risk = abs(entry - stop)
    atr = float(sig["atr"])
    costs_pct = 2 * BASE.fee_pct + BASE.entry_slip_pct
    gross_rr = abs(target - entry) / risk
    net_rr = (abs(target - entry) - entry * costs_pct / 100) / risk
    out: dict[str, Any] = {
        "id": sig["id"], "symbol": symbol, "timeframe": tf, "direction": direction, "strategy": sig.get("strategy_type"),
        "signal_candle_close": str(sig["candle_close_time"]), "created_at": str(sig["created_at"]),
        "entry_reference": entry, "stop_loss": stop, "take_profit": target, "atr": atr,
        "entry_to_stop_pct": round(100 * risk / entry, 3), "entry_to_target_pct": round(100 * abs(target - entry) / entry, 3),
        "stop_atr": round(risk / atr, 3), "gross_rr": round(gross_rr, 3), "net_rr_estimate": round(net_rr, 3),
        "cost_share_of_risk_pct": round(100 * (entry * (costs_pct + BASE.stop_slip_pct) / 100) / risk, 1),
        "stored_status": sig["evaluation_status"], "stored_r": (sig.get("evaluation") or {}).get("r_multiple"),
        "reconstruction_matches": rec.get("matches"), "reconstruction_differences": rec.get("differences"),
    }
    s = load_series(cache, symbol, tf)
    idx = int(pd.Series(s.t).searchsorted(t_open.value))
    if idx < len(s.t) and s.t[idx] == t_open.value:
        r = simulate_legs(s, idx, direction, stop, [(target, 1.0)], settings.evaluation.max_bars, BASE, 100 * atr / entry)
        out["replay"] = None if r is None else {k: (round(r[k], 4) if isinstance(r[k], float) else r[k]) for k in
                                                ("net_r", "gross_r", "reason", "mfe_r", "mae_r", "bars_held", "fee_r", "funding_r")}
        if r is not None:
            fund = float(s.fund_r[s.fund_t <= int(s.t[idx]) + s.bar_ns][-1]) if len(s.fund_t) and (s.fund_t <= int(s.t[idx]) + s.bar_ns).any() else None
            out["funding_rate_at_signal_pct"] = round(100 * fund, 4) if fund is not None else None
    if rec.get("ok"):
        f, i = rec["features"], rec["index"]
        ev = rec["evaluation"]
        st = next((x for x in ev.setups if x.direction == direction and x.strategy == sig.get("strategy_type")), None)
        out["market_structure"] = ev.structure
        out["rvol"] = round(float(f.rvol[i]), 2) if f.rvol[i] == f.rvol[i] else None
        if st is not None and st.entry_reference is not None:
            ctx = build_context(f, regime_arrays(f), i, st)
            out["htf_trends"] = ctx.htf_trends
            out["regime"] = ctx.regime
            out["opposing_boundary"] = ctx.opposing_boundary
            out["noise_distance_atr"] = round(ctx.noise_distance / ctx.atr, 3) if ctx.noise_distance else None
            out["stop_inside_noise"] = bool(ctx.noise_distance and risk < ctx.noise_distance)
            gate = classify_candidate(ctx.gate_input(stop))
            out["gate_status"], out["gate_reasons"] = gate.status, [x["message"] for x in gate.reasons]
            per_method = {}
            for m in STOP_METHODS:
                p = plan_stop(m, ctx.stop_context())
                if not p.valid:
                    per_method[m] = {"valid": False}
                    continue
                sign = 1 if direction == "LONG" else -1
                o = simulate_legs(s, idx, direction, p.price, [(entry + sign * 2 * p.distance, 1.0)], settings.evaluation.max_bars, BASE, 100 * atr / entry)
                per_method[m] = {"valid": True, "stop": p.price, "stop_atr": round(p.distance_atr, 2), "gate": classify_candidate(ctx.gate_input(p.price)).status,
                                 "result": None if o is None else {"net_r": round(o["net_r"], 3), "reason": o["reason"]}}
            out["hypothetical_other_stops"] = per_method
    close_ns = _ns(sig["candle_open_time"])
    fills = [p for p in paper if p["symbol"] == symbol and p["timeframe"] == tf and p["direction"] == direction and _ns(p["candle_open_time"]) == close_ns]
    out["paper"] = [{k: p.get(k) for k in ("variant_key", "status", "decision", "decision_reason", "fill_price", "fill_time", "exit_price", "exit_reason", "net_r", "quantity", "risk_amount", "portfolio_taken")} for p in fills]
    return out


def run(cache: Path = DEFAULT_CACHE, db: Path = DB_PATH) -> dict[str, Any]:
    sigs, paper = load_rows(db)
    settings = live_settings(db)
    rows = [audit_signal(s, paper, cache, settings) for s in sigs]
    stored = [r["stored_r"] for r in rows if r["stored_r"] is not None]
    replay = [r["replay"] for r in rows if r.get("replay")]
    closed = [p for p in paper if p["status"] == "CLOSED" and p["net_r"] is not None]
    by_var = {k: {"closed": sum(1 for p in closed if p["variant_key"] == k), **summarize([{"net_r": p["net_r"], "exit_close_ns": _ns(p["exit_time"])} for p in closed if p["variant_key"] == k]),
                  "open": sum(1 for p in paper if p["variant_key"] == k and p["status"] == "OPEN"),
                  "statuses": {st: sum(1 for p in paper if p["variant_key"] == k and p["status"] == st) for st in sorted({p["status"] for p in paper})}}
              for k in sorted({p["variant_key"] for p in paper})}
    out = {"label": "Stored outcomes and re-simulations are hypothetical. Only the paper section comes from forward trading.",
           "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
           "signals": rows, "stored": {"n": len(stored), "wins": sum(x > 0 for x in stored), "total_r": round(sum(stored), 3)},
           "replay_net": {"n": len(replay), "wins": sum(x["net_r"] > 0 for x in replay), "total_r": round(sum(x["net_r"] for x in replay), 3),
                          "avg_mfe_r": round(sum(x["mfe_r"] for x in replay) / max(len(replay), 1), 3), "avg_mae_r": round(sum(x["mae_r"] for x in replay) / max(len(replay), 1), 3)},
           "paper_by_account": by_var, "bar_seconds": {r["timeframe"]: tf_seconds(r["timeframe"]) for r in rows}}
    out = jsonable(out)
    (cache / AUDIT_FILE).write_text(json.dumps(out), encoding="utf-8")
    return out


if __name__ == "__main__":
    a = run()
    print("stored", a["stored"], "replay", a["replay_net"])
    print("paper", {k: {x: v.get(x) for x in ("closed", "open", "avg_r", "total_r", "win_rate_pct")} for k, v in a["paper_by_account"].items()})
    for r in a["signals"]:
        print(r["signal_candle_close"][:16], r["symbol"], r["timeframe"], r["direction"], r["strategy"], "stop%", r["entry_to_stop_pct"], "stopATR", r["stop_atr"],
              "rr", r["net_rr_estimate"], r["stored_status"], r["stored_r"], "net", (r.get("replay") or {}).get("net_r"), "mfe", (r.get("replay") or {}).get("mfe_r"),
              "gate", r.get("gate_status"), "match", r["reconstruction_matches"], "paper", [(p["variant_key"], p["status"], p["net_r"]) for p in r["paper"]])
