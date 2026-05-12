"""Bug 12 修复：幂等播种默认账户 id=1

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-11

V1.0 单管理员场景下整个系统假设 account_id=1 存在（wizard、所有 /account/* 端点、
DailyPipeline mark_to_market 等都默认走 id=1）。原实现没有任何路径自动创建此账户，
导致 OnboardingWizard Step 3「初始资金」调 deposit 时直接 404。本迁移在升级时幂等
插入默认账户，已存在则跳过。
"""
from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO account (id, name, account_type, cash, total_assets)
        VALUES (1, '默认账户', 'REAL', 0, 0)
        ON CONFLICT (id) DO NOTHING
        """
    )
    # 重置序列：插入显式 id 后，后续 autoincrement 起点要跳过 1，否则下次 INSERT 冲突
    op.execute(
        "SELECT setval('account_id_seq', GREATEST(1, (SELECT MAX(id) FROM account)))"
    )


def downgrade() -> None:
    # 只删本迁移种入的默认行（兼名匹配避免误删用户后续创建的账户）
    op.execute("DELETE FROM account WHERE id = 1 AND name = '默认账户'")
