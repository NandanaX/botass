"""forward validation: strategy variants, paper trades, cursors, promotion decisions

Revision ID: 0005_forward_validation
Revises: 0004_pattern_detections
Create Date: 2026-11-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_forward_validation"
down_revision = "0004_pattern_detections"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "strategy_variants",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("key", sa.String(8), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("period_start", TS, nullable=False),
        sa.Column("period_end", TS, nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_strategy_variants_key", "strategy_variants", ["key"])
    op.create_table(
        "paper_trades",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("variant_id", sa.String(96), sa.ForeignKey("strategy_variants.id"), nullable=False),
        sa.Column("variant_key", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("strategy", sa.String(16), nullable=True),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("portfolio_taken", sa.Boolean(), nullable=False),
        sa.Column("portfolio_reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("detected_at", TS, nullable=False),
        sa.Column("candle_open_time", TS, nullable=False),
        sa.Column("candle_close_time", TS, nullable=False),
        sa.Column("reference_entry", sa.Float(), nullable=True),
        sa.Column("fill_price", sa.Float(), nullable=True),
        sa.Column("fill_time", TS, nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("take_profit", sa.Float(), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("risk_amount", sa.Float(), nullable=True),
        sa.Column("entry_fee", sa.Float(), nullable=False),
        sa.Column("exit_fee", sa.Float(), nullable=False),
        sa.Column("slippage_cost", sa.Float(), nullable=False),
        sa.Column("spread_cost", sa.Float(), nullable=False),
        sa.Column("funding_cost", sa.Float(), nullable=False),
        sa.Column("exit_time", TS, nullable=True),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("exit_reason", sa.String(24), nullable=True),
        sa.Column("gross_pnl", sa.Float(), nullable=True),
        sa.Column("net_pnl", sa.Float(), nullable=True),
        sa.Column("net_r", sa.Float(), nullable=True),
        sa.Column("cost_flags", sa.JSON(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    for col in ("variant_id", "variant_key", "symbol", "status", "detected_at"):
        op.create_index(f"ix_paper_trades_{col}", "paper_trades", [col])
    op.create_table(
        "forward_cursors",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("timeframe", sa.String(8), primary_key=True),
        sa.Column("last_candle_open", TS, nullable=False),
    )
    op.create_table(
        "promotion_decisions",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("variant_id", sa.String(96), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("readiness", sa.String(24), nullable=False),
        sa.Column("assessment", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_promotion_decisions_variant_id", "promotion_decisions", ["variant_id"])


def downgrade() -> None:
    op.drop_index("ix_promotion_decisions_variant_id", "promotion_decisions")
    op.drop_table("promotion_decisions")
    op.drop_table("forward_cursors")
    for col in ("variant_id", "variant_key", "symbol", "status", "detected_at"):
        op.drop_index(f"ix_paper_trades_{col}", "paper_trades")
    op.drop_table("paper_trades")
    op.drop_index("ix_strategy_variants_key", "strategy_variants")
    op.drop_table("strategy_variants")
