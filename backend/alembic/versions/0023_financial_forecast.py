"""V1.5-A A5（SDD-EXT-03）：financial_forecast（业绩预告/快报 PIT 数据层）

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-27

V1.5-A A5（设计 docs/design/phases/v1_5_a_backtest_monitoring.md §6）：业绩快报到
正式年报 1-2 月信息真空期，用 est_net_profit 派生前瞻 ROE 修正估值。新表前向建
（非破坏）。生产 upgrade + 5y 回填需 C-1 单独确认 + pg_dump 前置。

唯一约束 (ts_code, report_period, source_type) 支持 forecast/express 各一行幂等
upsert；索引 (ts_code, pre_announce_date) 供 PIT 查询。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financial_forecast",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts_code", sa.String(length=10), nullable=False),
        sa.Column("report_period", sa.Date(), nullable=False),
        sa.Column("pre_announce_date", sa.Date(), nullable=False),
        sa.Column("est_net_profit", sa.Numeric(18, 2), nullable=True),
        sa.Column("est_net_profit_yoy", sa.Numeric(10, 4), nullable=True),
        sa.Column("data_priority", sa.SmallInteger(), nullable=False),
        sa.Column("source_type", sa.String(length=10), nullable=False),
        sa.UniqueConstraint(
            "ts_code", "report_period", "source_type",
            name="uq_forecast_code_period_source",
        ),
    )
    op.create_index(
        "idx_forecast_code_announce",
        "financial_forecast",
        ["ts_code", "pre_announce_date"],
    )


def downgrade() -> None:
    op.drop_index("idx_forecast_code_announce", table_name="financial_forecast")
    op.drop_table("financial_forecast")
