"""Position-size and risk estimates for the risk calculator API. Nothing is executed.

Built on :func:`app.risk.sizing.size_position`, the same module that sizes every trade plan
and backtest trade, so all three agree.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.risk.sizing import InstrumentRules, SizingConfig, round_price, size_position


class RiskInput(BaseModel):
    account_balance: float = Field(gt=0)
    risk_pct: float = Field(1.0, gt=0, le=10)
    direction: Literal["LONG", "SHORT"]
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float | None = Field(None, gt=0)
    take_profits: list[float] | None = Field(None, max_length=5, description="Optional TP1..TPn")
    fee_rate_pct: float = Field(0.055, ge=0, le=5, description="Estimated fee per side, % of notional")
    slippage_pct: float = Field(0.0, ge=0, le=5, description="Adverse slippage on entry and stop, %")
    leverage: float | None = Field(None, ge=1, le=200, description="Preferred leverage; default = minimum needed")
    maintenance_margin_pct: float = Field(0.5, ge=0, le=50)
    symbol: str | None = Field(None, pattern=r"^[A-Z0-9]{2,20}USDT$", description="Validate against Bybit rules")
    include_fees_in_sizing: bool = True

    @model_validator(mode="after")
    def _check(self) -> RiskInput:
        long = self.direction == "LONG"
        if long and self.stop_loss >= self.entry_price:
            raise ValueError("for a LONG the stop-loss must be below the entry price")
        if not long and self.stop_loss <= self.entry_price:
            raise ValueError("for a SHORT the stop-loss must be above the entry price")
        for tp in [self.take_profit, *(self.take_profits or [])]:
            if tp is None:
                continue
            if long and tp <= self.entry_price:
                raise ValueError("for a LONG the take-profit must be above the entry price")
            if not long and tp >= self.entry_price:
                raise ValueError("for a SHORT the take-profit must be below the entry price")
        return self


class RiskOutput(BaseModel):
    max_loss: float
    quantity: float
    quantity_raw: float
    notional: float
    implied_leverage: float
    leverage: float
    initial_margin: float
    stop_distance: float
    stop_distance_pct: float
    estimated_entry_fee: float
    estimated_loss_at_stop_gross: float
    estimated_loss_at_stop_net: float
    risk_reward: float | None
    potential_gross_profit: float | None
    potential_net_profit: float | None
    estimated_fees_at_target: float | None
    net_risk_reward: float | None
    liquidation_price: float | None
    max_safe_leverage: float | None
    targets: list[dict[str, Any]]
    feasible: bool
    issues: list[dict[str, str]]
    warnings: list[str]
    instrument: dict[str, Any] | None
    note: str


def calculate_risk(inp: RiskInput, rules: InstrumentRules | None = None) -> RiskOutput:
    fee = inp.fee_rate_pct / 100
    warnings: list[str] = []
    if rules:
        for label, price in [("entry", inp.entry_price), ("stop-loss", inp.stop_loss), ("take-profit", inp.take_profit),
                             *[(f"TP{k + 1}", t) for k, t in enumerate(inp.take_profits or [])]]:
            if price is not None and not math.isclose(round_price(price, rules.tick_size), price, rel_tol=0,
                                                      abs_tol=rules.tick_size * 1e-6):
                warnings.append(f"The {label} {price:g} is not a multiple of the tick size {rules.tick_size:g}; "
                                f"Bybit would reject or round it.")
    sizing_fee = inp.fee_rate_pct if inp.include_fees_in_sizing else 0.0
    targets_in = []
    if inp.take_profit is not None:
        targets_in.append(("TP", inp.take_profit, 0.0, 100.0))
    for k, t in enumerate(inp.take_profits or []):
        targets_in.append((f"TP{k + 1}", t, 0.0, 0.0))

    def run(leverage: float):
        cfg = SizingConfig(account_balance=inp.account_balance, risk_pct=inp.risk_pct, fee_rate_pct=sizing_fee,
                           slippage_pct=inp.slippage_pct, leverage=leverage,
                           maintenance_margin_pct=inp.maintenance_margin_pct)
        return size_position(inp.direction, inp.entry_price, inp.stop_loss, cfg, rules)

    sized = run(inp.leverage or 1.0)
    if inp.leverage is None:
        needed = max(1.0, math.ceil(sized.notional / inp.account_balance * 100) / 100)
        sized = run(min(needed, 200.0))
    qty = sized.quantity
    long = inp.direction == "LONG"
    entry_fill, stop_fill = sized.entry_fill_estimate, sized.stop_fill_estimate
    distance = abs(inp.entry_price - inp.stop_loss)
    loss_net = qty * (abs(entry_fill - stop_fill) + fee * (entry_fill + stop_fill))
    targets: list[dict[str, Any]] = []
    for label, price, _, _ in targets_in:
        gross = qty * ((price - entry_fill) if long else (entry_fill - price))
        fees_tp = fee * qty * (entry_fill + price)
        targets.append({"label": label, "price": price, "r_multiple": abs(price - inp.entry_price) / distance,
                        "gross_profit": gross, "fees": fees_tp, "net_profit": gross - fees_tp,
                        "net_rr": (gross - fees_tp) / loss_net if loss_net > 0 else None})
    main = targets[0] if inp.take_profit is not None else None
    return RiskOutput(
        max_loss=sized.risk_budget,
        quantity=qty,
        quantity_raw=sized.quantity_raw,
        notional=sized.notional,
        implied_leverage=sized.notional / inp.account_balance,
        leverage=sized.leverage,
        initial_margin=sized.initial_margin,
        stop_distance=distance,
        stop_distance_pct=distance / inp.entry_price * 100,
        estimated_entry_fee=fee * qty * entry_fill,
        estimated_loss_at_stop_gross=qty * distance,
        estimated_loss_at_stop_net=loss_net,
        risk_reward=main["r_multiple"] if main else None,
        potential_gross_profit=main["gross_profit"] if main else None,
        potential_net_profit=main["net_profit"] if main else None,
        estimated_fees_at_target=main["fees"] if main else None,
        net_risk_reward=main["net_rr"] if main else None,
        liquidation_price=sized.liquidation_price,
        max_safe_leverage=sized.max_safe_leverage,
        targets=[t for t in targets if t["label"] != "TP"],
        feasible=sized.feasible,
        issues=sized.issues,
        warnings=warnings + sized.warnings,
        instrument=rules.to_dict() if rules else None,
        note=("Estimates only; nothing is executed. Take-profits are treated as limit orders without slippage. "
              "Actual losses can exceed the planned risk: slippage beyond the estimate, gaps through the stop, "
              "liquidation (estimated with the lowest-tier maintenance margin, isolated margin) and funding are "
              "not guaranteed bounds."),
    )
