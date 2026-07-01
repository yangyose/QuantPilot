"""V1.5-G G-3：report.account_id + 回填首账户（报告账户层隔离）

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-01

V1.5-G G-3（设计 docs/design/phases/v1_5_g_multiuser.md §5）：报告属账户层隔离数据
（§2 边界）。V1.0 单账户下 report 无归属列；多用户后须按 account 隔离，否则任一用户
可经 GET /reports/{id} 读到他人真实成交/持仓（评审 #1 数据泄漏）。

单事务内：report 加 account_id（nullable）、回填现存报告归属首账户、置 NOT NULL。
空库（测试 schema）report 无行 → 回填 no-op → NOT NULL 于空表成立。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report",
        sa.Column("account_id", sa.Integer, sa.ForeignKey("account.id"), nullable=True),
    )
    # 回填现存报告归属首账户（生产单账户 id=1；空库无行 → no-op）
    op.execute(
        """
        UPDATE report
        SET account_id = (SELECT id FROM account ORDER BY id ASC LIMIT 1)
        WHERE account_id IS NULL
        """
    )
    # 兜底：若回填后仍有 report 无归属（异常前置态：有报告但无任何账户——
    # pre-G3 generate_weekly 曾与账户无关），明确报错而非撞 NOT NULL 的隐晦约束错。
    remaining = op.get_bind().execute(
        sa.text("SELECT count(*) FROM report WHERE account_id IS NULL")
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"0019 迁移中止：{remaining} 条报告无法归属账户（库中无任何 account）。"
            "请先创建账户后重跑迁移，不静默丢弃报告。"
        )
    op.alter_column("report", "account_id", nullable=False)


def downgrade() -> None:
    op.drop_column("report", "account_id")
