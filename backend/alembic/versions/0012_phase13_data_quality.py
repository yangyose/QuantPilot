"""Phase 13 data_quality_metric table (S2-GAP-01 DataValidator 错误持久化)

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-21

依据：docs/design/phases/phase13_production_observability.md §3.4 + §5.1。
DataService.ingest_daily 调 DataValidator 后写入；/health/data 端点近 30 日聚合查询。
UNIQUE (metric_date, data_type, metric_key) 保证 upsert 幂等（同日重跑 ingest）。
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_quality_metric",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("metric_date", sa.Date, nullable=False),
        sa.Column("data_type", sa.String(32), nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("metric_value", sa.Numeric(20, 6), nullable=False),
        sa.Column("details", JSONB, nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"), nullable=False,
        ),
        sa.UniqueConstraint(
            "metric_date", "data_type", "metric_key",
            name="uq_data_quality_date_type_key",
        ),
    )
    op.create_index(
        "idx_data_quality_date_desc",
        "data_quality_metric",
        [sa.text("metric_date DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_data_quality_date_desc", table_name="data_quality_metric")
    op.drop_table("data_quality_metric")
