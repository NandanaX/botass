"""Configuration of the forward paper-trading experiment, its risk controls and the
promotion gate. Defaults are deliberately conservative; see docs/FORWARD_VALIDATION.md for
how each threshold was chosen."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PaperCosts(BaseModel):
    """Execution model of paper fills (percent of price unless stated)."""

    starting_equity: float = Field(10_000, gt=0, le=1e9, description="paper account per variant, USDT")
    taker_fee_pct: float = Field(0.055, ge=0, le=1, description="market entries, stop and time exits")
    maker_fee_pct: float = Field(0.02, ge=0, le=1, description="resting take-profit orders")
    slippage_pct: float = Field(0.02, ge=0, le=2, description="market-entry slippage beyond the quoted bid/ask")
    stop_slippage_pct: float = Field(0.05, ge=0, le=5, description="stop exits fill this much worse than the trigger")
    assumed_spread_pct: float = Field(0.02, ge=0, le=2, description="used only when no fresh bid/ask exists")
    max_quote_age_s: float = Field(5.0, gt=0, le=600, description="older bid/ask counts as stale")
    max_signal_delay_candles: float = Field(1.0, gt=0, le=10, description="detected later than this = MISSED")
    max_holding_bars: int = Field(96, ge=1, le=5000)


class RiskLimits(BaseModel):
    """Portfolio safeguards of each paper account. These are risk controls, not signal
    improvements: they limit exposure whether or not they raise expectancy."""

    risk_per_trade_pct: float = Field(0.5, gt=0, le=5, description="account risk per trade")
    max_open_risk_pct: float = Field(2.0, gt=0, le=50, description="sum of open risk")
    max_correlated_positions: int = Field(1, ge=0, le=50, description="same-direction positions with corr >= threshold")
    corr_threshold: float = Field(0.7, ge=0, le=1)
    corr_window_hours: int = Field(168, ge=24, le=5000)
    daily_loss_limit_pct: float = Field(2.0, gt=0, le=50, description="circuit breaker for the UTC day")
    max_drawdown_pct: float = Field(10.0, gt=0, le=100, description="circuit breaker until manually reset")
    require_fresh_quote: bool = Field(True, description="data-integrity breaker: no entry without a fresh quote")


class GateThresholds(BaseModel):
    """Promotion-gate criteria for READY FOR REVIEW (never automatic promotion)."""

    min_forward_days: float = Field(60, ge=0, le=3650)
    min_closed_trades: int = Field(60, ge=0, le=100_000)
    min_independent_clusters: int = Field(40, ge=0, le=100_000)
    min_prob_positive: float = Field(0.90, ge=0, le=1, description="cluster-bootstrap P(avg net R > 0)")
    min_avg_net_r: float = Field(0.05, ge=-1, le=5)
    max_best_month_share: float = Field(0.5, ge=0, le=1, description="of total positive monthly R")
    min_positive_thirds: int = Field(2, ge=0, le=3, description="of three equal forward sub-periods")
    max_forward_drawdown_r: float = Field(15.0, gt=0, le=1000)
    max_prob_drawdown_beyond: float = Field(0.2, ge=0, le=1)
    drawdown_threshold_r: float = Field(20.0, gt=0, le=1000)
    max_estimated_cost_share: float = Field(0.25, ge=0, le=1, description="trades with non-observed fills or exits")
    max_missed_share: float = Field(0.05, ge=0, le=1)
    max_test_age_days: float = Field(7, ge=0, le=365)


class ValidationSettings(BaseModel):
    enabled: bool = True
    paper: PaperCosts = Field(default_factory=PaperCosts)
    risk: RiskLimits = Field(default_factory=RiskLimits)
    gate: GateThresholds = Field(default_factory=GateThresholds)
