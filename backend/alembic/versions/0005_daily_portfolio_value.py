"""Phase 7: add daily_portfolio_value table

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-13

新建 daily_portfolio_value 表，记录每日账户净值曲线快照（Phase 8 PerformanceService 依赖）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_portfolio_value",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Integer, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("total_value", sa.Numeric(15, 2), nullable=False),
        sa.Column("cash", sa.Numeric(15, 2), nullable=False),
        sa.Column("position_value", sa.Numeric(15, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("account_id", "trade_date", name="uq_dpv_account_date"),
    )
    op.create_index(
        "ix_dpv_account_date",
        "daily_portfolio_value",
        ["account_id", sa.text("trade_date DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_dpv_account_date", table_name="daily_portfolio_value")
    op.drop_table("daily_portfolio_value")
