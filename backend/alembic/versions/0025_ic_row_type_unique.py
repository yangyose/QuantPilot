"""V1.5-C C0-7：factor_ic_window_state 唯一键补上 row_type

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-18

**问题**：Phase 11（alembic 0009）建的全表唯一约束
`uq_factor_ic_window_state_skft (strategy, factor, state, trade_date)` 不含
`row_type`。Phase 14（alembic 0014）新增 `row_type` 列并加了 partial unique
`... WHERE row_type='aggregate'`，注释明确写着与全表约束「并存 …… 冗余但向后
兼容，方案 A 设计取舍」——即**有意保留**跨类型的 4 元组唯一性。

该取舍是错的：它让 daily 行与 aggregate 行在同一 4 元组上**互斥**。月末那一天，
当日市场状态若恰等于某个已有 aggregate 行的 state，`upsert_ic_daily` 的
`ON CONFLICT (4 列)` 就会命中 aggregate 行，把日级 `ic_value` / `sample_size`
写进去，两行合并为一行且 `row_type` 仍为 'aggregate'。

生产实测（2026-08-18）：**156 行 / 39 个月末**被污染（2022-05 ~ 2026-03，源自
Phase 14 §14-9 五年回填）。后果：
1. 该日日级 IC 对 ICIR 窗口不可见（`get_ic_daily_window` 按 row_type='daily'
   过滤——§14-9 P2-2 只加固了读路径，没治根），每月静默少一个观测；
2. aggregate 的 `sample_size`（窗口观测数）被日级横截面股票数覆盖，
   `check_factor_offline_rules` R4（sample_size < 60 连续 3 月）在这些行上失效。

**本迁移**：唯一键改为 5 列 `(strategy, factor, state, trade_date, row_type)`，
三类行（daily / aggregate / monthly_quality）自此可在同一 4 元组共存。原
aggregate partial unique 随之冗余，一并删除。

配套（不在本迁移内）：
- `data/factor_ic_repository.py` 三处 upsert 的 `index_elements` 补 `row_type`
- `scripts/repair_ic_row_type_collision.py` 拆分存量被污染行（日级
  `ic_value`/`sample_size` 就存在被污染行里，精确可还原）；aggregate 侧统计列
  交由 `scripts/backfill_icir_rebalance.py --force` 重算（精确，无需估算）

**顺序**：先跑本迁移，再跑拆分脚本，最后重算 aggregate。反序会让拆分脚本插入
daily 行时撞上旧约束。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CONSTRAINT = "uq_factor_ic_window_state_skft"
_OLD_AGG_INDEX = "uq_factor_ic_window_state_aggregate"
_NEW_CONSTRAINT = "uq_factor_ic_window_state_skftr"


def upgrade() -> None:
    # 1. 先建新键再删旧键，任何时刻都有唯一性保护（存量已合并成单行，不会有重复）
    op.create_unique_constraint(
        _NEW_CONSTRAINT,
        "factor_ic_window_state",
        ["strategy", "factor", "state", "trade_date", "row_type"],
    )
    op.drop_constraint(
        _OLD_CONSTRAINT, "factor_ic_window_state", type_="unique",
    )
    # 2. aggregate partial unique 已被 5 列唯一键覆盖，冗余
    op.drop_index(_OLD_AGG_INDEX, table_name="factor_ic_window_state")


def downgrade() -> None:
    # 回退前必须确认不存在「同 4 元组多 row_type」的行——那正是本迁移要允许的
    # 状态，旧的 4 列唯一约束建不起来。这里显式检查并给出可操作的报错，
    # 避免退化成 psycopg 的 duplicate key 裸报。
    conn = op.get_bind()
    dup = conn.execute(sa.text(
        "SELECT count(*) FROM ("
        "  SELECT 1 FROM factor_ic_window_state"
        "  GROUP BY strategy, factor, state, trade_date"
        "  HAVING count(*) > 1"
        ") d"
    )).scalar_one()
    if dup:
        raise RuntimeError(
            f"downgrade 0025 阻断：有 {dup} 组 (strategy, factor, state, trade_date) "
            "存在多个 row_type 行，旧的 4 列唯一约束无法重建。"
            "回退前需先决定这些行的去留（合并会重新引入本迁移修复的数据丢失）。"
        )

    op.create_index(
        _OLD_AGG_INDEX,
        "factor_ic_window_state",
        ["strategy", "factor", "state", "trade_date"],
        unique=True,
        postgresql_where=sa.text("row_type = 'aggregate'"),
    )
    op.create_unique_constraint(
        _OLD_CONSTRAINT,
        "factor_ic_window_state",
        ["strategy", "factor", "state", "trade_date"],
    )
    op.drop_constraint(
        _NEW_CONSTRAINT, "factor_ic_window_state", type_="unique",
    )
