"""V1.5-C C4：candidate_pool + signal_score_snapshot 增 money_flow_score 列

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-16

**前向且非破坏**：纯 `ADD COLUMN`（可空），不动任何现有列/索引/约束。
与 0029（low_volatility_score）同形：策略分数列横跨两张表，漏一张则该表新策略分数
永远 NULL 且不报错；`test_strategy_registry.py` 以 `SCORE_COLUMN_MAP` 为契约钉死。
"""
import sqlalchemy as sa

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_TABLES = ("candidate_pool", "signal_score_snapshot")


def upgrade() -> None:
    for t in _TABLES:
        op.add_column(t, sa.Column("money_flow_score", sa.Numeric(5, 2), nullable=True))


def downgrade() -> None:
    for t in _TABLES:
        op.drop_column(t, "money_flow_score")
