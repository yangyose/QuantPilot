"""V1.5-G 多用户：user 表 + account.user_id + env admin→首用户回填

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-30

V1.5-G G-1（设计 docs/design/phases/v1_5_g_multiuser.md §3.3）：
单事务内建 user 表、account 加 user_id（nullable）、从 env admin 种子首用户
（level='L3' 管理员=专业用户）、回填现存账户归属、置 user_id NOT NULL。

env admin 去留：迁移后登录改查 user 表（§4.2）。ADMIN_USERNAME/ADMIN_PASSWORD_HASH
作为「首用户种子」消费后废弃，不再被登录路径读取。

【降级说明】迁移依赖 env admin 存在（启动校验已强制）。若 .env 缺
ADMIN_USERNAME/ADMIN_PASSWORD_HASH，迁移中止报错，不静默建无主账户。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from quantpilot.core.config import settings

    admin_username = settings.admin_username
    admin_hash = settings.admin_password_hash
    if not admin_username or not admin_hash:
        raise RuntimeError(
            "0018 迁移依赖 env admin（ADMIN_USERNAME/ADMIN_PASSWORD_HASH）作首用户种子，"
            "未配置则中止，不建无主账户。"
        )

    # 1. user 表
    op.create_table(
        "user",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(32), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(72), nullable=False),
        sa.Column("level", sa.String(2), nullable=False, server_default="L1"),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.UniqueConstraint("username", name="uq_user_username"),
        sa.UniqueConstraint("email", name="uq_user_email"),
        sa.CheckConstraint("level IN ('L1','L2','L3')", name="ck_user_level"),
    )

    # 2. account 加 user_id（nullable，回填后置 NOT NULL）
    op.add_column(
        "account",
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("user.id"), nullable=True),
    )

    # 3. 种子首用户（env admin → user；level='L3' 管理员=专业用户）
    op.execute(
        sa.text(
            """
            INSERT INTO "user" (username, email, password_hash, level, is_active)
            VALUES (:username, :email, :password_hash, 'L3', true)
            """
        ).bindparams(
            username=admin_username,
            # @local.host（非 @local）以通过应用自身 email 正则（域名需含点，schemas/auth.py）
            email=f"{admin_username}@local.host",
            password_hash=admin_hash,
        )
    )

    # 4. 回填现存账户归属首用户
    op.execute(
        """
        UPDATE account
        SET user_id = (SELECT id FROM "user" ORDER BY id ASC LIMIT 1)
        WHERE user_id IS NULL
        """
    )

    # 5. account.user_id 置 NOT NULL
    op.alter_column("account", "user_id", nullable=False)


def downgrade() -> None:
    op.drop_column("account", "user_id")
    op.drop_table("user")
