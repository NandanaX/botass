"""One hypothetical trade with one or more exit legs, under a cost and execution scenario.

Same conventions as ``robustness.simulate``, which it matches exactly for a single exit (see the
regression test): next-open entry, OHLC touches, stop first when a bar touches both, a bar opening
beyond a level fills at that open, and only bars that exist are read. Added here:

* several take-profit legs, each closing a fraction of the position; the stop closes whatever is left;
* an optional resting limit entry for retest research, filling when the limit is touched;
* maximum favourable and adverse excursion (MFE, MAE) in R.

The stop isn't moved after entry. R is the planned risk ``|raw entry - stop|``, so a wider stop means
a proportionally smaller position, not more risk.
"""

from __future__ import annotations

from typing import Any

from app.research.robustness import Scenario, Series

RETEST_WAIT_BARS = 12
RETEST_MIN_RISK_ATR = 0.5


def retest_plan(direction: str, near_edge: float | None, stop: float | None, atr: float) -> tuple[float, float, float] | None:
    """``(limit, stop, 2R target)`` for a breakout's retest entry at the broken zone's near edge, or None
    when the zone doesn't leave a usable level. A stop closer than the minimum is moved out, never in."""
    long = direction == "LONG"
    if near_edge is None or stop is None or (long and near_edge <= stop) or (not long and near_edge >= stop):
        return None
    if abs(near_edge - stop) < RETEST_MIN_RISK_ATR * atr:
        stop = near_edge - RETEST_MIN_RISK_ATR * atr if long else near_edge + RETEST_MIN_RISK_ATR * atr
    risk = abs(near_edge - stop)
    return near_edge, stop, near_edge + 2 * risk if long else near_edge - 2 * risk


def simulate_legs(s: Series, i: int, direction: str, stop: float, legs: list[tuple[float, float]], max_bars: int,
                  sc: Scenario, atr_pct: float, limit_entry: tuple[int, float] | None = None) -> dict[str, Any] | None:
    """``legs``: ``(price, fraction)`` nearest target first, fractions summing to 1. ``limit_entry``:
    ``(bar index, limit price)`` replaces the next-open entry. Returns None if no trade can be scored."""
    long = direction == "LONG"
    sign = 1.0 if long else -1.0
    n = len(s.c)
    extra = sc.vol_slip_frac * atr_pct
    if limit_entry is None:
        e = i + 1 + sc.entry_delay
        if e >= n:
            return None
        raw = float(s.o[e])
        entry_slip = (sc.entry_slip_pct * sc.slip_mult + sc.spread_pct / 2 + extra) / 100
    else:
        e, raw = limit_entry
        entry_slip = 0.0  # a resting limit fills at its price
    nearest = legs[0][0]
    if (long and not stop < raw < nearest) or (not long and not nearest < raw < stop):
        return None
    fill = raw * (1 + sign * entry_slip)
    risk = abs(raw - stop)
    last = min(n - 1, e + max_bars - 1)
    exit_slip = (sc.stop_slip_pct * sc.slip_mult + sc.spread_pct / 2 + extra) / 100
    remaining = list(legs)
    closed: list[tuple[float, float, bool, int]] = []  # price, fraction, market order, exit bar
    final_reason: str | None = None
    hi, lo = -float("inf"), float("inf")
    k_end = last
    for k in range(e, last + 1):
        hi, lo = max(hi, float(s.h[k])), min(lo, float(s.lo[k]))
        if k > e:
            op = float(s.o[k])
            if (long and op <= stop) or (not long and op >= stop):
                closed += [(op, f, True, k) for _, f in remaining]
                remaining, final_reason, k_end = [], "STOP_GAP", k
                break
            while remaining and ((long and op >= remaining[0][0]) or (not long and op <= remaining[0][0])):
                closed.append((op, remaining.pop(0)[1], False, k))
                final_reason = "TARGET_GAP"
            if not remaining:
                k_end = k
                break
        touched_stop = s.lo[k] <= stop if long else s.h[k] >= stop
        if touched_stop:
            ambiguous = bool(remaining) and (s.h[k] >= remaining[0][0] if long else s.lo[k] <= remaining[0][0])
            final_reason = "AMBIGUOUS_STOP" if ambiguous else "STOP"
            px, k_end = float(stop), k
            if sc.stop_delay and k + 1 < n:
                px, k_end = float(s.o[k + 1]), k + 1  # executed at the next bar's open
                px = min(px, stop) if long else max(px, stop)
            closed += [(px, f, True, k_end) for _, f in remaining]
            remaining = []
            break
        while remaining and ((long and s.h[k] >= remaining[0][0]) or (not long and s.lo[k] <= remaining[0][0])):
            px, f = remaining.pop(0)
            closed.append((float(px), f, False, k))
            final_reason = "TARGET"
        if not remaining:
            k_end = k
            break
    if remaining:
        if last < e + max_bars - 1:
            return None  # still open at the end of the data
        px = float(s.c[last])
        closed += [(px, f, True, last) for _, f in remaining]
        final_reason, k_end = "TIME_EXIT", last
    gross = pnl = fees = fund = 0.0
    fee_rate = sc.fee_pct * sc.fee_mult / 100
    for px, f, market, k in closed:
        exit_fill = px * (1 - sign * exit_slip) if market else px
        gross += f * sign * (px - raw)
        pnl += f * sign * (exit_fill - fill)
        fees += f * fee_rate * (fill + exit_fill)
        if sc.funding and len(s.fund_t):
            m = (s.fund_t > s.t[e]) & (s.fund_t <= int(s.t[k]) + s.bar_ns)
            fund += f * fill * float(s.fund_r[m].sum()) * sign
    net = (pnl - fees - fund) / risk
    hit_targets = sum(1 for _, _, market, _ in closed if not market)
    return {"net_r": net, "gross_r": gross / risk, "reason": final_reason, "bars_held": k_end - e + 1,
            "exit_close_ns": int(s.t[k_end]) + s.bar_ns, "fee_r": fees / risk, "funding_r": fund / risk,
            "stopped": final_reason in ("STOP", "STOP_GAP", "AMBIGUOUS_STOP"), "targets_hit": hit_targets,
            "mfe_r": (hi - fill if long else fill - lo) / risk, "mae_r": (fill - lo if long else hi - fill) / risk,
            "entry_index": e, "exit_index": k_end, "raw_entry": raw, "entry_fill": fill}


def find_retest_fill(s: Series, i: int, direction: str, level: float, stop: float, target: float, wait_bars: int,
                     through: float = 0.0) -> tuple[int, float] | None:
    """First bar after the signal touching ``level``. The order is cancelled if the stop or the target
    trades first, or after ``wait_bars``. A bar that reaches both the target and the limit counts as
    missed, since the order inside a bar is unknown and a missed trade is the cautious reading.
    ``through`` requires price to trade that far past the level, because a touch doesn't guarantee a fill."""
    long = direction == "LONG"
    for k in range(i + 1, min(len(s.c), i + 1 + wait_bars)):
        if (long and s.lo[k] <= stop) or (not long and s.h[k] >= stop):
            return None
        if (long and s.h[k] >= target) or (not long and s.lo[k] <= target):
            return None
        if (long and s.lo[k] <= level - through) or (not long and s.h[k] >= level + through):
            return k, float(level)
    return None
