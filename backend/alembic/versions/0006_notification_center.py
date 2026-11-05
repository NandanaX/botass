"""notification center: events, delivery outbox, incidents

Revision ID: 0006_notification_center
Revises: 0005_forward_validation
Create Date: 2026-11-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_notification_center"
down_revision = "0005_forward_validation"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "notification_events",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("recipient", sa.String(64), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(12), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("timeframe", sa.String(8), nullable=True),
        sa.Column("strategy_id", sa.String(8), nullable=True),
        sa.Column("strategy_version", sa.String(80), nullable=True),
        sa.Column("signal_id", sa.String(64), nullable=True),
        sa.Column("paper_trade_id", sa.String(64), nullable=True),
        sa.Column("incident_id", BIGID, nullable=True),
        sa.Column("group_key", sa.String(200), nullable=True),
        sa.Column("is_group_summary", sa.Boolean(), nullable=False),
        sa.Column("muted", sa.Boolean(), nullable=False),
        sa.Column("delivery_status", sa.String(24), nullable=False),
        sa.Column("occurred_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("read_at", TS, nullable=True),
        sa.UniqueConstraint("recipient", "event_id", name="uq_notification_events_recipient_event"),
    )
    for col in ("recipient", "event_type", "category", "severity", "symbol", "strategy_id", "signal_id",
                "paper_trade_id", "group_key", "created_at", "read_at"):
        op.create_index(f"ix_notification_events_{col}", "notification_events", [col])
    op.create_table(
        "notification_deliveries",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("notification_id", BIGID, sa.ForeignKey("notification_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", TS, nullable=False),
        sa.Column("not_after", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("sent_at", TS, nullable=True),
        sa.UniqueConstraint("notification_id", "channel", name="uq_notification_deliveries_channel"),
    )
    op.create_index("ix_notification_deliveries_notification_id", "notification_deliveries", ["notification_id"])
    op.create_index("ix_notification_deliveries_status", "notification_deliveries", ["status"])
    op.create_table(
        "notification_incidents",
        sa.Column("id", BIGID, primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(12), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("resolved_at", TS, nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_notification_incidents_key", "notification_incidents", ["key"])
    op.create_index("ix_notification_incidents_status", "notification_incidents", ["status"])
    op.create_index("ix_notification_incidents_started_at", "notification_incidents", ["started_at"])


def downgrade() -> None:
    op.drop_table("notification_incidents")
    op.drop_table("notification_deliveries")
    op.drop_table("notification_events")
