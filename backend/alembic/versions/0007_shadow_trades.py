"""shadow trades: paper-only test of a research lead, separate from the forward variants

Revision ID: 0007_shadow_trades
Revises: 0006_notification_center
Create Date: 2026-11-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_shadow_trades"
down_revision = "0006_notification_center"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "shadow_trades",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("lead", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("strategy", sa.String(16), nullable=True),
        sa.Column("strategy_version", sa.String(80), nullable=False),
        sa.Column("detected_at", TS, nullable=False),
        sa.Column("candle_close_time", TS, nullable=False),
        sa.Column("stop_loss", sa.Float(), nullable=False),
        sa.Column("take_profit", sa.Float(), nullable=False),
        sa.Column("room_r", sa.Float(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("fill_price", sa.Float(), nullable=True),
        sa.Column("fill_time", TS, nullable=True),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("exit_time", TS, nullable=True),
        sa.Column("exit_reason", sa.String(24), nullable=True),
        sa.Column("net_r", sa.Float(), nullable=True),
    )
    op.create_index("ix_shadow_trades_open", "shadow_trades", ["symbol", "timeframe", "status"])
    op.create_index("ix_shadow_trades_lead", "shadow_trades", ["lead", "detected_at"])


def downgrade() -> None:
    op.drop_index("ix_shadow_trades_lead", table_name="shadow_trades")
    op.drop_index("ix_shadow_trades_open", table_name="shadow_trades")
    op.drop_table("shadow_trades")
