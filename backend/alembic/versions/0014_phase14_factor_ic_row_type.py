"""Phase 14 §14-6 factor_ic_window_state.row_type（共表拆分方案 A）

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-26

依据：docs/design/phases/phase14_account_integrity.md §8.2（方案 A 保守）。

目的：
- 当前 factor_ic_window_state 表 daily 行（仅 ic_value/sample_size）+ aggregate
  行（icir/CI/t_stat/half_life/...）共表 + 共 UNIQUE 约束
  (strategy, factor, state, trade_date)，靠 `ic_value/icir IS NOT NULL` 区分
- 5y × 250 trade_date × 4 strategy × 4 factor × 3 state ≈ 1.2M 行后表膨胀
- 索引无法加速 `WHERE icir IS NOT NULL` / `WHERE ic_value IS NOT NULL` 谓词

方案 A（保守，向后兼容）：
- ADD COLUMN row_type VARCHAR(8) NOT NULL DEFAULT 'daily'
- 回填存量：icir IS NOT NULL → 'aggregate'，否则保留 'daily'
- 加 partial unique index 仅在 row_type='aggregate' 上：
  - 让 `WHERE row_type='aggregate'` 查询走 index-only scan
  - 与既有 UNIQUE 约束并存（在 aggregate 上仍只能 1 行）；UNIQUE 提供"4-tuple
    唯一"语义，partial unique 提供"快速 aggregate 行查找 + 索引覆盖"

V1.5+ 方案 B（拆 factor_ic_daily 窄表 + factor_ic_window_state 聚合表）留 DBA 视图。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 加列（DEFAULT 'daily'：新建表 + 既有行回填到 'daily'）
    # length=16：覆盖 'daily' (5) / 'aggregate' (9) + 兼容未来扩展（如 'monthly'）。
    op.add_column(
        "factor_ic_window_state",
        sa.Column(
            "row_type",
            sa.String(length=16),
            nullable=False,
            server_default="daily",
        ),
    )

    # 2. 回填存量：icir IS NOT NULL → 'aggregate'（其余保持 'daily' 默认）
    op.execute(
        "UPDATE factor_ic_window_state SET row_type = 'aggregate' "
        "WHERE icir IS NOT NULL"
    )

    # 3. partial unique index on aggregate 行
    # 与既有 UNIQUE (strategy, factor, state, trade_date) 并存：UNIQUE 保留全表
    # 唯一性约束（覆盖 daily + aggregate 任一类型 4-tuple 唯一）；partial 仅供
    # `WHERE row_type='aggregate'` 查询路径走 index-only scan + 文档化"aggregate
    # 唯一性"意图。冗余但向后兼容，方案 A 设计取舍。
    op.create_index(
        "uq_factor_ic_window_state_aggregate",
        "factor_ic_window_state",
        ["strategy", "factor", "state", "trade_date"],
        unique=True,
        postgresql_where=sa.text("row_type = 'aggregate'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_factor_ic_window_state_aggregate",
        table_name="factor_ic_window_state",
    )
    op.drop_column("factor_ic_window_state", "row_type")
