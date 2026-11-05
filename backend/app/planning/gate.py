"""Entry-quality classification with objective conditions, checked in a fixed order (first match wins).

=====================  ===================================================================================
REJECTED               No valid stop or plan, a production rule other than room failed, or a position in
                       the same coin or too many correlated same-direction positions are already open.
CONFLICTING_TIMEFRAMES At least one higher timeframe's EMA trend points against the trade.
INSUFFICIENT_REWARD    Room to the nearest opposing zone is below ``min_reward_r`` of this stop, or the net
                       result at the primary target is below ``min_net_rr``.
OVEREXTENDED           Close at least ``overextended_atr`` beyond EMA-fast in the trade direction, or
                       ``exhaustion_run`` closes in a row with RSI at an extreme.
WAIT_FOR_RETEST        A breakout closed more than ``retest_break_atr`` beyond its level but isn't extreme:
                       the idea stands, the price doesn't.
QUALIFIED              None of the above.
=====================  ===================================================================================

Thresholds repeat existing production and entry-quality values, or were set before any result was seen,
and aren't tuned here. The classes are hypotheses until the study has tested them. QUALIFIED only means
these conditions hold; it isn't a probability, and this gate never creates a trade the engine didn't find.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.planning.targets import Costs

GATE_STATUSES = ["QUALIFIED", "WAIT_FOR_RETEST", "OVEREXTENDED", "INSUFFICIENT_REWARD", "CONFLICTING_TIMEFRAMES", "REJECTED"]
ROOM_CODE = "opposing_zone_too_close"


class GateParams(BaseModel):
    min_reward_r: float = Field(1.5, ge=0)
    min_net_rr: float = Field(1.2, ge=0)
    primary_r: float = Field(2.0, gt=0)
    overextended_atr: float = Field(1.5, ge=0)
    exhaustion_run: int = Field(5, ge=2)
    rsi_extreme_low: float = 30
    rsi_extreme_high: float = 70
    retest_break_atr: float = Field(0.5, ge=0)
    max_correlated: int = Field(1, ge=0)
    corr_threshold: float = Field(0.7, ge=0, le=1)


GATE_DEFINITIONS: dict[str, str] = {
    "QUALIFIED": "Every condition below holds. It doesn't estimate the chance of winning.",
    "WAIT_FOR_RETEST": "A breakout that closed more than 0.5 ATR past its level. Better price may come on a retest.",
    "OVEREXTENDED": "Price is 1.5 ATR or more past EMA-fast, or has 5 closes in a row with RSI at an extreme.",
    "INSUFFICIENT_REWARD": "Under 1.5R of room to the nearest opposing zone, or under 1.2R net at the 2R target.",
    "CONFLICTING_TIMEFRAMES": "A higher timeframe's trend points against the trade.",
    "REJECTED": "No valid plan, another production rule failed, or the portfolio already holds this risk.",
}


@dataclass(slots=True)
class GateInput:
    direction: str
    strategy: str
    entry: float
    atr: float
    stop: float | None
    production_status: str = "QUALIFIED"
    production_reason_codes: list[str] = field(default_factory=list)
    opposing_boundary: float | None = None
    htf_trends: dict[str, str] = field(default_factory=dict)
    ext_fast_atr: float | None = None
    run: int = 0
    rsi: float | None = None
    break_dist_atr: float | None = None
    same_symbol_open: bool = False
    correlated_open: int = 0
    stop_refused: str | None = None


@dataclass(slots=True)
class GateResult:
    status: str
    reasons: list[dict[str, str]]
    checks: list[dict[str, Any]]
    room_r: float | None
    net_r_at_primary: float | None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "definition": GATE_DEFINITIONS[self.status], "reasons": self.reasons,
                "checks": self.checks, "room_r": self.room_r, "net_r_at_primary": self.net_r_at_primary}


def classify_candidate(g: GateInput, p: GateParams | None = None, costs: Costs = Costs()) -> GateResult:
    p = p or GateParams()
    long = g.direction == "LONG"
    opposite = "BEARISH" if long else "BULLISH"
    risk = abs(g.entry - g.stop) if g.stop is not None else None
    room = None
    if risk and g.opposing_boundary is not None:
        room = abs(g.opposing_boundary - g.entry) / risk
    net = None
    if risk:
        primary = g.entry + p.primary_r * risk if long else g.entry - p.primary_r * risk
        net = costs.net_r(g.direction, g.entry, g.stop, primary)  # type: ignore[arg-type]
    against = sorted(tf for tf, t in g.htf_trends.items() if t == opposite)
    other_failures = [c for c in g.production_reason_codes if c != ROOM_CODE]
    ext = g.ext_fast_atr
    rsi_extreme = g.rsi is not None and (g.rsi <= p.rsi_extreme_low if not long else g.rsi >= p.rsi_extreme_high)
    exhausted = g.run >= p.exhaustion_run and rsi_extreme
    too_far = ext is not None and ext >= p.overextended_atr
    room_ok = room is None or room >= p.min_reward_r
    net_ok = net is not None and net >= p.min_net_rr
    wait = g.strategy == "breakout" and g.break_dist_atr is not None and g.break_dist_atr > p.retest_break_atr
    checks = [
        {"key": "valid_plan", "label": "Valid stop and plan", "passed": g.stop is not None and not other_failures,
         "detail": g.stop_refused or (", ".join(other_failures) if other_failures else "ok")},
        {"key": "portfolio", "label": "No overlapping open risk",
         "passed": not g.same_symbol_open and not (p.max_correlated and g.correlated_open >= p.max_correlated),
         "detail": "same coin already open" if g.same_symbol_open else f"{g.correlated_open} correlated same-direction open"},
        {"key": "timeframes", "label": "No higher timeframe against the trade", "passed": not against,
         "detail": f"against: {', '.join(against)}" if against else (", ".join(f"{k} {v.lower()}" for k, v in g.htf_trends.items()) or "no higher timeframe")},
        {"key": "room", "label": f"Room of at least {p.min_reward_r:g}R to the opposing zone", "passed": room_ok,
         "detail": f"{room:.2f}R" if room is not None else "no opposing zone found"},
        {"key": "net_reward", "label": f"At least {p.min_net_rr:g}R net at the {p.primary_r:g}R target", "passed": net_ok,
         "detail": f"{net:.2f}R" if net is not None else "no stop"},
        {"key": "extension", "label": f"Under {p.overextended_atr:g} ATR past EMA-fast, not exhausted",
         "passed": not too_far and not exhausted, "detail": (f"{ext:.2f} ATR" if ext is not None else "n/a") + (f", {g.run} closes in a row" if exhausted else "")},
        {"key": "retest", "label": f"Breakout within {p.retest_break_atr:g} ATR of its level", "passed": not wait,
         "detail": f"{g.break_dist_atr:.2f} ATR" if g.break_dist_atr is not None else "not a breakout"},
    ]
    reasons: list[dict[str, str]] = []

    def done(status: str) -> GateResult:
        return GateResult(status, reasons, checks, room, net)

    if g.stop is None:
        reasons.append({"code": "no_valid_stop", "message": g.stop_refused or "This stop method can't place a valid stop."})
    if other_failures:
        reasons.append({"code": "production_rule_failed", "message": f"Production rules failed: {', '.join(other_failures)}."})
    if g.same_symbol_open:
        reasons.append({"code": "symbol_already_open", "message": "A position in this coin is already open, so this isn't a new independent trade."})
    elif p.max_correlated and g.correlated_open >= p.max_correlated:
        reasons.append({"code": "correlated_exposure", "message": f"{g.correlated_open} correlated same-direction position(s) already open; the limit is {p.max_correlated}."})
    if reasons:
        return done("REJECTED")
    if against:
        reasons.append({"code": "htf_against", "message": f"The {', '.join(against)} trend points against this {g.direction}."})
        return done("CONFLICTING_TIMEFRAMES")
    if not room_ok:
        reasons.append({"code": "room_too_small", "message": f"Only {room:.2f}R of room before the nearest opposing zone with this stop (minimum {p.min_reward_r:g}R)."})
    if not net_ok:
        reasons.append({"code": "net_reward_too_small", "message": f"The {p.primary_r:g}R target pays only {net:.2f}R after costs (minimum {p.min_net_rr:g}R)."})
    if reasons:
        return done("INSUFFICIENT_REWARD")
    if too_far or exhausted:
        reasons.append({"code": "overextended", "message": f"Price is {ext:.2f} ATR past EMA-fast." if too_far else f"{g.run} closes in a row with RSI {g.rsi:.1f}."})
        return done("OVEREXTENDED")
    if wait:
        reasons.append({"code": "wait_for_retest", "message": f"The close is {g.break_dist_atr:.2f} ATR past the broken level. A retest may offer a better entry."})
        return done("WAIT_FOR_RETEST")
    return done("QUALIFIED")


def correlated_count(symbol: str, direction: str, open_positions: list[dict[str, Any]], corr_fn, threshold: float) -> tuple[bool, int, list[dict[str, Any]]]:
    """(same coin open, correlated same-direction count, detail). Unknown correlation doesn't count as correlated."""
    same = any(p["symbol"] == symbol for p in open_positions)
    detail = []
    n = 0
    for p in open_positions:
        if p["symbol"] == symbol or p["direction"] != direction:
            continue
        c = corr_fn(p["symbol"], symbol) if corr_fn else None
        hit = c is not None and c == c and c >= threshold
        n += bool(hit)
        detail.append({"symbol": p["symbol"], "direction": p["direction"], "correlation": c if c == c else None, "counts": bool(hit)})
    return same, n, detail
