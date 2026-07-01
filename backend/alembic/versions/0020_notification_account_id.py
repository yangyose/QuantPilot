"""V1.5-G G-4b：in_app_notification.account_id（通知账户隔离，混合方案）

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-01

设计 docs/design/phases/v1_5_g_multiuser.md §6.4（2026-07-01 拍板混合方案）：
通知来源混合——共享事件（信号/市场状态/因子告警/健康告警）与账户私有事件
（止损预警/风险告警）同表。account_id **可空**：
- NULL  = 系统级/共享通知，所有登录用户可见
- 非 NULL = 账户私有通知，仅归属用户可见

列表查询 `WHERE account_id IS NULL OR account_id = :current_account_id`。
无需回填（存量通知保持 NULL = 共享，符合"信号层共享"语义，不 N 倍膨胀）。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "in_app_notification",
        sa.Column("account_id", sa.BigInteger, sa.ForeignKey("account.id"), nullable=True),
    )
    # 账户私有通知按账户过滤的查询走此部分索引（account_id 非空行）
    op.create_index(
        "idx_notify_account",
        "in_app_notification",
        ["account_id", "created_at"],
        postgresql_where=sa.text("account_id IS NOT NULL"),
        postgresql_ops={"created_at": "DESC"},
    )


def downgrade() -> None:
    op.drop_index("idx_notify_account", table_name="in_app_notification")
    op.drop_column("in_app_notification", "account_id")
