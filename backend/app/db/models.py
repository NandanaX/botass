"""ORM models. Portable between PostgreSQL (production) and SQLite (local/tests)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text
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
