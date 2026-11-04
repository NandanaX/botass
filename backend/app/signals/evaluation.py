"""Outcome evaluation methodology for live signals (``methodology v1``).

Signals are research outputs, not executed positions. To report how they *would* have
played out, each signal is replayed against later closed candles with the same execution
model as the backtester (``app.backtest.execution``):

1. Hypothetical entry at the open of the candle after the signal candle.
2. If that open is already beyond the stop or the target, the signal is INVALIDATED.
3. Exit when the stop or target is touched (stop assumed first if both touch within one
   candle: AMBIGUOUS_STOP). After ``max_bars`` candles without either: EXPIRED.
4. The R multiple is (exit - entry) / (entry - stop) for LONG (mirrored for SHORT),
   before fees and slippage (``r_multiple``, gross).
5. ``net_r_multiple`` (methodology v2) additionally charges taker fees on entry and exit,
   adverse slippage on the entry and on stop/time exits (a stop is not assumed to fill
   exactly at its price), measured against the same planned risk. Funding is not included.

Until all of this is decided, the signal stays PENDING (shown as "active").

Phase 2 signals carry TP1-TP3. The status above is decided on the *primary* target (the
``take_profit`` column). The ``targets`` section additionally reports which targets were
reached before the stop, using the same conservative rules (``simulate_partial_exits``);
with partial exits enabled it also reports the allocation-weighted gross R.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.backtest.execution import simulate_exit, simulate_partial_exits

METHODOLOGY = ("v2: next-candle-open entry, OHLC touch, stop-first on ambiguity, gross R and net R "
               "(fees + entry/stop slippage)")
STOP_SLIPPAGE_PCT = 0.05

STATUS_FOR_REASON = {
    "TARGET": "TARGET_HIT",
    "TARGET_GAP": "TARGET_HIT",
    "STOP": "STOP_HIT",
    "STOP_GAP": "STOP_HIT",
    "AMBIGUOUS_STOP": "AMBIGUOUS_STOP",
    "TIME_EXIT": "EXPIRED",
}


def _targets_section(direction: str, entry: float, stop: float, plan: dict[str, Any], o, h, lo, c,
                     max_bars: int) -> dict[str, Any] | None:
    targets = plan.get("targets") or []
    if not targets:
        return None
    partial = bool(plan.get("partial_exits"))
    tgt = [(t["price"], (t["allocation_pct"] / 100) if partial else 1 / len(targets)) for t in targets]
    res = simulate_partial_exits(direction, o, h, lo, c, 0, entry, stop, tgt, max_bars, False,
                                 final_if_unresolved="OPEN")
    risk = abs(entry - stop)
    hits = [f.reason.split("_")[0] for f in res.fills if f.reason.startswith("TP")]
    out: dict[str, Any] = {"targets_hit": hits, "final": res.final_reason, "partial_exits": partial}
    if partial and res.final_reason != "OPEN":
        long = direction == "LONG"
        out["weighted_r"] = round(sum(f.fraction * ((f.price - entry) if long else (entry - f.price)) / risk
                                      for f in res.fills), 4)
    return out


def net_r(direction: str, entry: float, exit_price: float, stop: float, market_exit: bool, fee_pct: float,
          slippage_pct: float, stop_slippage_pct: float = STOP_SLIPPAGE_PCT) -> float:
    """R after fees and slippage, relative to the planned risk ``|entry - stop|``."""
    long = direction == "LONG"
    fill = entry * (1 + slippage_pct / 100) if long else entry * (1 - slippage_pct / 100)
    out = exit_price
    if market_exit:
        out = exit_price * (1 - stop_slippage_pct / 100) if long else exit_price * (1 + stop_slippage_pct / 100)
    pnl = (out - fill) if long else (fill - out)
    fees = fee_pct / 100 * (fill + out)
    return (pnl - fees) / abs(entry - stop)


def evaluate_signal_outcome(
    direction: str, candle_open_time: pd.Timestamp, stop: float, target: float, candles: pd.DataFrame, max_bars: int,
    plan: dict[str, Any] | None = None, fee_pct: float = 0.055, slippage_pct: float = 0.02,
) -> dict[str, Any] | None:
    """Return an evaluation dict, or None while the outcome is still undecided."""
    after = candles[candles["open_time"] > candle_open_time].reset_index(drop=True)
    if after.empty:
        return None
    o = after["open"].to_numpy(float)
    h = after["high"].to_numpy(float)
    lo = after["low"].to_numpy(float)
    c = after["close"].to_numpy(float)
    entry = float(o[0])
    long = direction == "LONG"
    if (long and (entry <= stop or entry >= target)) or (not long and (entry >= stop or entry <= target)):
        return {"status": "INVALIDATED", "methodology": METHODOLOGY, "entry_price": entry,
                "entry_time": after["open_time"].iloc[0].isoformat(),
                "note": "next candle opened beyond the stop or target; no hypothetical entry"}
    res = simulate_exit(direction, o, h, lo, c, 0, stop, target, max_bars, check_gap_on_first_bar=False,
                        final_if_unresolved="OPEN")
    if res.reason == "OPEN":
        return None
    risk = abs(entry - stop)
    r = ((res.exit_price - entry) if long else (entry - res.exit_price)) / risk
    targets = _targets_section(direction, entry, stop, plan, o, h, lo, c, max_bars) if plan else None
    nr = net_r(direction, entry, res.exit_price, stop, res.reason not in ("TARGET", "TARGET_GAP"), fee_pct,
               slippage_pct)
    return {
        "targets": targets,
        "status": STATUS_FOR_REASON[res.reason],
        "methodology": METHODOLOGY,
        "entry_price": entry,
        "entry_time": after["open_time"].iloc[0].isoformat(),
        "exit_price": res.exit_price,
        "exit_time": after["open_time"].iloc[res.exit_index].isoformat(),
        "exit_reason": res.reason,
        "bars_held": res.bars_held,
        "r_multiple": round(r, 4),
        "net_r_multiple": round(nr, 4),
        "cost_r": round(r - nr, 4),
        "note": "Hypothetical outcome from OHLC data; not a realized trade.",
    }
