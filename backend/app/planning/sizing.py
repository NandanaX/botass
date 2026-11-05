"""Futures position calculator for linear USDT perpetuals. Estimates only; nothing is executed.

::

    risk budget  = equity x risk %
    entry fill   = entry -/+ entry slippage (always adverse)
    stop fill    = stop  -/+ stop slippage (always adverse)
    loss / unit  = |entry fill - stop fill| + entry fee x entry fill + exit fee x stop fill
    quantity     = risk budget / (loss / unit), rounded DOWN to the exchange step
    notional     = quantity x entry
    margin       = notional / leverage

Rounding down keeps the planned loss within the budget. If the smallest order the exchange allows
would lose more than the budget, the trade is refused rather than rounded up. Leverage and risk are
taken as given and are never raised to make a trade fit. A planned loss is not a maximum loss.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.planning.targets import Costs
from app.risk.sizing import InstrumentRules, floor_qty, liquidation_price, round_price

LOSS_NOTE = ("Planned loss is an estimate, not a maximum. A stop fills at the next available price, so gaps, thin "
             "liquidity, liquidation and exchange outages can make the real loss larger. Funding isn't included.")
GAP_STRESS_PCT = (0.5, 1.0)


class TargetIn(BaseModel):
    label: str = Field(max_length=12)
    price: float = Field(gt=0)
    allocation_pct: float | None = Field(None, ge=0, le=100)


class PositionInput(BaseModel):
    symbol: str | None = Field(None, pattern=r"^[A-Z0-9]{2,20}USDT$")
    direction: Literal["LONG", "SHORT"]
    equity: float = Field(gt=0, le=1e9)
    risk_pct: float = Field(gt=0, le=10)
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    leverage: float = Field(ge=1, le=200)
    entry_fee_pct: float = Field(0.055, ge=0, le=5)
    exit_fee_pct: float = Field(0.055, ge=0, le=5)
    entry_slippage_pct: float = Field(0.02, ge=0, le=5)
    stop_slippage_pct: float = Field(0.05, ge=0, le=5)
    maintenance_margin_pct: float = Field(0.5, ge=0, le=50)
    available_margin: float | None = Field(None, ge=0, description="Defaults to the equity")
    open_risk_usdt: float = Field(0, ge=0, description="Planned risk already open")
    max_open_risk_pct: float | None = Field(None, gt=0, le=100, description="Portfolio limit on open risk")
    targets: list[TargetIn] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def _check(self) -> PositionInput:
        long = self.direction == "LONG"
        if (long and self.stop >= self.entry) or (not long and self.stop <= self.entry):
            raise ValueError(f"for a {self.direction} the stop must be {'below' if long else 'above'} the entry")
        for t in self.targets:
            if (long and t.price <= self.entry) or (not long and t.price >= self.entry):
                raise ValueError(f"{t.label}: for a {self.direction} the target must be {'above' if long else 'below'} the entry")
        return self


def decimals_of(step: float | None) -> int | None:
    """Decimal places of an exchange increment such as 0.001 or 1e-05."""
    if not step:
        return None
    text = f"{step:.12f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def plan_position(inp: PositionInput, rules: InstrumentRules | None = None) -> dict[str, Any]:
    long = inp.direction == "LONG"
    tick = rules.tick_size if rules else None
    entry = round_price(inp.entry, tick)
    stop = round_price(inp.stop, tick, "down" if long else "up")  # away from the entry, so it never tightens
    costs = Costs(inp.entry_fee_pct, inp.exit_fee_pct, inp.entry_slippage_pct, inp.stop_slippage_pct)
    budget = inp.equity * inp.risk_pct / 100
    entry_fill = costs.entry_fill(inp.direction, entry)
    slip = inp.stop_slippage_pct / 100
    stop_fill = stop * (1 - slip) if long else stop * (1 + slip)
    entry_fee_unit = inp.entry_fee_pct / 100 * entry_fill
    stop_fee_unit = inp.exit_fee_pct / 100 * stop_fill
    loss_unit = abs(entry_fill - stop_fill) + entry_fee_unit + stop_fee_unit
    qty_raw = budget / loss_unit
    issues: list[dict[str, str]] = []
    warnings: list[str] = []
    qty = floor_qty(qty_raw, rules.qty_step if rules else None)
    min_order: float | None = None
    if rules:
        cap = rules.max_market_qty or rules.max_qty
        if qty > cap:
            qty = floor_qty(cap, rules.qty_step)
            warnings.append(f"Quantity capped at the exchange maximum of {cap:g}, so the planned risk is below the budget.")
        min_order = max(rules.min_qty, round_price(rules.min_notional / entry, rules.qty_step, "up"))
        if qty < min_order:
            loss_at_min = min_order * loss_unit
            issues.append({"code": "min_order_exceeds_budget",
                           "message": f"The smallest order the exchange allows ({min_order:g}) would lose about {loss_at_min:.2f} "
                                      f"USDT at the stop, more than the {budget:.2f} USDT budget."})
            qty = 0.0
    if qty <= 0 and not issues:
        issues.append({"code": "zero_quantity", "message": "The risk budget is too small for any position."})
    notional = qty * entry
    margin = notional / inp.leverage
    available = inp.available_margin if inp.available_margin is not None else inp.equity
    if margin > available:
        issues.append({"code": "insufficient_margin",
                       "message": f"Needs {margin:.2f} USDT of margin at {inp.leverage:g}x, but only {available:.2f} USDT is "
                                  "available. Leverage isn't raised automatically."})
    if rules and inp.leverage > rules.max_leverage:
        issues.append({"code": "leverage_above_exchange_max",
                       "message": f"{inp.leverage:g}x is above the exchange maximum of {rules.max_leverage:g}x."})
    mmr = rules.maintenance_margin_rate if rules and rules.maintenance_margin_rate is not None else inp.maintenance_margin_pct / 100
    liq = liquidation_price(inp.direction, entry, inp.leverage, mmr)
    stop_frac = abs(entry - stop) / entry
    safe = 1 / (stop_frac + mmr) if stop_frac + mmr > 0 else None
    if (long and liq >= stop) or (not long and liq <= stop):
        issues.append({"code": "liquidation_before_stop",
                       "message": f"Estimated liquidation at {liq:.6g} comes before the stop at {stop:.6g} with {inp.leverage:g}x. "
                                  f"The most that keeps the stop first is about {safe:.1f}x."})
    elif safe and inp.leverage > 0.8 * safe:
        warnings.append(f"Estimated liquidation ({liq:.6g}) is close to the stop. Consider less than {0.8 * safe:.1f}x.")
    planned_loss = qty * loss_unit
    if inp.max_open_risk_pct is not None:
        limit = inp.equity * inp.max_open_risk_pct / 100
        if inp.open_risk_usdt + planned_loss > limit:
            issues.append({"code": "portfolio_risk_limit",
                           "message": f"Open risk would reach {inp.open_risk_usdt + planned_loss:.2f} USDT, above the "
                                      f"{limit:.2f} USDT limit ({inp.max_open_risk_pct:g}% of equity)."})
    targets = []
    weighted = 0.0 if any(t.allocation_pct is not None for t in inp.targets) else None
    for t in inp.targets:
        price = round_price(t.price, tick, "down" if long else "up")  # toward the entry
        gross = qty * (price - entry_fill if long else entry_fill - price)
        fees = qty * (entry_fee_unit + inp.exit_fee_pct / 100 * price)
        net = gross - fees
        targets.append({"label": t.label, "price": price, "gross_profit": gross, "fees": fees, "net_profit": net,
                        "net_rr": net / planned_loss if planned_loss > 0 else None, "allocation_pct": t.allocation_pct})
        if weighted is not None and t.allocation_pct is not None:
            weighted += net * t.allocation_pct / 100
    gaps = []
    for pct in GAP_STRESS_PCT:
        worse = stop * (1 - pct / 100) if long else stop * (1 + pct / 100)
        loss = qty * (abs(entry_fill - worse) + entry_fee_unit + inp.exit_fee_pct / 100 * worse)
        gaps.append({"gap_pct": pct, "loss": loss, "pct_of_equity": 100 * loss / inp.equity})
    return {
        "feasible": not issues and qty > 0,
        "entry": entry, "stop": stop, "entry_fill_estimate": entry_fill, "stop_fill_estimate": stop_fill,
        "risk_budget": budget, "risk_pct": inp.risk_pct, "equity": inp.equity,
        "loss_per_unit": loss_unit, "quantity_raw": qty_raw, "quantity": qty,
        "quantity_decimals": decimals_of(rules.qty_step) if rules else None,
        "price_decimals": decimals_of(rules.tick_size) if rules else None,
        "min_order_quantity": min_order, "base_coin": rules.base_coin if rules else None,
        "notional": notional, "leverage": inp.leverage, "initial_margin": margin, "effective_exposure_x": notional / inp.equity,
        "price_risk": qty * abs(entry - stop), "planned_loss": planned_loss,
        "planned_loss_pct_of_equity": 100 * planned_loss / inp.equity,
        "estimated_entry_fee": qty * entry_fee_unit, "estimated_stop_fee": qty * stop_fee_unit,
        "liquidation_price": liq if qty > 0 else None, "max_safe_leverage": safe,
        "targets": targets, "weighted_net_profit": weighted, "stop_gap_stress": gaps,
        "open_risk_usdt": inp.open_risk_usdt, "max_open_risk_pct": inp.max_open_risk_pct,
        "assumptions": {"entry_fee_pct": inp.entry_fee_pct, "exit_fee_pct": inp.exit_fee_pct,
                        "entry_slippage_pct": inp.entry_slippage_pct, "stop_slippage_pct": inp.stop_slippage_pct,
                        "exchange_rules": rules is not None},
        "issues": issues, "warnings": warnings, "note": LOSS_NOTE,
    }
