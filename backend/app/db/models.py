"""ORM models. Portable between PostgreSQL (production) and SQLite (local/tests)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import UTCDateTime
from app.timeutil import utcnow


class Base(DeclarativeBase):
    pass


class Candle(Base):
    __tablename__ = "candles"

    exchange: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    turnover: Mapped[float] = mapped_column(Float)


class FundingRate(Base):
    __tablename__ = "funding_rates"

    exchange: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    funding_time: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True)
    rate: Mapped[float] = mapped_column(Float)


class Signal(Base):
    __tablename__ = "signals"

    # Deterministic id derived from exchange/symbol/timeframe/direction/candle/strategy version.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    strategy_name: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    candle_open_time: Mapped[datetime] = mapped_column(UTCDateTime())
    candle_close_time: Mapped[datetime] = mapped_column(UTCDateTime())
    entry_reference: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    risk_reward: Mapped[float] = mapped_column(Float)
    atr: Mapped[float] = mapped_column(Float)
    score: Mapped[int] = mapped_column(Integer)
    score_breakdown: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    indicators: Mapped[dict[str, Any]] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(Text)
    mtf_confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    mtf_details: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Phase 2: entry strategy, qualification status and the full trade plan (zones, stop
    # method, TP1-TP3, sizing, invalidation). QUALIFIED at creation; EXPIRED if the
    # hypothetical entry never became valid.
    strategy_type: Mapped[str] = mapped_column(String(16), default="breakout", server_default="breakout")
    setup_status: Mapped[str] = mapped_column(String(16), default="QUALIFIED", server_default="QUALIFIED")
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    notification_status: Mapped[str] = mapped_column(String(16), default="PENDING")
    # Outcome classification according to the documented methodology (see app.signals.evaluation).
    evaluation_status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    evaluation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (Index("ix_signals_symbol_tf_time", "symbol", "timeframe", "candle_open_time"),)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    signal_id: Mapped[str | None] = mapped_column(
        ForeignKey("signals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(16), default="telegram")
    kind: Mapped[str] = mapped_column(String(16), default="signal")  # signal | test
    status: Mapped[str] = mapped_column(String(16))  # SENT | FAILED | SKIPPED
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))  # QUEUED | RUNNING | COMPLETED | FAILED
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    trades: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    equity_curve: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    assumptions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class AnalysisSnapshot(Base):
    """Latest analysis per symbol/timeframe, written by the scanner and read by the API."""

    __tablename__ = "analysis_snapshots"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class PriceAlert(Base):
    """User-defined price level. Fires once (Telegram) when the live last price trades through it."""

    __tablename__ = "price_alerts"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    condition: Mapped[str] = mapped_column(String(8))  # above | below
    price: Mapped[float] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)  # ACTIVE | TRIGGERED | CANCELLED
    price_at_creation: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    triggered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    trigger_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    notification_status: Mapped[str | None] = mapped_column(String(16), nullable=True)


class StrategyVariant(Base):
    """An immutable forward-evaluation configuration. ``id`` = ``{key}@{version}``; a changed
    configuration gets a new id and a new evaluation period (the old one is closed)."""

    __tablename__ = "strategy_variants"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    key: Mapped[str] = mapped_column(String(8), index=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(80))
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    period_start: Mapped[datetime] = mapped_column(UTCDateTime())
    period_end: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ForwardExperimentTrade(Base):
    """Paper-only record of a forward test (see ``app.forward.experiments``). Separate from the forward
    variants on purpose: it can't affect their results, the paper accounts or any alert."""

    __tablename__ = "forward_experiment_trades"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    signal_key: Mapped[str] = mapped_column(String(64))
    track: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    direction: Mapped[str] = mapped_column(String(8))
    strategy: Mapped[str | None] = mapped_column(String(16), nullable=True)
    strategy_version: Mapped[str] = mapped_column(String(80))
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime())
    signal_ns: Mapped[int] = mapped_column(BigInteger)
    atr_pct: Mapped[float] = mapped_column(Float)
    max_bars: Mapped[int] = mapped_column(Integer)
    stop_loss: Mapped[float] = mapped_column(Float)
    legs: Mapped[list[Any]] = mapped_column(JSON)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    through: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_time: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    exit_time: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    targets_hit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_r: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (Index("ix_forward_experiment_open", "symbol", "timeframe", "status"),
                      Index("ix_forward_experiment_track", "track", "detected_at"))


class PaperTrade(Base):
    """One forward paper-trading record of one strategy variant for one live signal.

    ``decision``: ACCEPTED / REJECTED / MISSED by the strategy. ``status``: OPEN, CLOSED,
    UNFILLED, REJECTED or MISSED. ``portfolio_taken`` says whether the variant's paper
    portfolio (risk controls) took the trade; strategy-level statistics use every filled
    trade, portfolio statistics only taken ones. Only data that had arrived is used."""

    __tablename__ = "paper_trades"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    variant_id: Mapped[str] = mapped_column(ForeignKey("strategy_variants.id"), index=True)
    variant_key: Mapped[str] = mapped_column(String(8), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    direction: Mapped[str] = mapped_column(String(8))
    strategy: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decision: Mapped[str] = mapped_column(String(16))
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    portfolio_taken: Mapped[bool] = mapped_column(Boolean, default=False)
    portfolio_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    candle_open_time: Mapped[datetime] = mapped_column(UTCDateTime())
    candle_close_time: Mapped[datetime] = mapped_column(UTCDateTime())
    reference_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_time: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_fee: Mapped[float] = mapped_column(Float, default=0.0)
    exit_fee: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_cost: Mapped[float] = mapped_column(Float, default=0.0)
    spread_cost: Mapped[float] = mapped_column(Float, default=0.0)
    funding_cost: Mapped[float] = mapped_column(Float, default=0.0)
    exit_time: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    gross_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_flags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ForwardCursor(Base):
    """Last candle the forward tracker processed per symbol/timeframe (detects missed candles)."""

    __tablename__ = "forward_cursors"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    last_candle_open: Mapped[datetime] = mapped_column(UTCDateTime())


class PromotionDecision(Base):
    """Manual review decisions. Recording one never changes the production engine."""

    __tablename__ = "promotion_decisions"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    variant_id: Mapped[str] = mapped_column(String(96), index=True)
    decision: Mapped[str] = mapped_column(String(24))  # APPROVED | REJECTED | DEFERRED
    readiness: Mapped[str] = mapped_column(String(24))
    assessment: Mapped[dict[str, Any]] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class PatternDetection(Base):
    """Confirmed analysis detections (structure events, chart-pattern breakouts, candlestick
    patterns) stored by the scanner with the analysis version that produced them."""

    __tablename__ = "pattern_detections"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # structure | chart_pattern | candlestick
    name: Mapped[str] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16))
    candle_time: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    analysis_version: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class NotificationEvent(Base):
    """One user-facing event of the notification center (distinct from ``notifications``, the
    Telegram delivery log). The database is the source of truth; WebSocket messages only announce."""

    __tablename__ = "notification_events"
    __table_args__ = (UniqueConstraint("recipient", "event_id", name="uq_notification_events_recipient_event"),)

    # Monotonic id, also the cursor clients use to recover missed events.
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    recipient: Mapped[str] = mapped_column(String(64), index=True)
    event_id: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    category: Mapped[str] = mapped_column(String(16), index=True)
    severity: Mapped[str] = mapped_column(String(12), index=True)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    timeframe: Mapped[str | None] = mapped_column(String(8), nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    strategy_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    signal_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    paper_trade_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    incident_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), nullable=True)
    group_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    is_group_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    muted: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_status: Mapped[str] = mapped_column(String(24))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)


class NotificationDelivery(Base):
    """Outbox row: outbound delivery of an event, tracked separately from the event itself.
    Retries change this row only, so they can never create another notification."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (UniqueConstraint("notification_id", "channel", name="uq_notification_deliveries_channel"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    notification_id: Mapped[int] = mapped_column(ForeignKey("notification_events.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)  # PENDING SENDING RETRY SENT FAILED EXPIRED SUPPRESSED
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime())
    not_after: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class NotificationIncident(Base):
    """A grouped operational problem: one notification when it opens (and if it escalates), one
    when it recovers, however many retries happen in between."""

    __tablename__ = "notification_incidents"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(12), index=True)  # OPEN | RESOLVED
    category: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
