"""Immutable, versioned parameters of Strategy D.

They are code, not settings: nothing at runtime can change them, and any edit changes the version, so
one version's results always come from one configuration. The values were fixed before any D result
was seen. The weights are research assumptions, not calibrated probabilities.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from app.forward.settings import RiskLimits
from app.strategy.params import StrategyParams

STRATEGY_ID = "D"
STRATEGY_NAME = "15M Multi-Timeframe Confluence"
PRIMARY_TIMEFRAME = "15m"
CONTEXT_TIMEFRAMES = ("1d", "4h", "1h")


def _base_params() -> StrategyParams:
    """Indicator and trigger settings, pinned so that editing the live strategy can't change D."""
    p = StrategyParams()
    return p.model_copy(update={"mtf": p.mtf.model_copy(update={"enabled": True, "timeframes": ["1h", "4h"]}),
                                "zones": p.zones.model_copy(update={"htf_timeframes": ["1h", "4h"]})})


class DParams(BaseModel):
    model_config = ConfigDict(frozen=True)

    semver: str = "1.0.0"
    weights: dict[str, float] = Field(default_factory=lambda: {"1d": 15.0, "4h": 30.0, "1h": 30.0, "15m": 25.0})
    neutral_value: float = 0.4  # SIDEWAYS or CONFLICTED earns part of a timeframe's weight
    qualify_score: float = 70.0
    watch_score: float = 55.0
    adx_period: int = 14
    adx_min: float = 20.0
    slope_bars: int = 3
    min_bars: int = 200
    retest_lookback_bars: int = 12
    retest_touch_atr: float = 0.1
    stop_method: str = "atr_2_5"
    target_r: tuple[float, ...] = (1.0, 2.0, 3.0)
    target_weights: tuple[float, ...] = (0.3, 0.4, 0.3)
    min_room_r: float = 1.0
    min_stop_atr: float = 0.5
    max_stop_atr: float = 4.0
    cooldown_bars: int = 4
    max_bars: int = 96
    max_detection_delay_candles: float = 1.0
    starting_equity: float = 10_000.0
    risk: RiskLimits = Field(default_factory=lambda: RiskLimits(require_fresh_quote=False))
    base: StrategyParams = Field(default_factory=_base_params)

    @property
    def version(self) -> str:
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return f"D-{self.semver}+{hashlib.sha256(blob.encode()).hexdigest()[:8]}"


D_PARAMS = DParams()
