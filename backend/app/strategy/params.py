from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.timeutil import TIMEFRAMES

STRATEGY_NAME = "trend_breakout"  # legacy name of the Phase 1 rule set (range breakout)
STRATEGY_SEMVER = "2.0.0"
BREAKOUT_NAME = "zone_breakout"
PULLBACK_NAME = "zone_pullback"


class ScoringWeights(BaseModel):
    """Points per component. Rescaled to 0-100 over the components that were evaluated."""

    trend: float = Field(25, ge=0)
    momentum: float = Field(20, ge=0)
    breakout: float = Field(20, ge=0)
    volume: float = Field(15, ge=0)
    higher_timeframe: float = Field(15, ge=0)
    volatility: float = Field(5, ge=0)
    zone: float = Field(10, ge=0)
    room: float = Field(10, ge=0)


class MTFConfig(BaseModel):
    enabled: bool = True
    # Higher timeframes checked for agreement. Only timeframes strictly longer than the
    # signal timeframe are used.
    timeframes: list[str] = Field(default_factory=lambda: ["1h", "4h"])
    # quality: signals are kept and flagged; filter: signals without agreement are dropped.
    mode: Literal["quality", "filter"] = "quality"

    @model_validator(mode="after")
    def _check(self) -> MTFConfig:
        for tf in self.timeframes:
            if tf not in TIMEFRAMES:
                raise ValueError(f"unsupported timeframe {tf}")
        return self


class ZoneParams(BaseModel):
    """Support/resistance zone detection (see ``app.indicators.zones``)."""

    lookback: int = Field(300, ge=20, le=2000, description="Bars searched for confirmed pivots")
    cluster_atr_mult: float = Field(0.5, gt=0, le=5, description="Max zone width from clustering, in ATR")
    min_width_atr: float = Field(0.15, ge=0, le=2, description="Zones narrower than this are widened")
    min_tests: int = Field(2, ge=1, le=10, description="Confirmed swing tests required for a zone")
    break_buffer_atr: float = Field(0.1, ge=0, le=2, description="Close beyond a boundary by this much to break it")
    invalidate_after_crossings: int = Field(3, ge=1, le=20)
    max_zones_per_side: int = Field(4, ge=1, le=20)
    # Higher timeframes whose zones are shown and used as opposing levels.
    htf_timeframes: list[str] = Field(default_factory=lambda: ["1h", "4h"])

    @model_validator(mode="after")
    def _check(self) -> ZoneParams:
        for tf in self.htf_timeframes:
            if tf not in TIMEFRAMES:
                raise ValueError(f"unsupported timeframe {tf}")
        return self


class BreakoutParams(BaseModel):
    """Strategy A: confirmed close through a support/resistance zone in the trend direction."""

    enabled: bool = True
    # zone: break of a detected S/R zone. range: break of the N-bar high/low (Phase 1 rule).
    level_source: Literal["zone", "range"] = "zone"
    min_rvol: float = Field(1.2, ge=0, le=20, description="0 disables the volume requirement")
    min_body_ratio: float = Field(0.3, ge=0, le=1, description="Directional body / candle range")
    max_extension_atr: float = Field(1.5, ge=0, le=20, description="Max close distance beyond the zone; 0 = off")
    watch_distance_atr: float = Field(1.0, ge=0, le=10)


class PullbackParams(BaseModel):
    """Strategy B: retest of support (LONG) / resistance (SHORT) with a confirmed rejection candle."""

    enabled: bool = True
    touch_tolerance_atr: float = Field(0.1, ge=0, le=2)
    max_pierce_atr: float = Field(0.5, ge=0, le=5, description="Deepest allowed wick through the far boundary")
    require_close_beyond_zone: bool = True
    require_directional_candle: bool = True
    min_close_position: float = Field(0.6, ge=0, le=1, description="Close location in the candle range (LONG)")
    rsi_min_long: float = Field(40, ge=0, le=100)
    rsi_max_short: float = Field(60, ge=0, le=100)
    require_macd_turn: bool = True
    watch_distance_atr: float = Field(1.0, ge=0, le=10)


class StopParams(BaseModel):
    method: Literal["atr", "structure", "hybrid"] = "hybrid"
    atr_multiplier: float = Field(1.5, gt=0, le=20)
    hybrid_buffer_atr: float = Field(0.25, ge=0, le=5)
    min_stop_atr: float = Field(0.5, ge=0, le=10, description="Narrower stops are widened to this distance")
    max_stop_atr: float = Field(4.0, gt=0, le=50, description="Wider stops reject the setup")

    @model_validator(mode="after")
    def _check(self) -> StopParams:
        if self.min_stop_atr >= self.max_stop_atr:
            raise ValueError("min_stop_atr must be below max_stop_atr")
        return self


class TargetParams(BaseModel):
    r_multiples: list[float] = Field(default_factory=lambda: [1.0, 2.0, 3.0])
    primary_index: int = Field(1, ge=0, le=4, description="Target used for single-exit mode and the headline R:R")
    partial_exits: bool = False
    allocation_pct: list[float] = Field(default_factory=lambda: [30.0, 40.0, 30.0])
    move_stop_to_breakeven: bool = False

    @model_validator(mode="after")
    def _check(self) -> TargetParams:
        r = self.r_multiples
        if not 1 <= len(r) <= 5 or any(x <= 0 or x > 20 for x in r) or any(b <= a for a, b in zip(r, r[1:], strict=False)):
            raise ValueError("r_multiples must be 1-5 strictly increasing values in (0, 20]")
        if self.primary_index >= len(r):
            raise ValueError("primary_index must point at one of the r_multiples")
        if len(self.allocation_pct) != len(r) or any(a < 0 for a in self.allocation_pct):
            raise ValueError("allocation_pct needs one non-negative value per target")
        if abs(sum(self.allocation_pct) - 100) > 1e-6:
            raise ValueError("allocation_pct must sum to 100")
        return self


class QualificationParams(BaseModel):
    min_rr: float = Field(1.5, ge=0, le=20, description="Min reward room before the nearest opposing zone, in R")
    min_net_rr: float = Field(1.2, ge=0, le=20, description="Min fee/slippage-adjusted R:R at the primary target")
    reject_outside_volatility: bool = False


class StrategyParams(BaseModel):
    ema_fast: int = Field(20, ge=2, le=500)
    ema_mid: int = Field(50, ge=2, le=500)
    ema_slow: int = Field(200, ge=2, le=1000)
    sma_long: int = Field(200, ge=2, le=1000)
    rsi_period: int = Field(14, ge=2, le=100)
    rsi_long_min: float = Field(50, ge=0, le=100)
    rsi_long_max: float = Field(70, ge=0, le=100)
    rsi_short_min: float = Field(30, ge=0, le=100)
    rsi_short_max: float = Field(50, ge=0, le=100)
    macd_fast: int = Field(12, ge=2, le=100)
    macd_slow: int = Field(26, ge=3, le=200)
    macd_signal: int = Field(9, ge=2, le=100)
    breakout_lookback: int = Field(20, ge=2, le=500)
    atr_period: int = Field(14, ge=2, le=100)
    bb_period: int = Field(20, ge=2, le=200)
    bb_std: float = Field(2.0, gt=0, le=5)
    volume_ma_period: int = Field(20, ge=2, le=200)
    swing_strength: int = Field(3, ge=1, le=20)
    # Volatility "comfort zone" for scoring: ATR as a percentage of price.
    atr_pct_min: float = Field(0.05, ge=0)
    atr_pct_max: float = Field(3.0, gt=0)
    mtf: MTFConfig = Field(default_factory=MTFConfig)
    zones: ZoneParams = Field(default_factory=ZoneParams)
    breakout: BreakoutParams = Field(default_factory=BreakoutParams)
    pullback: PullbackParams = Field(default_factory=PullbackParams)
    stops: StopParams = Field(default_factory=StopParams)
    targets: TargetParams = Field(default_factory=TargetParams)
    qualification: QualificationParams = Field(default_factory=QualificationParams)
    weights: ScoringWeights = Field(default_factory=ScoringWeights)

    @model_validator(mode="after")
    def _check(self) -> StrategyParams:
        if not self.ema_fast < self.ema_mid < self.ema_slow:
            raise ValueError("EMA periods must satisfy fast < mid < slow")
        if self.macd_fast >= self.macd_slow:
            raise ValueError("MACD fast must be < slow")
        if self.rsi_long_min >= self.rsi_long_max or self.rsi_short_min >= self.rsi_short_max:
            raise ValueError("RSI band minimum must be below maximum")
        if self.atr_pct_min >= self.atr_pct_max:
            raise ValueError("atr_pct_min must be below atr_pct_max")
        if not (self.breakout.enabled or self.pullback.enabled):
            raise ValueError("enable at least one entry strategy (breakout or pullback)")
        if self.pullback.rsi_min_long >= self.rsi_long_max or self.pullback.rsi_max_short <= self.rsi_short_min:
            raise ValueError("pullback RSI limits must leave a non-empty band")
        return self

    @property
    def warmup_bars(self) -> int:
        """Bars needed before every indicator has a value."""
        return max(self.ema_slow, self.sma_long, self.macd_slow + self.macd_signal, self.breakout_lookback + 1) + 1

    def params_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:8]

    @property
    def version(self) -> str:
        """Strategy version: semantic version of the rules + hash of the parameter set."""
        return f"{STRATEGY_SEMVER}+{self.params_hash()}"
