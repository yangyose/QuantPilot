"""V1.5-G G-3 集成测试：账户层数据隔离 + ownership 越权防护（§5.2，需真实 PostgreSQL）。

- INT-ISO-01：用户 A 的成交/持仓/流水不在用户 B 的列表里（account_id 过滤隔离）。
- INT-ISO-02：用户 B 持自己账户按 A 的资源 id 作废/改持仓/查报告 → not found/None
  （ownership：跨账户 id 查无，不泄露存在性 → 路由转 404）。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.core.security import hash_password
from quantpilot.models.account import Account
from quantpilot.models.user import User
from quantpilot.services.account_service import AccountService
from quantpilot.services.report_service import ReportService
from tests.integration._helpers import seeded_user_id

_TRADE_DATE = date(2026, 4, 10)
_TS = "ISOOWN1.SZ"


async def _account_for_seeded_user(session: AsyncSession) -> Account:
    """账户 A：归属 0018 种子首用户。"""
    acc = Account(
        user_id=await seeded_user_id(session),
        name="账户A", account_type="REAL", cash=100000.0, total_assets=100000.0,
    )
    session.add(acc)
    await session.flush()
    return acc


async def _account_for_new_user(session: AsyncSession) -> Account:
    """账户 B：新建用户 B（唯一 username/email）+ 其账户。"""
    user_b = User(
        username="iso_user_b", email="iso_user_b@test.local",
        password_hash=hash_password("Str0ngPass!"), level="L1",
    )
    session.add(user_b)
    await session.flush()
    acc = Account(
        user_id=user_b.id,
        name="账户B", account_type="REAL", cash=100000.0, total_assets=100000.0,
    )
    session.add(acc)
    await session.flush()
    return acc


async def test_int_iso_01_lists_scoped_by_account(db_session: AsyncSession) -> None:
    """A 的成交/持仓/流水不出现在 B 的列表（account_id 过滤隔离）。"""
    acc_a = await _account_for_seeded_user(db_session)
    acc_b = await _account_for_new_user(db_session)
    svc = AccountService(db_session)

    await svc.record_trade(
        account_id=acc_a.id, ts_code=_TS, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )

    # A 自己能看到；B 看不到
    a_trades, a_total = await svc.list_trades(acc_a.id)
    assert a_total == 1
    b_trades, b_total = await svc.list_trades(acc_b.id)
    assert b_total == 0
    assert await svc.get_positions(acc_b.id) == []
    # A 有 BUY_FEE 流水；B 无
    _, a_cash_total = await svc.get_cashflow(acc_a.id, include_voided=True)
    assert a_cash_total == 1
    _, b_cash_total = await svc.get_cashflow(acc_b.id, include_voided=True)
    assert b_cash_total == 0


async def test_int_iso_02_void_trade_cross_account_not_found(
    db_session: AsyncSession,
) -> None:
    """B 按 A 的 trade_id 作废 → not found（ownership）；A 的成交未被改动。"""
    acc_a = await _account_for_seeded_user(db_session)
    acc_b = await _account_for_new_user(db_session)
    svc = AccountService(db_session)

    trade = await svc.record_trade(
        account_id=acc_a.id, ts_code=_TS, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )

    with pytest.raises(ValueError, match="not found"):
        await svc.void_trade(trade.id, acc_b.id)

    # A 的成交未被作废
    a_trades, _ = await svc.list_trades(acc_a.id)
    assert a_trades[0].is_voided is False


async def test_int_iso_02_update_position_cross_account_not_found(
    db_session: AsyncSession,
) -> None:
    """B 按 A 的 position_id 改持仓 → not found（ownership）。"""
    acc_a = await _account_for_seeded_user(db_session)
    acc_b = await _account_for_new_user(db_session)
    svc = AccountService(db_session)

    await svc.record_trade(
        account_id=acc_a.id, ts_code=_TS, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )
    pos = (await svc.get_positions(acc_a.id))[0]

    with pytest.raises(ValueError, match="not found"):
        await svc.update_position(pos.id, acc_b.id, current_price=12.0)


async def test_int_iso_02_void_fund_flow_cross_account_not_found(
    db_session: AsyncSession,
) -> None:
    """B 按 A 的 fund_flow_id 作废 → not found（ownership）。"""
    acc_a = await _account_for_seeded_user(db_session)
    acc_b = await _account_for_new_user(db_session)
    svc = AccountService(db_session)

    flow = await svc.deposit(account_id=acc_a.id, amount=5000.0, trade_date=_TRADE_DATE)

    with pytest.raises(ValueError, match="not found"):
        await svc.void_fund_flow(flow.id, acc_b.id)


async def test_int_iso_02_report_get_by_id_cross_account_none(
    db_session: AsyncSession,
) -> None:
    """B 按 A 的 report_id 查报告 → None（ownership，路由转 404）；get_list 亦隔离。"""
    acc_a = await _account_for_seeded_user(db_session)
    acc_b = await _account_for_new_user(db_session)
    rsvc = ReportService(db_session)

    report = await rsvc.generate_custom(_TRADE_DATE, _TRADE_DATE, acc_a.id)

    assert await rsvc.get_by_id(report.id, acc_a.id) is not None
    assert await rsvc.get_by_id(report.id, acc_b.id) is None
    _, b_total = await rsvc.get_list(acc_b.id)
    assert b_total == 0
