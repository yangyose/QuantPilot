"""Phase 8: add backtest_task and backtest_result tables

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-14

新建 backtest_task / backtest_result 两张表（Phase 8 §2.1）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # backtest_task
    op.create_table(
        "backtest_task",
        sa.Column("task_id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
    )

    # backtest_result
    op.create_table(
        "backtest_result",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("backtest_task.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("performance_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("daily_nav_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("disclaimer", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=True,
        ),
        sa.UniqueConstraint("task_id", name="uq_backtest_result_task_id"),
    )
    op.create_index("idx_backtest_result_task", "backtest_result", ["task_id"])


def downgrade() -> None:
    op.drop_index("idx_backtest_result_task", table_name="backtest_result")
    op.drop_table("backtest_result")
    op.drop_table("backtest_task")
