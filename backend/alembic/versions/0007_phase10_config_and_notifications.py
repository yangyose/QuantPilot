"""Phase 10: pipeline_run.config_snapshot + backtest_task.config_snapshot + in_app_notification

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-20

Phase 10 §2.1：
- `pipeline_run.config_snapshot` JSONB nullable（启动时一次性写入的配置快照，§4.3）
- `backtest_task.config_snapshot` JSONB nullable（回测 Engine 参数快照，§4.4 评审 Q-2）
- `in_app_notification` 新表（通知兜底 + 前端 Bell，SDD §13.1）
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. pipeline_run 新增 config_snapshot
    op.add_column(
        "pipeline_run",
        sa.Column(
            "config_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # 2. backtest_task 新增 config_snapshot（与 config_json 区分：后者为用户回测参数，本列为 Engine 层快照）
    op.add_column(
        "backtest_task",
        sa.Column(
            "config_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # 3. in_app_notification 新表
    op.create_table(
        "in_app_notification",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("notify_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "wx_pushed", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("wx_error", sa.Text(), nullable=True),
        sa.Column("read_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_notify_unread",
        "in_app_notification",
        [sa.text("created_at DESC")],
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index(
        "idx_notify_type_created",
        "in_app_notification",
        ["notify_type", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_notify_type_created", table_name="in_app_notification")
    op.drop_index("idx_notify_unread", table_name="in_app_notification")
    op.drop_table("in_app_notification")
    op.drop_column("backtest_task", "config_snapshot")
    op.drop_column("pipeline_run", "config_snapshot")
