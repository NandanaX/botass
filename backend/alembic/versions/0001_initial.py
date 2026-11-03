"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "candles",
        sa.Column("exchange", sa.String(16), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("timeframe", sa.String(8), primary_key=True),
        sa.Column("open_time", TS, primary_key=True),
        sa.Column("open", sa.Float, nullable=False),
        sa.Column("high", sa.Float, nullable=False),
        sa.Column("low", sa.Float, nullable=False),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("volume", sa.Float, nullable=False),
        sa.Column("turnover", sa.Float, nullable=False),
    )
    op.create_table(
        "funding_rates",
        sa.Column("exchange", sa.String(16), primary_key=True),
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("funding_time", TS, primary_key=True),
        sa.Column("rate", sa.Float, nullable=False),
    )
    op.create_table(
        "signals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("exchange", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("strategy_name", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("candle_open_time", TS, nullable=False),
        sa.Column("candle_close_time", TS, nullable=False),
        sa.Column("entry_reference", sa.Float, nullable=False),
        sa.Column("stop_loss", sa.Float, nullable=False),
        sa.Column("take_profit", sa.Float, nullable=False),
        sa.Column("risk_reward", sa.Float, nullable=False),
        sa.Column("atr", sa.Float, nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("score_breakdown", sa.JSON, nullable=False),
        sa.Column("conditions", sa.JSON, nullable=False),
        sa.Column("indicators", sa.JSON, nullable=False),
        sa.Column("explanation", sa.Text, nullable=False),
        sa.Column("mtf_confirmed", sa.Boolean, nullable=True),
        sa.Column("mtf_details", sa.JSON, nullable=False),
        sa.Column("notification_status", sa.String(16), nullable=False),
        sa.Column("evaluation_status", sa.String(24), nullable=False),
        sa.Column("evaluation", sa.JSON, nullable=True),
        sa.Column("evaluated_at", TS, nullable=True),
    )
    op.create_index("ix_signals_symbol", "signals", ["symbol"])
    op.create_index("ix_signals_timeframe", "signals", ["timeframe"])
    op.create_index("ix_signals_created_at", "signals", ["created_at"])
    op.create_index("ix_signals_evaluation_status", "signals", ["evaluation_status"])
    op.create_index("ix_signals_symbol_tf_time", "signals", ["symbol", "timeframe", "candle_open_time"])
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.String(64), sa.ForeignKey("signals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("sent_at", TS, nullable=True),
    )
    op.create_index("ix_notifications_signal_id", "notifications", ["signal_id"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("config", sa.JSON, nullable=False),
        sa.Column("metrics", sa.JSON, nullable=True),
        sa.Column("trades", sa.JSON, nullable=True),
        sa.Column("equity_curve", sa.JSON, nullable=True),
        sa.Column("assumptions", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index("ix_backtest_runs_created_at", "backtest_runs", ["created_at"])
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.JSON, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_table(
        "analysis_snapshots",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("timeframe", sa.String(8), primary_key=True),
        sa.Column("data", sa.JSON, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )


def downgrade() -> None:
    for table in ("analysis_snapshots", "app_settings", "backtest_runs", "notifications", "signals",
                  "funding_rates", "candles"):
        op.drop_table(table)
