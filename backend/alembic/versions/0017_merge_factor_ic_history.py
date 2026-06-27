"""factor_ic_history 归并进 factor_ic_window_state（row_type='monthly_quality'）+ DROP 旧表

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-27

Phase 15 §15-7：消除「月度因子质量」与「ICIR 校准」双表。盘点发现两表是两个
不同功能（旧表=月度 strategy-composite 因子质量 + alert_status，state 无关；
新表=日级/聚合 × market state ICIR），旧表数据无法从新表派生。决策（用户拍板
2026-06-27）：把月度因子质量行归并进单表 factor_ic_window_state，用
row_type='monthly_quality' 区分，DROP 旧表。

字段映射（monthly_quality 行）：
- calc_month        → trade_date
- strategy_name     → strategy
- factor_name       → factor
- ic_value          → ic_value           （Numeric(8,6)→(8,4)，IC<1 精度足够）
- ic_mean_3m        → ic_mean_state       （复用列；语义=3 月滚动均值）
- ic_std_3m         → ic_std_state
- ir_3m             → icir                （复用列；IR=mean/std 与 ICIR 同形）
- half_life_days    → half_life           （Numeric(6,1)→Integer，ROUND）
- alert_status      → alert_status        （本迁移新增列）
- return_window(20) → 不存，mapper 常量 20
- （state 无关）     → state='ALL' 哨兵    （monthly_quality 行专用，与 daily/aggregate 读路径隔离）
- （无 sample_size） → sample_size=0       【降级说明】旧月度路径不记 sample_size，
                                            归并置 0 占位；monthly_quality 行不经
                                            sample_size 消费路径（R1/R2/R4 只读 aggregate 行），
                                            /factor-quality 响应也不含该字段。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 新增 alert_status 列（monthly_quality 行专用；daily/aggregate 行留 NULL）
    op.add_column(
        "factor_ic_window_state",
        sa.Column("alert_status", sa.String(20), nullable=True),
    )

    # 2. 数据迁移：factor_ic_history → factor_ic_window_state（row_type='monthly_quality'）
    #    ON CONFLICT DO NOTHING 防重复执行（4-tuple 已存在则跳过）
    op.execute(
        """
        INSERT INTO factor_ic_window_state (
            strategy, factor, state, trade_date,
            ic_value, ic_mean_state, ic_std_state, icir,
            sample_size, half_life, row_type, alert_status
        )
        SELECT
            strategy_name, factor_name, 'ALL', calc_month,
            ic_value, ic_mean_3m, ic_std_3m, ir_3m,
            0, ROUND(half_life_days)::int, 'monthly_quality', alert_status
        FROM factor_ic_history
        ON CONFLICT (strategy, factor, state, trade_date) DO NOTHING
        """
    )

    # 3. DROP 旧表（索引随表删除）
    op.drop_index("idx_ic_history_strategy", table_name="factor_ic_history")
    op.drop_table("factor_ic_history")


def downgrade() -> None:
    # 1. 重建旧表（与 0001 一致）
    op.create_table(
        "factor_ic_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("calc_month", sa.Date, nullable=False),
        sa.Column("strategy_name", sa.String(30), nullable=False),
        sa.Column("factor_name", sa.String(50), nullable=False),
        sa.Column("ic_value", sa.Numeric(8, 6)),
        sa.Column("ic_mean_3m", sa.Numeric(8, 6)),
        sa.Column("ic_std_3m", sa.Numeric(8, 6)),
        sa.Column("ir_3m", sa.Numeric(8, 6)),
        sa.Column("half_life_days", sa.Numeric(6, 1)),
        sa.Column("return_window", sa.Integer, server_default="20"),
        sa.Column("alert_status", sa.String(20)),
        sa.UniqueConstraint(
            "calc_month", "strategy_name", "factor_name", "return_window",
            name="uq_ic_history_month_strategy_factor_window",
        ),
    )
    op.create_index(
        "idx_ic_history_strategy", "factor_ic_history", ["strategy_name", "calc_month"]
    )

    # 2. 回拷 monthly_quality 行 → 旧表（反向映射，return_window 恒 20）
    op.execute(
        """
        INSERT INTO factor_ic_history (
            calc_month, strategy_name, factor_name,
            ic_value, ic_mean_3m, ic_std_3m, ir_3m,
            half_life_days, return_window, alert_status
        )
        SELECT
            trade_date, strategy, factor,
            ic_value, ic_mean_state, ic_std_state, icir,
            half_life, 20, alert_status
        FROM factor_ic_window_state
        WHERE row_type = 'monthly_quality'
        ON CONFLICT (calc_month, strategy_name, factor_name, return_window) DO NOTHING
        """
    )

    # 3. 删除归并行 + DROP 新增列
    op.execute("DELETE FROM factor_ic_window_state WHERE row_type = 'monthly_quality'")
    op.drop_column("factor_ic_window_state", "alert_status")
