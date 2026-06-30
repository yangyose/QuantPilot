"""集成测试共享辅助（V1.5-G 多用户）。

`account.user_id` 自 alembic 0018 起为 NOT NULL。集成测试 schema 经
`alembic upgrade head` 建表时，0018 已从 env admin 种子首用户（committed，
跨 db_session 逐测试 rollback 存活）。建账户的测试统一引用该种子用户，避免
``user`` UNIQUE 约束在重复建用户时冲突。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.user import User


async def seeded_user_id(session: AsyncSession) -> int:
    """返回 0018 迁移种子的首用户 id（最小 id）。"""
    result = await session.execute(select(User.id).order_by(User.id).limit(1))
    return result.scalar_one()
