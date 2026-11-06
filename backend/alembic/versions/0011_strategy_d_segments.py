"""strategy d evaluation segments: manual drawdown resets start a new segment; existing decisions stay in segment 1

Revision ID: 0011_strategy_d_segments
Revises: 0010_strategy_d
Create Date: 2026-11-06
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_strategy_d_segments"
down_revision = "0010_strategy_d"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("strategy_d_decisions") as batch:
        batch.add_column(sa.Column("evaluation_segment", sa.Integer(), nullable=False, server_default="1"))
    op.create_table(
        "strategy_d_segments",
        sa.Column("evaluation_version", sa.String(100), primary_key=True),
        sa.Column("strategy_version", sa.String(80), nullable=False),
        sa.Column("segment", sa.Integer(), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("start_equity", sa.Float(), nullable=False),
        sa.Column("reset_reason", sa.Text(), nullable=True),
        sa.Column("reset_by", sa.String(80), nullable=True),
        sa.Column("halted_at", TS, nullable=True),
        sa.Column("halted_equity", sa.Float(), nullable=True),
        sa.Column("halted_peak", sa.Float(), nullable=True),
        sa.Column("halted_drawdown_pct", sa.Float(), nullable=True),
        sa.Column("ended_at", TS, nullable=True),
        sa.Column("end_equity", sa.Float(), nullable=True),
    )
    op.create_index("ix_strategy_d_segments_strategy_version", "strategy_d_segments", ["strategy_version"])


def downgrade() -> None:
    op.drop_index("ix_strategy_d_segments_strategy_version", table_name="strategy_d_segments")
    op.drop_table("strategy_d_segments")
    with op.batch_alter_table("strategy_d_decisions") as batch:
        batch.drop_column("evaluation_segment")
