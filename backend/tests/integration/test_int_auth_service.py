"""V1.5-G G-2 集成测试：AuthService 注册/登录（需真实 PostgreSQL）。

- INT-REG-01：register 建 user(level=L1) + 自动建空账户（user_id 绑定）。
- INT-REG-02：register 重复 username/email → DuplicateUserError。
- INT-REG-03：弱密码 → ValueError。
- INT-AUTH-01：get_user_by_username 命中；is_active 翻转。
- INT-LVL（update_me）：改 level/email；非法 level → ValueError。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.core.security import verify_password
from quantpilot.models.account import Account
from quantpilot.services.auth_service import AuthService, DuplicateUserError

_PW = "Str0ngPass!"


async def test_int_reg_01_register_creates_user_and_account(
    db_session: AsyncSession,
) -> None:
    """register → user(level=L1, is_active) + 自动建空账户（user_id 绑定）。"""
    svc = AuthService(db_session)
    user = await svc.register("regalice", "RegAlice@Example.com", _PW)

    assert user.id is not None
    assert user.level == "L1"
    assert user.is_active is True
    assert user.email == "regalice@example.com"  # 规范化小写
    assert verify_password(_PW, user.password_hash)

    account = (
        await db_session.execute(
            select(Account).where(Account.user_id == user.id)
        )
    ).scalar_one()
    assert account.cash == 0.0
    assert account.user_id == user.id


async def test_int_reg_02_duplicate_username_rejected(
    db_session: AsyncSession,
) -> None:
    svc = AuthService(db_session)
    await svc.register("dupreg", "dupreg1@example.com", _PW)
    with pytest.raises(DuplicateUserError):
        await svc.register("dupreg", "dupreg2@example.com", _PW)


async def test_int_reg_02_duplicate_email_rejected(
    db_session: AsyncSession,
) -> None:
    svc = AuthService(db_session)
    await svc.register("emailreg1", "dupemail@example.com", _PW)
    with pytest.raises(DuplicateUserError):
        await svc.register("emailreg2", "DupEmail@example.com", _PW)  # 大小写规范化后撞


async def test_int_reg_03_weak_password_rejected(db_session: AsyncSession) -> None:
    svc = AuthService(db_session)
    with pytest.raises(ValueError):
        await svc.register("weakreg", "weak@example.com", "short")


async def test_int_auth_01_get_user_and_is_active(db_session: AsyncSession) -> None:
    """get_user_by_username 命中；is_active 可翻转（停用）。"""
    svc = AuthService(db_session)
    await svc.register("authuser", "authuser@example.com", _PW)

    fetched = await svc.get_user_by_username("authuser")
    assert fetched is not None
    assert fetched.is_active is True

    fetched.is_active = False
    await db_session.flush()
    refetched = await svc.get_user_by_username("authuser")
    assert refetched.is_active is False


async def test_int_lvl_update_me(db_session: AsyncSession) -> None:
    """update_me 改 level（L1→L3）；非法 level → ValueError。"""
    svc = AuthService(db_session)
    user = await svc.register("lvluser", "lvluser@example.com", _PW)

    await svc.update_me(user, level="L3")
    assert user.level == "L3"

    with pytest.raises(ValueError):
        await svc.update_me(user, level="L9")


async def test_int_lvl_update_me_duplicate_email(db_session: AsyncSession) -> None:
    """update_me 改 email 撞他人 → DuplicateUserError。"""
    svc = AuthService(db_session)
    await svc.register("u_a", "u_a@example.com", _PW)
    user_b = await svc.register("u_b", "u_b@example.com", _PW)
    with pytest.raises(DuplicateUserError):
        await svc.update_me(user_b, email="u_a@example.com")
