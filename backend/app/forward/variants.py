"""The three forward-evaluated strategy variants.

All three see exactly the same production signals (``evaluate_at`` QUALIFIED setups on
closed candles); they differ only in the rule that turns a signal into a paper trade:

* ``A`` Baseline: the production plan (hybrid stop, primary target).
* ``B`` 2.5 ATR stop: entry -/+ 2.5 x ATR stop, 2R target (position size rescaled so the
  risk stays the same).
* ``C`` 2.5 ATR + HTF: as B, but only when every higher timeframe's EMA trend agrees
  (``mtf_confirmed``); otherwise REJECTED.

A variant's ``version`` hashes its own rules, the production strategy version and the paper
cost/risk configuration. Any change creates a new version and a new evaluation period, so
results of one period always come from one fixed configuration.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.forward.settings import ValidationSettings
from app.strategy.engine import SignalSetup

VARIANT_RULES: dict[str, dict[str, Any]] = {
    "A": {"name": "Baseline", "stop": "production", "require_mtf": False},
    "B": {"name": "2.5 ATR stop", "stop": "atr", "atr_multiplier": 2.5, "target_r": 2.0, "require_mtf": False},
    "C": {"name": "2.5 ATR + HTF", "stop": "atr", "atr_multiplier": 2.5, "target_r": 2.0, "require_mtf": True},
}


def variant_config(key: str, strategy_version: str, vs: ValidationSettings) -> dict[str, Any]:
    return {"rules": VARIANT_RULES[key], "strategy_version": strategy_version,
            "paper": vs.paper.model_dump(), "risk": vs.risk.model_dump()}


def variant_version(cfg: dict[str, Any]) -> str:
    h = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:10]
    return f"{cfg['strategy_version']}~{h}"


@dataclass(slots=True)
class VariantPlan:
    accepted: bool
    reason: str
    stop: float | None = None
    target: float | None = None


def plan_for(key: str, setup: SignalSetup) -> VariantPlan:
    """Decision-time rule of a variant for one QUALIFIED production setup."""
    rules = VARIANT_RULES[key]
    if rules["require_mtf"] and setup.mtf_confirmed is not True:
        why = ("higher timeframes do not all agree" if setup.mtf_confirmed is False
               else "no higher timeframe configured for this timeframe")
        return VariantPlan(False, f"rejected by HTF confirmation: {why}")
    if rules["stop"] == "production":
        return VariantPlan(True, "production plan", setup.stop_loss, setup.take_profit)
    sign = 1 if setup.direction == "LONG" else -1
    d = rules["atr_multiplier"] * setup.atr
    entry = float(setup.entry_reference)
    return VariantPlan(True, f"{rules['atr_multiplier']:g} ATR stop, {rules['target_r']:g}R target",
                       entry - sign * d, entry + sign * rules["target_r"] * d)
