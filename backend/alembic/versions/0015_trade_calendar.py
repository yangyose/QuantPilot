"""交易日历入库（trade_calendar）

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-01

依据：数据完整性核验需要权威交易日历参照（此前 TradingCalendar 纯内存，
启动时实时拉 Tushare，离线不可用、核验缺基准）。

目的：
- 持久化 A 股交易日历，作为「daily_quote / candidate_pool / index_history 是否
  缺交易日」的权威差集基准（scripts/audit_data_integrity.py 消费）。
- 启动 DB 优先：从本表加载 TradingCalendar，缺范围时自愈拉 Tushare 落库。

表设计（全历法日 + is_open）：
- 每个自然日一行（含闭市日 is_open=false），忠实 Tushare trade_cal。
- is_trade_date 可对范围内任意日期权威作答开/闭市；差集核验取 is_open=true。
- 复合主键 (exchange, cal_date)；exchange 默认 'SSE'（A 股沪深同历）。
- 5y × 365 ≈ 1825 行/交易所，体量可忽略。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trade_calendar",
        sa.Column("exchange", sa.String(length=10), nullable=False, server_default="SSE"),
        sa.Column("cal_date", sa.Date(), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("exchange", "cal_date", name="pk_trade_calendar"),
    )
    # 范围查询 + is_open 过滤走索引（差集核验 / from_repo 主路径）
    op.create_index(
        "idx_trade_calendar_open",
        "trade_calendar",
        ["exchange", "is_open", "cal_date"],
    )


def downgrade() -> None:
    op.drop_index("idx_trade_calendar_open", table_name="trade_calendar")
    op.drop_table("trade_calendar")
