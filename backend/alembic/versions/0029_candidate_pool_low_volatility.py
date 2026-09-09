"""V1.5-C C3：candidate_pool + signal_score_snapshot 增 low_volatility_score 列

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-08

**前向且非破坏**：纯 `ADD COLUMN`（可空），不动任何现有列/索引/约束。

C3 低波动策略以**影子模式**上线（权重 0，进 composite 结构但不影响打分），
本列用于记录其分数以便观察与 ICIR 累积。既有行为 NULL，前端与 API 按可空处理。

⚠️ 加策略必须同时改三处，否则分数静默丢失：本迁移 / `CandidatePool` 模型 /
`PoolEntry`。`tests/unit/test_strategy_registry.py::TestScoreColumnMapIsACheckedContract`
以 `SCORE_COLUMN_MAP` 为契约把三者钉在一起——那个 map 此前是零消费者的装饰品。

DDL 权威源：`docs/design/phases/v1_5_c_strategy_expansion.md` §8.3 陷阱 3。
"""
import sqlalchemy as sa

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


# ⚠️ 策略分数列**横跨两张表**——设计 §8.3 陷阱 3 只数到 `candidate_pool`，
# 而 `signal_score_snapshot`（信号数据血缘）有同样的四列。漏一张表则该表的
# 新策略分数永远为 NULL，且**不报错**。
_TABLES = ("candidate_pool", "signal_score_snapshot")


def upgrade() -> None:
    for t in _TABLES:
        op.add_column(
            t, sa.Column("low_volatility_score", sa.Numeric(5, 2), nullable=True)
        )


def downgrade() -> None:
    for t in reversed(_TABLES):
        op.drop_column(t, "low_volatility_score")
