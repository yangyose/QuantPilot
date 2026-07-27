"""V1.5-A A1（S6-GAP-02）：backtest_daily_position（回测每日持仓明细流式持久化）

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-27

V1.5-A A1（设计 docs/design/phases/v1_5_a_backtest_monitoring.md §2）：兑现 Phase 8
§2.1 推迟的 daily_positions 持久化。BacktestEngine 流式 sink 逐日落此表（不在内存
累积 O(N×T)），结果页按 task_id 分页查每日持仓。

新表前向建（非破坏）。与 backtest_task/backtest_result 同族，本地算力中心 + 生产
回流两侧 upgrade head。FK→backtest_task ondelete CASCADE；唯一约束
(task_id, trade_date, ts_code) 支持幂等 upsert。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtest_daily_position",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("backtest_task.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(length=10), nullable=False),
        sa.Column("shares", sa.Integer(), nullable=False),
        sa.Column("cost_price", sa.Numeric(10, 3), nullable=True),
        sa.Column("market_value", sa.Numeric(15, 2), nullable=True),
        sa.UniqueConstraint(
            "task_id", "trade_date", "ts_code", name="uq_bt_daily_pos",
        ),
    )
    op.create_index(
        "idx_bt_daily_pos_task_date",
        "backtest_daily_position",
        ["task_id", "trade_date"],
    )


def downgrade() -> None:
    op.drop_index("idx_bt_daily_pos_task_date", table_name="backtest_daily_position")
    op.drop_table("backtest_daily_position")
