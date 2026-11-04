"""Forward-test results and the strategy promotion gate.

Only CLOSED paper trades count as completed results. ``strategy`` statistics use every
filled trade of a variant (signal-level expectancy); ``portfolio`` statistics use the trades
its paper account actually took under the risk limits (account-level results).

Promotion readiness is a checklist (``GateThresholds``). ``READY_FOR_REVIEW`` means every
gating criterion passed; it is a request for a human decision, never an automatic change of
the production engine or of the decision-engine mode.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.forward.settings import GateThresholds
from app.research.data import DEFAULT_CACHE
from app.research.metrics import summarize
from app.research.stats import cluster_bootstrap, drawdown_distribution, period_consistency

TEST_STATUS_FILE = DEFAULT_CACHE / "test_status.json"


def as_trade(r: dict[str, Any]) -> dict[str, Any]:
    return {**r, "decision_ns": int(r["candle_close_time"].timestamp() * 1e9),
            "exit_close_ns": int(r["exit_time"].timestamp() * 1e9) if r.get("exit_time") else None}


def _estimated(r: dict[str, Any]) -> bool:
    f = r.get("cost_flags") or {}
    return (f.get("entry") != "observed_bid_ask" or str(f.get("exit", "")).startswith("candle")
            or f.get("funding") == "estimated_from_predicted_rate")


def cost_breakdown(closed: list[dict[str, Any]]) -> dict[str, Any]:
    if not closed:
        return {"n": 0}
    def avg_r(field):
        vals = [r[field] / r["risk_amount"] for r in closed if r.get("risk_amount")]
        return round(sum(vals) / len(vals), 4) if vals else None
    return {
        "n": len(closed),
        "avg_fees_r": round(sum((r["entry_fee"] + r["exit_fee"]) / r["risk_amount"] for r in closed if r.get("risk_amount"))
                            / len(closed), 4),
        "avg_spread_r": avg_r("spread_cost"), "avg_slippage_r": avg_r("slippage_cost"), "avg_funding_r": avg_r("funding_cost"),
        "total_fees": round(sum(r["entry_fee"] + r["exit_fee"] for r in closed), 2),
        "total_funding": round(sum(r["funding_cost"] for r in closed), 2),
        "estimated_share": round(sum(_estimated(r) for r in closed) / len(closed), 4),
        "flags": dict(Counter(f"{k}={v}" for r in closed for k, v in (r.get("cost_flags") or {}).items())),
    }


def equity_curve(closed_taken: list[dict[str, Any]], start: float) -> list[dict[str, Any]]:
    eq, out = start, []
    for r in sorted(closed_taken, key=lambda r: r["exit_time"]):
        eq += r["net_pnl"] or 0.0
        out.append({"time": r["exit_time"].isoformat(), "equity": round(eq, 2)})
    return out


def block(trades: list[dict[str, Any]], dd_threshold: float) -> dict[str, Any]:
    ts = [as_trade(r) for r in trades if r.get("net_r") is not None]
    return {"summary": summarize(ts), "bootstrap": cluster_bootstrap(ts),
            "drawdown": drawdown_distribution(ts, [dd_threshold / 2, dd_threshold, dd_threshold * 2]) if ts else None,
            "consistency": period_consistency(ts)}


def variant_report(rows: list[dict[str, Any]], variant: dict[str, Any], start_equity: float, now: datetime,
                   gate: GateThresholds) -> dict[str, Any]:
    closed = [r for r in rows if r["status"] == "CLOSED"]
    taken = [r for r in closed if r["portfolio_taken"]]
    elapsed = (now - variant["period_start"]).total_seconds() / 86400
    return {
        "variant": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in variant.items()},
        "elapsed_days": round(elapsed, 3),
        "counts": {"decisions": dict(Counter(r["decision"] for r in rows)), "status": dict(Counter(r["status"] for r in rows)),
                   "portfolio_taken": sum(1 for r in rows if r["portfolio_taken"]),
                   "portfolio_rejections": dict(Counter((r.get("data") or {}).get("risk_code") for r in rows
                                                        if r["status"] in ("OPEN", "CLOSED") and not r["portfolio_taken"]))},
        "strategy": block(closed, gate.drawdown_threshold_r),
        "portfolio": {**block(taken, gate.drawdown_threshold_r), "equity_curve": equity_curve(taken, start_equity)},
        "costs": cost_breakdown(closed),
    }


def test_status(path: Path = TEST_STATUS_FILE) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def assess(rep: dict[str, Any], rows: list[dict[str, Any]], gate: GateThresholds, now: datetime,
           tests: dict[str, Any] | None, breakers_tripped: bool) -> dict[str, Any]:
    s = rep["strategy"]
    summ, boot, dd, cons = s["summary"], s["bootstrap"], s["drawdown"] or {}, s["consistency"]
    checks: list[dict[str, Any]] = []

    def add(key: str, label: str, ok: bool, detail: str) -> None:
        checks.append({"key": key, "criterion": label, "passed": bool(ok), "detail": detail})

    add("period", "Forward observation period", rep["elapsed_days"] >= gate.min_forward_days,
        f"{rep['elapsed_days']:.1f} of {gate.min_forward_days:g} days")
    n = summ.get("n", 0)
    add("trades", "Completed forward trades", n >= gate.min_closed_trades, f"{n} of {gate.min_closed_trades}")
    nc = boot.get("n_clusters", 0)
    add("clusters", "Independent trade clusters", nc >= gate.min_independent_clusters,
        f"{nc} of {gate.min_independent_clusters} (same-direction trades within 60 min count once)")
    avg, p = summ.get("avg_r"), boot.get("prob_avg_positive")
    add("expectancy", "Net expectancy supported by forward evidence",
        avg is not None and p is not None and avg >= gate.min_avg_net_r and p >= gate.min_prob_positive,
        f"avg net R {avg if avg is not None else '—'} (min {gate.min_avg_net_r:g}), cluster-bootstrap "
        f"P(>0) {p if p is not None else '—'} (min {gate.min_prob_positive:g}), 95% CI {boot.get('avg_net_r_ci95')}")
    share, thirds = cons.get("best_month_share_of_positive_r"), cons.get("positive_thirds", 0)
    add("concentration", "Not driven by one concentrated period",
        share is not None and share <= gate.max_best_month_share and thirds >= gate.min_positive_thirds,
        f"best month = {share if share is not None else '—'} of positive R (max {gate.max_best_month_share:g}); "
        f"{thirds}/3 sub-periods positive (min {gate.min_positive_thirds})")
    hist_dd = dd.get("historical_max_dd_r")
    p_dd = (dd.get("prob_dd_beyond") or {}).get(f"{gate.drawdown_threshold_r:g}R")
    add("drawdown", "Drawdown within limits",
        hist_dd is not None and hist_dd <= gate.max_forward_drawdown_r and p_dd is not None
        and p_dd <= gate.max_prob_drawdown_beyond and not breakers_tripped,
        f"forward max DD {hist_dd}R (max {gate.max_forward_drawdown_r:g}R); P(DD > {gate.drawdown_threshold_r:g}R) "
        f"{p_dd} (max {gate.max_prob_drawdown_beyond:g}); circuit breaker {'TRIPPED' if breakers_tripped else 'ok'}")
    est = rep["costs"].get("estimated_share")
    add("costs", "Execution costs realistic (observed, not estimated)",
        est is not None and est <= gate.max_estimated_cost_share,
        f"{est if est is not None else '—'} of trades with estimated fills/exits/funding (max {gate.max_estimated_cost_share:g})")
    sig = [r for r in rows if r["decision"] in ("ACCEPTED", "MISSED")]
    missed = sum(1 for r in sig if r["decision"] == "MISSED")
    data_rej = sum(1 for r in rows if (r.get("data") or {}).get("risk_code") == "data_integrity")
    share_m = (missed + data_rej) / len(sig) if sig else None
    add("data", "Data integrity", share_m is not None and share_m <= gate.max_missed_share,
        f"{missed} missed + {data_rej} data-integrity rejections of {len(sig)} signals (max share {gate.max_missed_share:g})")
    t_ok, t_detail = False, "no recorded full test run"
    if tests:
        age = (now - datetime.fromisoformat(tests["finished_at"])).total_seconds() / 86400
        t_ok = tests.get("failed", 1) == 0 and tests.get("full_suite") and age <= gate.max_test_age_days
        t_detail = (f"{tests.get('passed')} passed, {tests.get('failed')} failed, full suite: {tests.get('full_suite')}, "
                    f"{age:.1f} days ago (max {gate.max_test_age_days:g})")
    add("tests", "All regression tests pass", t_ok, t_detail)
    passed = all(c["passed"] for c in checks)
    insufficient = not all(c["passed"] for c in checks if c["key"] in ("period", "trades", "clusters"))
    readiness = "READY_FOR_REVIEW" if passed else ("INSUFFICIENT_FORWARD_DATA" if insufficient else "NOT_READY")
    return {"readiness": readiness, "checks": checks, "manual_approval_required": True,
            "note": ("READY_FOR_REVIEW only requests a human review. Approval is recorded and never changes the "
                     "production strategy or enables enforcement automatically.")}
