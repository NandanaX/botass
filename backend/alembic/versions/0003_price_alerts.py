"""price alerts

Revision ID: 0003_price_alerts
Revises: 0002_trade_plans
Create Date: 2026-11-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_price_alerts"
down_revision = "0002_trade_plans"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "price_alerts",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("condition", sa.String(8), nullable=False),
        sa.Column("price", sa.Float, nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("price_at_creation", sa.Float, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("triggered_at", TS, nullable=True),
        sa.Column("trigger_price", sa.Float, nullable=True),
        sa.Column("notification_status", sa.String(16), nullable=True),
    )
    op.create_index("ix_price_alerts_symbol", "price_alerts", ["symbol"])
    op.create_index("ix_price_alerts_status", "price_alerts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_price_alerts_status", "price_alerts")
    op.drop_index("ix_price_alerts_symbol", "price_alerts")
    op.drop_table("price_alerts")
