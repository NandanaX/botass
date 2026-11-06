"""strategy d decisions: forward shadow records of the 15m strategy, separate from the forward variants

Revision ID: 0010_strategy_d
Revises: 0009_forward_experiments
Create Date: 2026-11-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_strategy_d"
down_revision = "0009_forward_experiments"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "strategy_d_decisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("strategy_version", sa.String(80), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("entry_type", sa.String(16), nullable=False),
        sa.Column("signal_ns", sa.BigInteger(), nullable=False),
        sa.Column("decision_at", TS, nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("taken", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("legs", sa.JSON(), nullable=True),
        sa.Column("atr_pct", sa.Float(), nullable=False),
        sa.Column("max_bars", sa.Integer(), nullable=False),
        sa.Column("risk_amount", sa.Float(), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("notional", sa.Float(), nullable=True),
        sa.Column("fill_price", sa.Float(), nullable=True),
        sa.Column("fill_time", TS, nullable=True),
        sa.Column("exit_time", TS, nullable=True),
        sa.Column("exit_reason", sa.String(24), nullable=True),
        sa.Column("targets_hit", sa.Integer(), nullable=True),
        sa.Column("gross_r", sa.Float(), nullable=True),
        sa.Column("net_r", sa.Float(), nullable=True),
    )
    op.create_index("ix_strategy_d_open", "strategy_d_decisions", ["symbol", "status"])
    op.create_index("ix_strategy_d_signal", "strategy_d_decisions", ["strategy_version", "symbol", "signal_ns"])


def downgrade() -> None:
    op.drop_index("ix_strategy_d_signal", table_name="strategy_d_decisions")
    op.drop_index("ix_strategy_d_open", table_name="strategy_d_decisions")
    op.drop_table("strategy_d_decisions")
