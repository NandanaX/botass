"""Cost-aware, conservative simulation of one hypothetical trade.

Rules (same conventions as ``app.backtest.execution``, plus explicit costs):

* Decision at the close of signal bar ``i``; hypothetical fill at the **open of bar i+1**
  plus adverse entry slippage. If that open is already beyond the stop or the target the
  trade is ``INVALIDATED`` (no entry).
* Stop/target touch on OHLC; when both are touched inside one bar the stop is assumed
  first (``AMBIGUOUS_STOP``) unless lower-timeframe candles resolve the order.
* A bar that opens beyond the stop fills at that open (gap). Stop and time exits pay
  ``stop_slip_pct`` adverse slippage: a stop is **not** assumed to fill exactly at its
  price. Target exits are resting limit orders without slippage.
* Fees: ``fee_pct`` per side on entry and exit notional. Funding: every funding event in
  ``(entry_time, exit candle close]`` is charged on the position notional (LONG pays
  positive rates, SHORT receives them).
* ``R`` is measured against the planned risk ``|raw entry - stop|`` (what position sizing
  uses). ``gross_r`` excludes costs; ``net_r`` includes fees, slippage and funding.

MFE/MAE (maximum favourable/adverse excursion, in R) are measured from the fill over the
bars the trade was open. ``post_exit_target`` reports whether price reached the target
within ``max_bars`` anyway after a stop: a sign of stops inside normal noise.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.backtest.execution import apply_slippage, simulate_exit


@dataclass(frozen=True, slots=True)
class CostModel:
    fee_pct: float = 0.055  # Bybit taker fee per side
    entry_slip_pct: float = 0.02
    stop_slip_pct: float = 0.05
    include_funding: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ZERO_COSTS = CostModel(0.0, 0.0, 0.0, False)


@dataclass(slots=True)
class TradeOutcome:
    status: str  # FILLED | INVALIDATED | OPEN
    entry_index: int
    entry_time_ns: int
    raw_entry: float
    entry_fill: float
    exit_index: int = -1
    exit_price: float = math.nan  # fill incl. slippage
    reason: str = ""
    bars_held: int = 0
    gross_r: float = math.nan
    net_r: float = math.nan
    cost_r: float = math.nan
    funding_r: float = 0.0
    mfe_r: float = math.nan
    mae_r: float = math.nan
    post_exit_target: bool | None = None
    resolved_by: str = "ohlc"

    @property
    def win(self) -> bool:
        return self.status == "FILLED" and self.net_r > 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, float) and not math.isfinite(v):
                d[k] = None
        return d


def _funding_per_unit(direction: str, funding: pd.DataFrame | None, entry_ns: int, exit_close_ns: int,
                      price: float) -> float:
    if funding is None or not len(funding):
        return 0.0
    t = funding["t_ns"].to_numpy()
    m = (t > entry_ns) & (t <= exit_close_ns)
    rate = float(funding["rate"].to_numpy()[m].sum())
    return price * rate * (1 if direction == "LONG" else -1)


def prepare_funding(funding: pd.DataFrame | None) -> pd.DataFrame | None:
    if funding is None or not len(funding):
        return None
    out = funding.copy()
    out["t_ns"] = pd.to_datetime(out["funding_time"], utc=True).astype("int64")
    return out


def simulate_trade(
    direction: str,
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, open_ns: np.ndarray, bar_ns: int,
    signal_index: int, stop: float, target: float, max_bars: int, costs: CostModel = CostModel(),
    funding: pd.DataFrame | None = None, allow_open: bool = False,
) -> TradeOutcome:
    n = len(close)
    e = signal_index + 1
    long = direction == "LONG"
    if e >= n:
        return TradeOutcome("OPEN", e, 0, math.nan, math.nan)
    raw = float(open_[e])
    t0 = int(open_ns[e])
    if (long and not stop < raw < target) or (not long and not target < raw < stop):
        return TradeOutcome("INVALIDATED", e, t0, raw, raw, reason="entry candle opened beyond stop or target")
    fill = apply_slippage(raw, direction, "entry", costs.entry_slip_pct / 100)
    res = simulate_exit(direction, open_, high, low, close, e, stop, target, max_bars,
                        check_gap_on_first_bar=False, final_if_unresolved="OPEN")
    risk = abs(raw - stop)
    sign = 1.0 if long else -1.0
    last = res.exit_index
    hi = float(np.max(high[e:last + 1]))
    lo = float(np.min(low[e:last + 1]))
    mfe = ((hi - fill) if long else (fill - lo)) / risk
    mae = ((fill - lo) if long else (hi - fill)) / risk
    if res.reason == "OPEN":
        if not allow_open:
            return TradeOutcome("OPEN", e, t0, raw, fill, mfe_r=mfe, mae_r=mae)
    market_exit = res.reason not in ("TARGET", "TARGET_GAP")
    exit_fill = apply_slippage(res.exit_price, direction, "exit", costs.stop_slip_pct / 100) if market_exit \
        else res.exit_price
    gross = sign * (res.exit_price - raw) / risk
    pnl = sign * (exit_fill - fill)
    fees = costs.fee_pct / 100 * (fill + exit_fill)
    fund = _funding_per_unit(direction, funding, t0, int(open_ns[last]) + bar_ns, fill) if costs.include_funding else 0.0
    net = (pnl - fees - fund) / risk
    post = None
    if res.reason in ("STOP", "STOP_GAP", "AMBIGUOUS_STOP"):
        horizon = min(n - 1, e + max_bars - 1)
        if last + 1 <= horizon:
            post = bool(np.max(high[last + 1:horizon + 1]) >= target) if long else \
                bool(np.min(low[last + 1:horizon + 1]) <= target)
        else:
            post = False
    return TradeOutcome("FILLED", e, t0, raw, fill, last, exit_fill, res.reason, res.bars_held, gross, net,
                        gross - net, fund / risk, mfe, mae, post)


def resolve_ambiguous_with_lower_tf(
    direction: str, stop: float, target: float, bar_open_ns: int, bar_ns: int, lower: pd.DataFrame,
) -> str | None:
    """For a bar where both stop and target were touched, walk lower-timeframe candles
    inside it to find which level was reached first. Returns ``STOP``, ``TARGET`` or
    ``None`` (still ambiguous or no data)."""
    if lower is None or not len(lower):
        return None
    t = lower["open_time"].astype("int64").to_numpy()
    m = (t >= bar_open_ns) & (t < bar_open_ns + bar_ns)
    sub = lower.loc[m]
    if not len(sub):
        return None
    long = direction == "LONG"
    for h, lo in zip(sub["high"].to_numpy(float), sub["low"].to_numpy(float), strict=True):
        hs = lo <= stop if long else h >= stop
        ht = h >= target if long else lo <= target
        if hs and ht:
            return None
        if hs:
            return "STOP"
        if ht:
            return "TARGET"
    return None


def path_with_lower_tf(
    direction: str, entry_ns: int, stop: float, target: float, max_minutes: int, lower: pd.DataFrame,
) -> dict[str, Any] | None:
    """Replay the trade on lower-timeframe candles (e.g. 1m) from the entry time."""
    if lower is None or not len(lower):
        return None
    t = lower["open_time"].astype("int64").to_numpy()
    k0 = int(np.searchsorted(t, entry_ns, side="left"))
    if k0 >= len(t) or t[k0] != entry_ns:
        return None
    o, h, lo, c = (lower[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    step = int(t[1] - t[0]) if len(t) > 1 else 60_000_000_000
    res = simulate_exit(direction, o, h, lo, c, k0, stop, target, max_minutes, False, "OPEN")
    return {"reason": res.reason, "exit_time": pd.Timestamp(int(t[res.exit_index]), tz="UTC").isoformat(),
            "exit_price": res.exit_price, "bars_held": res.bars_held,
            "covered_until": pd.Timestamp(int(t[-1]) + step, tz="UTC").isoformat()}
