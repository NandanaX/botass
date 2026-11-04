"""Configuration of the technical analysis engine.

Kept separate from ``StrategyParams`` so that changing analysis tolerances does not change
signal identifiers. ``version`` (semver + parameter hash) is stored with every persisted
pattern detection.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, Field, model_validator

ANALYSIS_SEMVER = "1.0.0"


class StructureParams(BaseModel):
    pivot_window: int = Field(3, ge=1, le=20, description="Bars on each side of a confirmed swing")
    equal_tolerance_atr: float = Field(0.15, ge=0, le=2, description="Swings within this are 'equal' highs/lows")
    break_buffer_atr: float = Field(0.05, ge=0, le=2, description="Close beyond the swing by this much to break it")
    range_atr: float = Field(4.0, gt=0, le=50, description="Recent swing range below this x ATR = consolidation")


class CandleParams(BaseModel):
    doji_body_pct: float = Field(0.1, gt=0, le=0.5)
    small_body_pct: float = Field(0.33, gt=0, le=0.6)
    long_wick_ratio: float = Field(2.0, gt=0, le=10, description="Wick >= this x body for hammer/star shapes")
    marubozu_wick_pct: float = Field(0.05, ge=0, le=0.3)
    trend_lookback: int = Field(10, ge=3, le=100)
    level_distance_atr: float = Field(0.5, ge=0, le=5, description="'At a level' if within this x ATR")
    outcome_bars: int = Field(10, ge=1, le=200, description="Bars ahead used to evaluate historical outcomes")


class ChartPatternParams(BaseModel):
    pivot_window: int = Field(5, ge=2, le=30, description="Larger swings for classical patterns")
    lookback_pivots: int = Field(8, ge=4, le=30)
    max_age_bars: int = Field(150, ge=20, le=2000, description="Ignore patterns whose last pivot is older")
    equal_tolerance_atr: float = Field(0.6, gt=0, le=5, description="Tops/bottoms 'equal' within this x ATR")
    flat_slope_atr: float = Field(0.02, ge=0, le=1, description="Boundary slope per bar below this x ATR = flat")
    min_height_atr: float = Field(2.0, gt=0, le=50, description="Minimum pattern height")
    breakout_buffer_atr: float = Field(0.1, ge=0, le=2)
    pole_min_atr: float = Field(4.0, gt=0, le=50, description="Flagpole move for bull/bear flags")


class LiquidityParams(BaseModel):
    equal_level_tolerance_atr: float = Field(0.1, ge=0, le=2)
    fvg_min_atr: float = Field(0.2, ge=0, le=5, description="Minimum fair-value-gap size")
    sweep_lookback: int = Field(50, ge=5, le=1000)
    max_fvgs: int = Field(6, ge=0, le=50)


class EvidenceWeights(BaseModel):
    structure: float = Field(20, ge=0)
    trend: float = Field(20, ge=0)
    momentum: float = Field(15, ge=0)
    levels: float = Field(15, ge=0)
    price_action: float = Field(10, ge=0)
    volume: float = Field(10, ge=0)
    volatility: float = Field(5, ge=0)
    futures: float = Field(5, ge=0)


class AnalysisParams(BaseModel):
    structure: StructureParams = Field(default_factory=StructureParams)
    candles: CandleParams = Field(default_factory=CandleParams)
    patterns: ChartPatternParams = Field(default_factory=ChartPatternParams)
    liquidity: LiquidityParams = Field(default_factory=LiquidityParams)
    weights: EvidenceWeights = Field(default_factory=EvidenceWeights)
    adx_period: int = Field(14, ge=2, le=100)
    adx_trend: float = Field(25, ge=0, le=100, description="ADX above this = trending")
    supertrend_mult: float = Field(3.0, gt=0, le=20)
    stoch_period: int = Field(14, ge=2, le=100)
    bias_threshold: float = Field(0.2, gt=0, le=1, description="|net evidence| above this = directional bias")
    min_evidence_categories: int = Field(3, ge=1, le=8, description="Fewer evaluable categories = insufficient")

    @model_validator(mode="after")
    def _check(self) -> AnalysisParams:
        if sum(self.weights.model_dump().values()) <= 0:
            raise ValueError("at least one evidence weight must be positive")
        return self

    @property
    def version(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return f"{ANALYSIS_SEMVER}+{hashlib.sha256(payload.encode()).hexdigest()[:8]}"
