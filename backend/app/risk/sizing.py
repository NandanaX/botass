"""Position sizing for Bybit linear (USDT-margined) perpetuals, with exchange-rule checks.

Everything here is an *estimate* for planning. Nothing is executed. Real losses can exceed
the planned risk because of slippage beyond the estimate, gaps through the stop,
liquidation, funding, and operational failures.

Sizing formula (LONG; mirrored for SHORT)::

    entry_fill = entry x (1 + slippage)          # adverse
    stop_fill  = stop  x (1 - slippage)          # adverse
    loss/unit  = (entry_fill - stop_fill) + fee x (entry_fill + stop_fill)
    quantity   = floor_to_step(risk_budget / loss/unit)

Rounding the quantity *down* guarantees ``quantity x loss/unit <= risk_budget``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

Direction = Literal["LONG", "SHORT"]


@dataclass(frozen=True, slots=True)
class InstrumentRules:
    symbol: str
    tick_size: float
    qty_step: float
    min_qty: float
    max_qty: float
    max_market_qty: float | None
    min_notional: float
    max_leverage: float
    maintenance_margin_rate: float | None = None  # lowest risk tier, from /v5/market/risk-limit
    base_coin: str = ""

    @classmethod
    def from_bybit(cls, info: dict[str, Any], risk_tiers: list[dict[str, Any]] | None = None) -> InstrumentRules:
        lot = info.get("lotSizeFilter") or {}
        price = info.get("priceFilter") or {}
        lev = info.get("leverageFilter") or {}
        mmr = None
        for tier in risk_tiers or []:
            if str(tier.get("isLowestRisk")) == "1" or mmr is None:
                try:
                    mmr = float(tier["maintenanceMargin"])
                except (KeyError, TypeError, ValueError):
                    continue
                if str(tier.get("isLowestRisk")) == "1":
                    break
        mmq = lot.get("maxMktOrderQty")
        return cls(
            symbol=info["symbol"],
            tick_size=float(price["tickSize"]),
            qty_step=float(lot["qtyStep"]),
            min_qty=float(lot["minOrderQty"]),
            max_qty=float(lot["maxOrderQty"]),
            max_market_qty=float(mmq) if mmq else None,
            min_notional=float(lot.get("minNotionalValue") or 0),
            max_leverage=float(lev.get("maxLeverage") or 100),
            maintenance_margin_rate=mmr,
            base_coin=info.get("baseCoin", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _round_to(value: float, step: float, mode: str) -> float:
    if step <= 0:
        return value
    d = (Decimal(str(value)) / Decimal(str(step))).quantize(Decimal(1), rounding=mode) * Decimal(str(step))
    return float(d)


def round_price(price: float, tick: float | None, mode: Literal["nearest", "down", "up"] = "nearest") -> float:
    if not tick:
        return price
    return _round_to(price, tick, {"nearest": ROUND_HALF_UP, "down": ROUND_FLOOR, "up": ROUND_CEILING}[mode])


def floor_qty(qty: float, step: float | None) -> float:
    if not step:
        return qty
    # Small epsilon so 0.3 / 0.1 style float noise does not drop a whole step.
    return _round_to(qty + step * 1e-9, step, ROUND_FLOOR)


class SizingConfig(BaseModel):
    account_balance: float = Field(10_000, gt=0, description="USDT")
    risk_pct: float = Field(1.0, gt=0, le=10)
    fee_rate_pct: float = Field(0.055, ge=0, le=5, description="Per side, % of notional (taker)")
    slippage_pct: float = Field(0.02, ge=0, le=5)
    leverage: float = Field(5, ge=1, le=200)
    maintenance_margin_pct: float = Field(0.5, ge=0, le=50, description="Fallback when the exchange tier is unknown")


@dataclass(slots=True)
class TargetResult:
    label: str
    price: float
    r_multiple: float
    allocation_pct: float
    gross_profit: float
    fees: float
    net_profit: float
    net_rr: float


@dataclass(slots=True)
class SizingResult:
    feasible: bool
    risk_budget: float
    quantity_raw: float
    quantity: float
    notional: float
    leverage: float
    initial_margin: float
    entry_fill_estimate: float
    stop_fill_estimate: float
    loss_per_unit: float
    price_risk: float  # quantity x |entry - stop|, before costs
    estimated_loss: float  # with fees and adverse slippage at the stop
    estimated_entry_fee: float
    liquidation_price: float | None
    max_safe_leverage: float | None
    targets: list[TargetResult] = field(default_factory=list)
    weighted_net_profit: float | None = None
    issues: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rules: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def liquidation_price(direction: Direction, entry: float, leverage: float, mmr: float) -> float:
    """Isolated-margin estimate ignoring fees and funding: LONG ``entry x (1 - 1/L + mmr)``."""
    if direction == "LONG":
        return entry * (1 - 1 / leverage + mmr)
    return entry * (1 + 1 / leverage - mmr)


def size_position(
    direction: Direction,
    entry: float,
    stop: float,
    cfg: SizingConfig,
    rules: InstrumentRules | None = None,
    targets: list[tuple[str, float, float, float]] | None = None,
) -> SizingResult:
    """``targets``: ``(label, price, r_multiple, allocation_pct)`` tuples."""
    long = direction == "LONG"
    if entry <= 0 or stop <= 0 or (long and stop >= entry) or (not long and stop <= entry):
        raise ValueError(f"invalid stop for {direction}: entry {entry}, stop {stop}")
    fee = cfg.fee_rate_pct / 100
    slip = cfg.slippage_pct / 100
    budget = cfg.account_balance * cfg.risk_pct / 100
    entry_fill = entry * (1 + slip) if long else entry * (1 - slip)
    stop_fill = stop * (1 - slip) if long else stop * (1 + slip)
    loss_per_unit = abs(entry_fill - stop_fill) + fee * (entry_fill + stop_fill)
    qty_raw = budget / loss_per_unit
    issues: list[dict[str, str]] = []
    warnings: list[str] = []
    qty = floor_qty(qty_raw, rules.qty_step if rules else None)
    if rules:
        cap = rules.max_market_qty or rules.max_qty
        if qty > cap:
            qty = floor_qty(cap, rules.qty_step)
            warnings.append(f"Quantity capped at the exchange maximum market order size {cap:g}; "
                            "planned risk is lower than the budget.")
        if qty < rules.min_qty:
            issues.append({"code": "below_min_qty",
                           "message": f"Risk budget allows {qty_raw:.6g} units, below the minimum order "
                                      f"quantity {rules.min_qty:g}."})
    leverage = cfg.leverage
    if rules and leverage > rules.max_leverage:
        issues.append({"code": "leverage_above_exchange_max",
                       "message": f"Leverage {leverage:g}x exceeds the exchange maximum {rules.max_leverage:g}x."})
    notional = qty * entry
    if rules and qty >= rules.min_qty and notional < rules.min_notional:
        issues.append({"code": "below_min_notional",
                       "message": f"Position notional {notional:.2f} USDT is below the minimum "
                                  f"{rules.min_notional:g} USDT."})
    margin = notional / leverage
    if margin > cfg.account_balance:
        issues.append({"code": "insufficient_margin",
                       "message": f"Initial margin {margin:.2f} USDT at {leverage:g}x exceeds the account balance "
                                  f"{cfg.account_balance:.2f} USDT; a higher leverage would be needed "
                                  f"(at least {notional / cfg.account_balance:.1f}x)."})
    mmr = (rules.maintenance_margin_rate if rules and rules.maintenance_margin_rate is not None
           else cfg.maintenance_margin_pct / 100)
    liq = liquidation_price(direction, entry, leverage, mmr)
    # Leverage at which the estimated liquidation price would sit exactly at the stop.
    stop_frac = abs(entry - stop) / entry
    safe = 1 / (stop_frac + mmr) if stop_frac + mmr > 0 else None
    if (long and liq >= stop) or (not long and liq <= stop):
        issues.append({"code": "liquidation_before_stop",
                       "message": f"Estimated liquidation price {liq:.6g} is reached before the stop {stop:.6g} at "
                                  f"{leverage:g}x; use at most about {safe:.1f}x." if safe else
                                  "Estimated liquidation price is reached before the stop."})
    elif safe and leverage > 0.8 * safe:
        warnings.append(f"Estimated liquidation ({liq:.6g}) is close to the stop; consider leverage below "
                        f"{0.8 * safe:.1f}x.")
    price_risk = qty * abs(entry - stop)
    est_loss = qty * loss_per_unit
    t_results: list[TargetResult] = []
    weighted: float | None = None
    if targets:
        weighted = 0.0
        for label, price, r, alloc in targets:
            tp_fill = price  # take-profit modelled as a resting limit order: no slippage
            gross = qty * ((tp_fill - entry_fill) if long else (entry_fill - tp_fill))
            fees = fee * qty * (entry_fill + tp_fill)
            net = gross - fees
            t_results.append(TargetResult(label, price, r, alloc, gross, fees, net,
                                          net / est_loss if est_loss > 0 else math.nan))
            weighted += net * alloc / 100
    feasible = not issues and qty > 0
    if qty <= 0 and not any(i["code"] == "below_min_qty" for i in issues):
        issues.append({"code": "zero_quantity", "message": "Risk budget is too small for any position."})
        feasible = False
    return SizingResult(
        feasible=feasible, risk_budget=budget, quantity_raw=qty_raw, quantity=qty, notional=notional,
        leverage=leverage, initial_margin=margin, entry_fill_estimate=entry_fill, stop_fill_estimate=stop_fill,
        loss_per_unit=loss_per_unit, price_risk=price_risk, estimated_loss=est_loss,
        estimated_entry_fee=fee * qty * entry_fill, liquidation_price=liq, max_safe_leverage=safe,
        targets=t_results, weighted_net_profit=weighted, issues=issues, warnings=warnings,
        rules=rules.to_dict() if rules else None,
    )
