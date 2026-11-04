"""pattern detections

Revision ID: 0004_pattern_detections
Revises: 0003_price_alerts
Create Date: 2026-11-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_pattern_detections"
down_revision = "0003_price_alerts"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "pattern_detections",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("candle_time", TS, nullable=False),
        sa.Column("analysis_version", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_pattern_detections_symbol", "pattern_detections", ["symbol"])
    op.create_index("ix_pattern_detections_timeframe", "pattern_detections", ["timeframe"])
    op.create_index("ix_pattern_detections_candle_time", "pattern_detections", ["candle_time"])


def downgrade() -> None:
    op.drop_index("ix_pattern_detections_candle_time", "pattern_detections")
    op.drop_index("ix_pattern_detections_timeframe", "pattern_detections")
    op.drop_index("ix_pattern_detections_symbol", "pattern_detections")
    op.drop_table("pattern_detections")
