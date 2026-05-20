"""Phase 12 attribution_history table（多因子回归归因结果持久化）

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-20

依据：docs/design/phases/phase12_factor_lineage.md v1.1 §3.2.3 + §5.2。
AttributionService.run_monthly 月末批写入，每月 4 行（4 策略归因）。
UNIQUE (calc_date, factor) 保证 upsert 幂等。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attribution_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("calc_date", sa.Date, nullable=False),
        sa.Column("factor", sa.String(32), nullable=False),
        sa.Column("beta", sa.Numeric(10, 6), nullable=False),
        sa.Column("t_stat", sa.Numeric(8, 4), nullable=True),
        sa.Column("residual_std", sa.Numeric(10, 6), nullable=True),
        sa.Column("r_squared", sa.Numeric(6, 4), nullable=True),
        sa.Column("sample_size", sa.Integer, nullable=False),
        sa.Column(
            "window_days", sa.Integer, nullable=False, server_default="20",
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"), nullable=False,
        ),
        sa.UniqueConstraint("calc_date", "factor", name="uq_attribution_date_factor"),
    )
    op.create_index(
        "idx_attribution_date_desc",
        "attribution_history",
        [sa.text("calc_date DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_attribution_date_desc", table_name="attribution_history")
    op.drop_table("attribution_history")
