"""成交/资金流水作废订正（is_voided 软删除）

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-18

依据：录入错误订正机制（B 方案 void + replay 再计算）。
前台成交/资金流水原为 append-only，录入错误无订正经路。本迁移给 trade_record
与 fund_flow 加软删除三列，使「作废」可保留审计痕迹的同时让持仓/现金被正确订正：
- is_voided   BOOLEAN NOT NULL DEFAULT false —— 是否已作废
- voided_at   TIMESTAMPTZ NULL              —— 作废时刻
- void_note   TEXT NULL                     —— 作废原因（订正说明）

持仓由非作废成交 + 非作废分红 replay 再构建；现金按被作废行的 amount 增量逆仕訳。
所有面向用户的查询默认过滤 is_voided = false。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_TABLES = ("trade_record", "fund_flow")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "is_voided",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        op.add_column(
            table,
            sa.Column("voided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        )
        op.add_column(table, sa.Column("void_note", sa.Text(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "void_note")
        op.drop_column(table, "voided_at")
        op.drop_column(table, "is_voided")
