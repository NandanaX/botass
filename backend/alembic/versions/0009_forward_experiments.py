"""forward experiment trades: paper-only tests of the Phase 9 candidates, separate from the forward variants

Revision ID: 0009_forward_experiments
Revises: 0008_drop_shadow_trades
Create Date: 2026-11-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_forward_experiments"
down_revision = "0008_drop_shadow_trades"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "forward_experiment_trades",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("signal_key", sa.String(64), nullable=False),
        sa.Column("track", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("strategy", sa.String(16), nullable=True),
        sa.Column("strategy_version", sa.String(80), nullable=False),
        sa.Column("detected_at", TS, nullable=False),
        sa.Column("signal_ns", sa.BigInteger(), nullable=False),
        sa.Column("atr_pct", sa.Float(), nullable=False),
        sa.Column("max_bars", sa.Integer(), nullable=False),
        sa.Column("stop_loss", sa.Float(), nullable=False),
        sa.Column("legs", sa.JSON(), nullable=False),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("through", sa.Float(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("fill_price", sa.Float(), nullable=True),
        sa.Column("fill_time", TS, nullable=True),
        sa.Column("exit_time", TS, nullable=True),
        sa.Column("exit_reason", sa.String(24), nullable=True),
        sa.Column("targets_hit", sa.Integer(), nullable=True),
        sa.Column("gross_r", sa.Float(), nullable=True),
        sa.Column("net_r", sa.Float(), nullable=True),
    )
    op.create_index("ix_forward_experiment_open", "forward_experiment_trades", ["symbol", "timeframe", "status"])
    op.create_index("ix_forward_experiment_track", "forward_experiment_trades", ["track", "detected_at"])


def downgrade() -> None:
    op.drop_index("ix_forward_experiment_track", table_name="forward_experiment_trades")
    op.drop_index("ix_forward_experiment_open", table_name="forward_experiment_trades")
    op.drop_table("forward_experiment_trades")
