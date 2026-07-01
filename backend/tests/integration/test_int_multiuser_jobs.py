"""V1.5-G G-4c 集成测试：批量 Job 多用户化基础查询（§6.4，需真实 PostgreSQL）。

INT-JOB-01：list_active_user_accounts 仅返回 is_active 用户的账户（停用用户账户
排除在止损/报告批量 Job 之外），按 account.id 稳定排序。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.core.security import hash_password
from quantpilot.models.account import Account
from quantpilot.models.user import User
from quantpilot.services.account_service import AccountService


async def _make_user_with_account(
    session: AsyncSession, username: str, *, is_active: bool,
) -> Account:
    user = User(
        username=username, email=f"{username}@test.local",
        password_hash=hash_password("Str0ngPass!"), level="L1", is_active=is_active,
    )
    session.add(user)
    await session.flush()
    acc = Account(
        user_id=user.id, name=f"{username}-账户", account_type="REAL",
        cash=100000.0, total_assets=100000.0,
    )
    session.add(acc)
    await session.flush()
    return acc


async def test_int_job_01_active_accounts_only(db_session: AsyncSession) -> None:
    """停用用户账户不在批量 Job 遍历范围；仅返回 active 用户账户。

    基线：alembic 0008 已种子 1 个默认账户（0018 回填归属 active 种子 admin）。
    """
    before = {a.id for a in await AccountService(db_session).list_active_user_accounts()}

    active_acc = await _make_user_with_account(db_session, "job_active", is_active=True)
    inactive_acc = await _make_user_with_account(
        db_session, "job_inactive", is_active=False,
    )

    accounts = await AccountService(db_session).list_active_user_accounts()
    ids = [a.id for a in accounts]

    # active 用户账户纳入；停用用户账户排除
    assert active_acc.id in ids
    assert inactive_acc.id not in ids
    # 相对基线恰好多了 1 个（active_acc），inactive 未计入
    assert set(ids) == before | {active_acc.id}
    # 稳定排序（account.id 升序）
    assert ids == sorted(ids)
