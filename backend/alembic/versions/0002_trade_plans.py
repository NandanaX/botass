"""phase 2: trade plans on signals

Revision ID: 0002_trade_plans
Revises: 0001_initial
Create Date: 2026-11-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_trade_plans"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("signals") as batch:
        batch.add_column(sa.Column("strategy_type", sa.String(16), nullable=False, server_default="breakout"))
        batch.add_column(sa.Column("setup_status", sa.String(16), nullable=False, server_default="QUALIFIED"))
        batch.add_column(sa.Column("plan", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("signals") as batch:
        batch.drop_column("plan")
        batch.drop_column("setup_status")
        batch.drop_column("strategy_type")
