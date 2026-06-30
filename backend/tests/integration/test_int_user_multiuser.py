"""V1.5-G G-1 集成测试：user 表 + account.user_id（alembic 0018）。

- INT-MIG-01：迁移 0018 已从 env admin 种子首用户（level='L3'/is_active）；
  现存账户经回填挂到该用户（account.user_id NOT NULL）。
- level CHECK 约束（L1/L2/L3 之外拒绝）。
- username/email DB 层 UNIQUE。
- account.user_id NOT NULL（无主账户被拒）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.core.security import hash_password
from quantpilot.models.account import Account
from quantpilot.models.user import User
from tests.integration._helpers import seeded_user_id


async def test_int_mig_01_seed_user_exists(db_session: AsyncSession) -> None:
    """0018 种子首用户存在：level='L3'、is_active=true。"""
    user = (
        await db_session.execute(select(User).order_by(User.id).limit(1))
    ).scalar_one()
    assert user.level == "L3"  # 管理员=专业用户
    assert user.is_active is True
    assert user.username  # 非空（来自 env ADMIN_USERNAME）
    assert user.email.endswith("@local")


async def test_int_mig_01_account_bound_to_user(db_session: AsyncSession) -> None:
    """新建账户挂到种子用户后，user_id 链路可回查到 User。"""
    uid = await seeded_user_id(db_session)
    acc = Account(user_id=uid, name="G1 绑定测试", account_type="REAL", cash=0.0)
    db_session.add(acc)
    await db_session.flush()
    assert acc.user_id == uid


async def test_int_user_level_check_rejects_invalid(db_session: AsyncSession) -> None:
    """level 非 L1/L2/L3 触发 CHECK 约束。"""
    db_session.add(
        User(
            username="lvlbad", email="lvlbad@test.local",
            password_hash=hash_password("x"), level="L4",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_int_user_username_unique(db_session: AsyncSession) -> None:
    """username DB 层 UNIQUE。"""
    db_session.add(
        User(username="dupname", email="a@test.local", password_hash=hash_password("x"))
    )
    await db_session.flush()
    db_session.add(
        User(username="dupname", email="b@test.local", password_hash=hash_password("x"))
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_int_user_email_unique(db_session: AsyncSession) -> None:
    """email DB 层 UNIQUE。"""
    db_session.add(
        User(username="e1", email="dup@test.local", password_hash=hash_password("x"))
    )
    await db_session.flush()
    db_session.add(
        User(username="e2", email="dup@test.local", password_hash=hash_password("x"))
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_int_account_user_id_not_null(db_session: AsyncSession) -> None:
    """account.user_id NOT NULL：无主账户被拒（裸 SQL 绕过 ORM 默认）。"""
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO account (name, account_type, cash) "
                "VALUES ('无主账户', 'REAL', 0)"
            )
        )
        await db_session.flush()
