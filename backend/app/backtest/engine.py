"""Historical simulation using the *same* ``evaluate_at`` as the live scanner.

Execution model (declared in ``assumptions`` of every result):

* Decisions are made at the close of each candle using only data up to that close
  (features are causal; higher timeframes use candles closed by then).
* Default entry: next candle open (``entry_mode="next_open"``), with adverse slippage.
  ``signal_close`` is an optimistic alternative for comparison.
* Stop/target levels are those of the signal (computed from the signal close and ATR).
* If the entry candle opens beyond the stop or target, the trade is skipped.
* Exits follow ``app.backtest.execution`` (stop-first on ambiguous candles, gap fills).
* Fees are charged on entry and exit notional. One position at a time per backtest.
* Position size risks ``risk_per_trade_pct`` of current equity, including estimated fees.
* Funding: when funding history is available, each funding event between entry and the
  close of the exit candle is charged (LONG pays positive rates, SHORT receives them).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator

from app.backtest.execution import MARKET_EXITS_ALL, apply_slippage, simulate_exit, simulate_partial_exits
from app.backtest.metrics import compute_metrics
from app.risk.sizing import InstrumentRules, SizingConfig, size_position
from app.strategy.engine import evaluate_at
from app.strategy.features import Features, compute_features
from app.strategy.params import StrategyParams
from app.timeutil import TIMEFRAMES, tf_delta


class BacktestConfig(BaseModel):
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,20}USDT$")
    timeframe: str
    start: datetime
    end: datetime
    strategy: StrategyParams = Field(default_factory=StrategyParams)
    initial_equity: float = Field(10_000, gt=0)
    risk_per_trade_pct: float = Field(1.0, gt=0, le=10)
    fee_rate_pct: float = Field(0.055, ge=0, le=5)
    slippage_pct: float = Field(0.02, ge=0, le=5)
    leverage: float = Field(5, ge=1, le=100)
    entry_mode: Literal["next_open", "signal_close"] = "next_open"
    max_holding_bars: int = Field(200, ge=0, le=100_000)  # 0 = no time limit
    include_funding: bool = True
    min_score: int = Field(0, ge=0, le=100)
    name: str | None = Field(None, max_length=128)

    @model_validator(mode="after")
    def _check(self) -> BacktestConfig:
        if self.timeframe not in TIMEFRAMES:
            raise ValueError(f"unsupported timeframe {self.timeframe}")
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("start and end must include a timezone (use UTC)")
        if self.end <= self.start:
            raise ValueError("end must be after start")
        bars = (self.end - self.start) / tf_delta(self.timeframe)
        if bars > 200_000:
            raise ValueError("date range too large for this timeframe (max 200k candles)")
        return self


@dataclass(slots=True)
class BacktestResult:
    metrics: dict[str, Any]
    trades: list[dict[str, Any]]
    equity_curve: list[dict[str, Any]]
    assumptions: list[str]
    skipped: list[dict[str, Any]] = field(default_factory=list)


def assumptions_for(cfg: BacktestConfig, funding_used: bool, rules_used: bool = False) -> list[str]:
    return [
        "Historical simulation only; results are not live trading results and do not predict future returns.",
        "Signals are confirmed at candle close using only candles closed at that moment (no look-ahead).",
        ("Entries fill at the next candle open." if cfg.entry_mode == "next_open"
         else "Entries fill at the signal candle close (optimistic; for comparison only)."),
        f"Adverse slippage of {cfg.slippage_pct}% on entries and on stop/time/end-of-data exits; "
        "take-profit exits are treated as limit orders without slippage.",
        f"Fees of {cfg.fee_rate_pct}% per side on entry and exit notional.",
        "When stop-loss and take-profit are both touched within one candle, the stop-loss is assumed to be hit first.",
        "Candles that open beyond the stop (or target) exit at that open price.",
        "Trades whose entry candle opens beyond the stop or target are skipped.",
        "Only QUALIFIED setups are traded; WATCHLIST and REJECTED setups (with reasons) are not.",
        f"Position size risks {cfg.risk_per_trade_pct}% of current equity including estimated fees and slippage, "
        "rounded down to the instrument quantity step"
        + (" (current Bybit instrument rules; historical rule changes are not modelled)." if rules_used
           else " (no instrument rules available; quantities are not rounded)."),
        f"Margin and liquidation are estimated at {cfg.leverage:g}x isolated leverage; setups whose estimated "
        "liquidation price lies before the stop are rejected. Margin calls and liquidation are not simulated.",
        ("Partial exits at " + ", ".join(f"TP{k + 1} {a:g}%" for k, a in enumerate(cfg.strategy.targets.allocation_pct))
         + (" with the stop moved to breakeven after TP1." if cfg.strategy.targets.move_stop_to_breakeven else ".")
         if cfg.strategy.targets.partial_exits
         else f"Single exit at TP{cfg.strategy.targets.primary_index + 1} "
              f"({cfg.strategy.targets.r_multiples[cfg.strategy.targets.primary_index]:g}R)."),
        "With partial exits, a candle touching the stop and any pending target closes the whole remainder at the stop.",
        "One open position at a time; new signals are ignored while a position is open.",
        (f"Positions are closed at the candle close after {cfg.max_holding_bars} candles."
         if cfg.max_holding_bars else "No time-based exit."),
        ("Funding payments included from Bybit funding history (events up to the exit candle's close)."
         if funding_used else "Funding payments NOT included (disabled or history unavailable)."),
        "Max drawdown is measured on closed-trade equity (intra-trade drawdown is not included).",
    ]


def _funding_cost(direction: str, qty: float, funding: pd.DataFrame | None, entry_ns: int, exit_close_ns: int,
                  f: Features) -> float:
    if funding is None or funding.empty:
        return 0.0
    times = funding["t_ns"].to_numpy()
    mask = (times > entry_ns) & (times <= exit_close_ns)
    cost = 0.0
    for t, rate in zip(times[mask], funding["rate"].to_numpy()[mask], strict=True):
        idx = max(0, int(np.searchsorted(f.open_time, t, side="right")) - 1)
        notional = qty * f.open[idx]
        cost += notional * rate * (1 if direction == "LONG" else -1)
    return cost


def run_backtest(
    cfg: BacktestConfig,
    candles: pd.DataFrame,
    htf_candles: dict[str, pd.DataFrame] | None = None,
    funding: list[dict[str, Any]] | None = None,
    rules: InstrumentRules | None = None,
) -> BacktestResult:
    """``candles`` must be closed candles (ascending) and should include warm-up history
    before ``cfg.start``; decisions are only taken for candles opening at/after ``start``."""
    params = cfg.strategy
    f = compute_features(candles, cfg.timeframe, params)
    htf_features = {tf: compute_features(df, tf, params) for tf, df in (htf_candles or {}).items() if not df.empty}
    funding_df = None
    if cfg.include_funding and funding:
        funding_df = pd.DataFrame(funding)
        funding_df["t_ns"] = pd.to_datetime(funding_df["funding_time"], utc=True).astype("int64")
    n = len(f)
    start_ns = pd.Timestamp(cfg.start).value
    end_ns = pd.Timestamp(cfg.end).value
    first = int(np.searchsorted(f.open_time, start_ns, side="left"))
    last_decision = int(np.searchsorted(f.open_time, end_ns, side="right")) - 1
    fee = cfg.fee_rate_pct / 100
    slip = cfg.slippage_pct / 100
    equity = cfg.initial_equity
    trades: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    curve = [{"time": pd.Timestamp(f.open_time[first] if first < n else start_ns, tz="UTC").isoformat(),
              "equity": equity}]
    bar_ns = int(tf_delta(cfg.timeframe).total_seconds() * 1e9)

    tparams = params.targets
    rejected_setups: Counter[str] = Counter()
    i = first
    while i <= min(last_decision, n - 1):
        sizing_cfg = SizingConfig(account_balance=max(equity, 1e-9), risk_pct=cfg.risk_per_trade_pct,
                                  fee_rate_pct=cfg.fee_rate_pct, slippage_pct=cfg.slippage_pct, leverage=cfg.leverage)
        ev = evaluate_at(f, i, params, htf_features, sizing=sizing_cfg, rules=rules, context=False)
        for st in ev.setups:
            if st.status == "REJECTED":
                for r in st.reasons:
                    rejected_setups[r["code"]] += 1
        setup = ev.setup
        if setup is None or setup.score < cfg.min_score:
            i += 1
            continue
        assert setup.stop_loss is not None and setup.take_profit is not None
        direction = setup.direction
        if cfg.entry_mode == "next_open":
            if i + 1 >= n:
                break
            entry_idx, raw_entry, exit_start, gap_first = i + 1, float(f.open[i + 1]), i + 1, False
        else:
            entry_idx, raw_entry, exit_start, gap_first = i, float(f.close[i]), i + 1, True
        sl, tp = setup.stop_loss, setup.take_profit
        plan_targets = setup.plan.get("targets") or []
        first_tp = plan_targets[0]["price"] if plan_targets else tp
        long = direction == "LONG"
        guard_tp = first_tp if tparams.partial_exits else tp
        if (long and not sl < raw_entry < guard_tp) or (not long and not guard_tp < raw_entry < sl):
            skipped.append({"signal_time": ev.open_time, "direction": direction, "strategy": setup.strategy,
                            "reason": "entry candle opened beyond stop or target"})
            i += 1
            continue
        if exit_start >= n:
            break
        # Size from the actual (next-open) entry with the same sizing module as the live plan.
        sized = size_position(direction, raw_entry, sl, sizing_cfg, rules)
        if not sized.feasible:
            skipped.append({"signal_time": ev.open_time, "direction": direction, "strategy": setup.strategy,
                            "reason": "; ".join(x["message"] for x in sized.issues) or "position not feasible"})
            i += 1
            continue
        entry = apply_slippage(raw_entry, direction, "entry", slip)
        risk_amount = equity * cfg.risk_per_trade_pct / 100
        qty = sized.quantity
        if tparams.partial_exits:
            tgt = [(t["price"], t["allocation_pct"] / 100) for t in plan_targets]
            pres = simulate_partial_exits(direction, f.open, f.high, f.low, f.close, exit_start, entry, sl, tgt,
                                          cfg.max_holding_bars or None, check_gap_on_first_bar=gap_first,
                                          move_stop_to_breakeven=tparams.move_stop_to_breakeven)
            fills = [(fl.index, fl.price, fl.fraction, fl.reason) for fl in pres.fills]
            exit_index, bars_held, exit_reason = pres.exit_index, pres.bars_held, pres.final_reason
        else:
            res = simulate_exit(direction, f.open, f.high, f.low, f.close, exit_start, sl, tp,
                                cfg.max_holding_bars or None, check_gap_on_first_bar=gap_first)
            fills = [(res.exit_index, res.exit_price, 1.0, res.reason)]
            exit_index, bars_held, exit_reason = res.exit_index, res.bars_held, res.reason
        entry_ns = int(f.open_time[entry_idx]) + (0 if cfg.entry_mode == "next_open" else bar_ns)
        gross = funding_cost = 0.0
        fees = fee * qty * entry
        fill_rows = []
        for idx, price, frac, reason in fills:
            px = apply_slippage(price, direction, "exit", slip) if reason in MARKET_EXITS_ALL else price
            q = qty * frac
            gross += q * ((px - entry) if long else (entry - px))
            fees += fee * q * px
            funding_cost += _funding_cost(direction, q, funding_df, entry_ns, int(f.close_time[idx]), f)
            fill_rows.append({"time": pd.Timestamp(int(f.open_time[idx]), tz="UTC").isoformat(), "price": px,
                              "fraction": frac, "reason": reason})
        exit_price = sum(r["price"] * r["fraction"] for r in fill_rows) / max(sum(r["fraction"] for r in fill_rows),
                                                                              1e-12)
        net = gross - fees - funding_cost
        equity += net
        trades.append({
            "direction": direction,
            "strategy": setup.strategy,
            "signal_time": ev.open_time,
            "entry_time": pd.Timestamp(entry_ns, tz="UTC").isoformat(),
            "exit_time": pd.Timestamp(int(f.open_time[exit_index]), tz="UTC").isoformat(),
            "entry_price": entry,
            "exit_price": exit_price,
            "stop_loss": sl,
            "take_profit": tp,
            "targets": [t["price"] for t in plan_targets],
            "stop_method": (setup.plan.get("stop") or {}).get("method"),
            "fills": fill_rows,
            "quantity": qty,
            "notional": qty * entry,
            "score": setup.score,
            "mtf_confirmed": setup.mtf_confirmed,
            "exit_reason": exit_reason,
            "bars_held": bars_held,
            "gross_pnl": gross,
            "fees": fees,
            "funding": funding_cost,
            "net_pnl": net,
            "risk_amount": risk_amount,
            "r_multiple": net / risk_amount if risk_amount > 0 else math.nan,
            "equity_after": equity,
        })
        curve.append({"time": pd.Timestamp(int(f.close_time[exit_index]), tz="UTC").isoformat(),
                      "equity": equity})
        if equity <= 0:
            break
        # A new decision can be taken at the close of the exit candle.
        i = max(exit_index, i + 1)

    first_close = float(f.close[first]) if first < n else math.nan
    last_close = float(f.close[min(last_decision, n - 1)]) if n else math.nan
    metrics = compute_metrics(trades, cfg.initial_equity, curve)
    metrics["skipped_signals"] = len(skipped)
    metrics["candles_evaluated"] = max(0, min(last_decision, n - 1) - first + 1)
    metrics["buy_and_hold_return_pct"] = (last_close / first_close - 1) * 100 if first_close else None
    metrics["funding_included"] = funding_df is not None
    metrics["rejected_setup_reasons"] = dict(rejected_setups)
    metrics["trades_by_strategy"] = dict(Counter(t["strategy"] for t in trades))
    return BacktestResult(metrics, trades, curve, assumptions_for(cfg, funding_df is not None, rules is not None),
                          skipped)
