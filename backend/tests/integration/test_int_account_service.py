"""INT-ACC-01~11 / INT-P14-1-01~06: AccountService 集成测试（需真实 PostgreSQL）。

V1.0 整改 Batch 2 — B2-6 新增：
- INT-ACC-10：已平仓股票分红仅写 fund_flow，不改 cost_price（S7-GAP-04 回归）
- INT-ACC-11：get_current_drawdown 计算账户最大回撤（B2-1 配套）

Phase 14 §14-1 RM-13 deposit 幂等：
- INT-P14-1-01：find_fund_flow_by_idempotency 命中/未命中
- INT-P14-1-02：deposit 同 key 重复 → 返回原 flow + cash 不变
- INT-P14-1-03：record_dividend 同 key 重复 → 返回原 flow + cost_price 不二次扣
- INT-P14-1-04：deposit 不传 key（旧路径）→ 多次调用产生多行（兼容回归）
- INT-P14-1-05：真 DB partial unique 抛 IntegrityError + service 兜底重查
- INT-P14-1-06：asyncio.gather 并发 2 个同 key → 两返回值同 flow_id + cash 仅加一次
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.core.database import AsyncSessionLocal
from quantpilot.models.account import Account, FundFlow
from quantpilot.models.market import DailyQuote
from quantpilot.services.account_service import AccountService
from tests.integration._helpers import seeded_user_id

# ---------------------------------------------------------------------------
# 测试常量
# ---------------------------------------------------------------------------
_TRADE_DATE = date(2026, 4, 10)
_TS_A = "INTACC1.SZ"
_TS_B = "INTACC2.SZ"


async def _make_account(
    session: AsyncSession, name: str = "测试账户", cash: float = 100000.0
) -> Account:
    account = Account(
        user_id=await seeded_user_id(session),
        name=name, account_type="REAL", cash=cash, total_assets=cash,
    )
    session.add(account)
    await session.flush()
    await session.refresh(account)
    return account


# ---------------------------------------------------------------------------
# INT-ACC-01: BUY → Position 创建 + cash 扣减 + fund_flow 写入
# ---------------------------------------------------------------------------
async def test_int_acc_01_buy_creates_position(db_session: AsyncSession) -> None:
    """BUY 成交 → 新建 Position，cash 扣减，fund_flow 写入 BUY_FEE。"""
    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    trade = await svc.record_trade(
        account_id=account.id,
        ts_code=_TS_A,
        trade_type="BUY",
        trade_date=_TRADE_DATE,
        price=10.0,
        shares=1000,
        commission=25.0,
    )

    assert trade.id is not None
    assert trade.trade_type == "BUY"

    positions = await svc.get_positions(account.id)
    assert len(positions) == 1
    pos = positions[0]
    assert pos.ts_code == _TS_A
    assert pos.shares == 1000
    assert float(pos.cost_price) == pytest.approx(10.025)  # WAC: (10000+25)/1000
    assert pos.phase == "BUILD"

    updated_account = await svc.get_account(account.id)
    assert float(updated_account.cash) == pytest.approx(100000.0 - 10025.0)

    flows, total = await svc.get_cashflow(account.id)
    assert total == 1
    assert flows[0].flow_type == "BUY_FEE"
    assert float(flows[0].amount) == pytest.approx(-10025.0)


# ---------------------------------------------------------------------------
# INT-ACC-02: 加仓 → WAC 成本价更新
# ---------------------------------------------------------------------------
async def test_int_acc_02_add_to_position_wac(db_session: AsyncSession) -> None:
    """两次 BUY → WAC 正确聚合。"""
    account = await _make_account(db_session, cash=200000.0)
    svc = AccountService(db_session)

    # 第一次买入：1000 股 @ 10.0
    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )
    # 第二次买入：500 股 @ 12.0
    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=12.0, shares=500, commission=0.0,
    )

    positions = await svc.get_positions(account.id)
    assert len(positions) == 1
    pos = positions[0]
    assert pos.shares == 1500
    expected_wac = (1000 * 10.0 + 500 * 12.0) / 1500  # ≈ 10.6667
    assert float(pos.cost_price) == pytest.approx(expected_wac, rel=1e-4)


# ---------------------------------------------------------------------------
# INT-ACC-03: SELL 部分 → Position 减仓，phase=REDUCE
# ---------------------------------------------------------------------------
async def test_int_acc_03_partial_sell(db_session: AsyncSession) -> None:
    """SELL 部分持仓 → shares 减少，phase=REDUCE，proceeds 入账。"""
    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )
    cash_after_buy = float((await svc.get_account(account.id)).cash)

    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="SELL",
        trade_date=_TRADE_DATE, price=12.0, shares=500,
        commission=15.0, stamp_tax=6.0,
    )

    positions = await svc.get_positions(account.id)
    assert len(positions) == 1
    assert positions[0].shares == 500
    assert positions[0].phase == "REDUCE"

    expected_proceeds = 12.0 * 500 - 15.0 - 6.0  # 6000 - 21 = 5979
    updated_cash = float((await svc.get_account(account.id)).cash)
    assert updated_cash == pytest.approx(cash_after_buy + expected_proceeds)

    flows, _ = await svc.get_cashflow(account.id)
    sell_flow = next(f for f in flows if f.flow_type == "SELL_PROCEEDS")
    assert float(sell_flow.amount) == pytest.approx(expected_proceeds)


# ---------------------------------------------------------------------------
# INT-ACC-04: SELL 清仓 → Position 删除
# ---------------------------------------------------------------------------
async def test_int_acc_04_full_sell_deletes_position(db_session: AsyncSession) -> None:
    """SELL 全部持仓 → Position 行删除。"""
    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )
    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="SELL",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )

    positions = await svc.get_positions(account.id)
    assert len(positions) == 0


# ---------------------------------------------------------------------------
# INT-ACC-05: SELL 超卖 → ValueError
# ---------------------------------------------------------------------------
async def test_int_acc_05_oversell_raises(db_session: AsyncSession) -> None:
    """SELL 数量超过持仓 → ValueError（超卖）。"""
    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=500, commission=0.0,
    )
    with pytest.raises(ValueError, match="超卖"):
        await svc.record_trade(
            account_id=account.id, ts_code=_TS_A, trade_type="SELL",
            trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
        )


# ---------------------------------------------------------------------------
# INT-ACC-06: 分红 → cost_price 调整 + fund_flow DIVIDEND
# ---------------------------------------------------------------------------
async def test_int_acc_06_dividend_adjusts_cost_price(db_session: AsyncSession) -> None:
    """手动录入分红 → cost_price 降低，写入 DIVIDEND fund_flow。"""
    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )
    cost_before = float((await svc.get_positions(account.id))[0].cost_price)

    # 分红：每股 0.5 元，共 500 元
    await svc.record_dividend(
        account_id=account.id, ts_code=_TS_A,
        amount=500.0, trade_date=_TRADE_DATE,
    )

    pos = (await svc.get_positions(account.id))[0]
    expected_cost = cost_before - 500.0 / 1000  # cost_price -= amount / shares
    assert float(pos.cost_price) == pytest.approx(expected_cost)

    flows, _ = await svc.get_cashflow(account.id, flow_type="DIVIDEND")
    assert len(flows) == 1
    assert float(flows[0].amount) == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# INT-ACC-07: get_all_positions 跨账户
# ---------------------------------------------------------------------------
async def test_int_acc_07_get_all_positions_cross_account(db_session: AsyncSession) -> None:
    """get_all_positions() 返回所有账户的持仓。"""
    acc1 = await _make_account(db_session, name="账户1", cash=100000.0)
    acc2 = await _make_account(db_session, name="账户2", cash=100000.0)
    svc = AccountService(db_session)

    await svc.record_trade(
        account_id=acc1.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )
    await svc.record_trade(
        account_id=acc2.id, ts_code=_TS_B, trade_type="BUY",
        trade_date=_TRADE_DATE, price=20.0, shares=500, commission=0.0,
    )

    all_positions = await svc.get_all_positions()
    codes = {p.ts_code for p in all_positions}
    assert _TS_A in codes
    assert _TS_B in codes


# ---------------------------------------------------------------------------
# INT-ACC-08: sync_account 更新价格（含 DailyQuote 数据）
# ---------------------------------------------------------------------------
async def test_int_acc_08_sync_account_updates_price(db_session: AsyncSession) -> None:
    """sync_account 从 daily_quote 获取最新价，更新 position.current_price 和 total_assets。"""
    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )

    # 写入 DailyQuote 最新价
    quote = DailyQuote(
        ts_code=_TS_A,
        trade_date=_TRADE_DATE,
        close=12.5,
        is_suspended=False,
        is_st=False,
        limit_up=False,
        limit_down=False,
    )
    db_session.add(quote)
    await db_session.flush()

    updated = await svc.sync_account(account.id)
    positions = await svc.get_positions(account.id)
    pos = positions[0]

    assert float(pos.current_price) == pytest.approx(12.5)
    assert float(pos.market_value) == pytest.approx(12500.0)
    assert float(pos.pnl_pct) == pytest.approx(0.25)  # (12.5 - 10.0) / 10.0
    # total_assets = cash(90000) + market_value(12500)
    assert float(updated.total_assets) == pytest.approx(90000.0 + 12500.0)


# ---------------------------------------------------------------------------
# INT-ACC-09: withdraw cash 不足 → ValueError
# ---------------------------------------------------------------------------
async def test_int_acc_09_withdraw_insufficient_cash(db_session: AsyncSession) -> None:
    """出金超过可用现金 → ValueError。"""
    account = await _make_account(db_session, cash=1000.0)
    svc = AccountService(db_session)

    with pytest.raises(ValueError, match="现金不足"):
        await svc.withdraw(
            account_id=account.id,
            amount=5000.0,
            trade_date=_TRADE_DATE,
        )


# ---------------------------------------------------------------------------
# INT-ACC-10 (V1.0 整改 Batch 2 — B2-6 / S7-GAP-04 回归):
# 已平仓股票的分红仅写 fund_flow + cash 入账，不调整 cost_price
# ---------------------------------------------------------------------------
async def test_int_acc_10_dividend_on_closed_position_keeps_no_cost_change(
    db_session: AsyncSession,
) -> None:
    """B2-2 排查文档化：已平仓时 record_dividend 仅写 fund_flow，不创建/改 cost_price。"""
    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    # 全卖：建仓 → 全卖出 → 持仓应被删除
    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )
    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="SELL",
        trade_date=_TRADE_DATE, price=12.0, shares=1000, commission=0.0,
    )
    positions_before = await svc.get_positions(account.id)
    assert all(p.ts_code != _TS_A for p in positions_before), "全卖后持仓应已删除"

    cash_before = float((await svc.get_account(account.id)).cash)

    # 平仓后录入分红：cash 入账 + fund_flow 写入；不应抛错，不应创建新 Position
    await svc.record_dividend(
        account_id=account.id, ts_code=_TS_A,
        amount=500.0, trade_date=_TRADE_DATE,
    )

    cash_after = float((await svc.get_account(account.id)).cash)
    assert cash_after == pytest.approx(cash_before + 500.0)

    flows, _ = await svc.get_cashflow(account.id, flow_type="DIVIDEND")
    assert len(flows) == 1
    assert float(flows[0].amount) == pytest.approx(500.0)
    assert flows[0].ts_code == _TS_A

    positions_after = await svc.get_positions(account.id)
    assert all(p.ts_code != _TS_A for p in positions_after), "分红不应重建已平仓 Position"


# ---------------------------------------------------------------------------
# INT-ACC-11 (V1.0 整改 Batch 2 — B2-6 / B2-1 配套):
# get_current_drawdown 基于 daily_portfolio_value 计算账户最大回撤
# ---------------------------------------------------------------------------
async def test_int_acc_11_get_current_drawdown(db_session: AsyncSession) -> None:
    """seed daily_portfolio_value：100 → 110 → 88（峰 110，谷 88）→ 最大回撤 ≈ 20%。"""
    from datetime import timedelta as _td

    from quantpilot.models.account import DailyPortfolioValue

    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    seed = [(0, 100.0), (1, 110.0), (2, 88.0)]  # 峰 110，谷 88 → DD = 22/110 = 0.2
    for offset, total in seed:
        db_session.add(DailyPortfolioValue(
            account_id=account.id,
            trade_date=_TRADE_DATE + _td(days=offset),
            total_value=total,
            cash=0.0,
            position_value=total,
        ))
    await db_session.flush()

    dd = await svc.get_current_drawdown(account.id)
    assert dd is not None
    assert dd == pytest.approx(22.0 / 110.0, rel=1e-6)

    # 不足 2 个净值点 → None（无法计算回撤）
    account2 = await _make_account(db_session, name="账户2", cash=100000.0)
    db_session.add(DailyPortfolioValue(
        account_id=account2.id,
        trade_date=_TRADE_DATE,
        total_value=100.0,
        cash=0.0,
        position_value=100.0,
    ))
    await db_session.flush()
    assert await svc.get_current_drawdown(account2.id) is None


# ---------------------------------------------------------------------------
# INT-P14-1-01: find_fund_flow_by_idempotency 命中 / 未命中
# ---------------------------------------------------------------------------
async def test_int_p14_1_01_find_fund_flow_by_idempotency(
    db_session: AsyncSession,
) -> None:
    """find 命中已存在 key；不存在的 key 返回 None；其他账户的 key 不串账。"""
    account = await _make_account(db_session, cash=100000.0)
    other = await _make_account(db_session, name="账户B", cash=100000.0)
    svc = AccountService(db_session)

    key = "deposit-key-abc-001"
    flow = await svc.deposit(
        account_id=account.id, amount=10000.0,
        trade_date=_TRADE_DATE, idempotency_key=key,
    )

    hit = await svc.find_fund_flow_by_idempotency(account.id, key)
    assert hit is not None
    assert hit.id == flow.id

    miss = await svc.find_fund_flow_by_idempotency(account.id, "non-existent-key")
    assert miss is None

    # 同 key 在不同 account 应不串账
    other_miss = await svc.find_fund_flow_by_idempotency(other.id, key)
    assert other_miss is None


# ---------------------------------------------------------------------------
# INT-P14-1-02: deposit 同 key 重复 → 返回原 flow + cash 不变
# ---------------------------------------------------------------------------
async def test_int_p14_1_02_deposit_same_key_returns_original(
    db_session: AsyncSession,
) -> None:
    """同 idempotency_key 调 2 次 deposit → 返回同 flow_id；account.cash 仅加一次。"""
    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    key = "deposit-idem-001"
    flow1 = await svc.deposit(
        account_id=account.id, amount=50000.0,
        trade_date=_TRADE_DATE, idempotency_key=key,
    )
    cash_after_first = float((await svc.get_account(account.id)).cash)

    flow2 = await svc.deposit(
        account_id=account.id, amount=50000.0,
        trade_date=_TRADE_DATE, idempotency_key=key,
    )
    cash_after_second = float((await svc.get_account(account.id)).cash)

    assert flow1.id == flow2.id, "同 key 应返回同 flow_id"
    assert cash_after_second == pytest.approx(cash_after_first), \
        "幂等命中 cash 不应再次累加"
    assert cash_after_first == pytest.approx(100000.0 + 50000.0)

    flows, total = await svc.get_cashflow(account.id, flow_type="DEPOSIT")
    assert total == 1, "幂等命中不应产生第二行 fund_flow"
    assert flows[0].idempotency_key == key


# ---------------------------------------------------------------------------
# INT-P14-1-03: record_dividend 同 key 重复 → 返回原 flow + cost_price 不二次扣
# ---------------------------------------------------------------------------
async def test_int_p14_1_03_dividend_same_key_returns_original(
    db_session: AsyncSession,
) -> None:
    """同 idempotency_key 调 2 次 record_dividend → 返回同 flow_id + cost_price 仅扣一次。"""
    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )
    cost_before = float((await svc.get_positions(account.id))[0].cost_price)

    key = "dividend-idem-001"
    flow1 = await svc.record_dividend(
        account_id=account.id, ts_code=_TS_A,
        amount=500.0, trade_date=_TRADE_DATE, idempotency_key=key,
    )
    cost_after_first = float((await svc.get_positions(account.id))[0].cost_price)
    assert cost_after_first == pytest.approx(cost_before - 0.5)

    flow2 = await svc.record_dividend(
        account_id=account.id, ts_code=_TS_A,
        amount=500.0, trade_date=_TRADE_DATE, idempotency_key=key,
    )
    cost_after_second = float((await svc.get_positions(account.id))[0].cost_price)

    assert flow1.id == flow2.id
    assert cost_after_second == pytest.approx(cost_after_first), \
        "幂等命中 cost_price 不应再次调整"

    flows, total = await svc.get_cashflow(account.id, flow_type="DIVIDEND")
    assert total == 1
    assert flows[0].idempotency_key == key


# ---------------------------------------------------------------------------
# INT-P14-1-04: deposit 不传 key（旧路径）→ 多次调用产生多行（兼容回归）
# ---------------------------------------------------------------------------
async def test_int_p14_1_04_deposit_no_key_creates_multiple_rows(
    db_session: AsyncSession,
) -> None:
    """不传 idempotency_key（None）→ 走旧路径，多次调用产生多行，cash 累加。"""
    account = await _make_account(db_session, cash=0.0)
    svc = AccountService(db_session)

    for _ in range(3):
        await svc.deposit(
            account_id=account.id, amount=10000.0,
            trade_date=_TRADE_DATE,
        )

    cash = float((await svc.get_account(account.id)).cash)
    assert cash == pytest.approx(30000.0), "不传 key 旧路径应累加 3 次"

    flows, total = await svc.get_cashflow(account.id, flow_type="DEPOSIT")
    assert total == 3
    assert all(f.idempotency_key is None for f in flows)


# ---------------------------------------------------------------------------
# INT-P14-1-05a: 真 DB partial unique 索引拒绝同 (account_id, key) 重复
# ---------------------------------------------------------------------------
async def test_int_p14_1_05a_partial_unique_rejects_duplicate(
    db_session: AsyncSession,
) -> None:
    """绕过 service 直接写两行同 (account_id, idempotency_key) → IntegrityError。

    覆盖 alembic 0013 partial unique 索引在真 DB 层的兜底约束。
    触发 IntegrityError 后 session 进入失败态，由 conftest fixture 末尾 rollback 收尾，
    本测试不再做后续断言。"""
    account = await _make_account(db_session, cash=100000.0)
    key = "raw-insert-conflict-001"

    db_session.add(FundFlow(
        account_id=account.id, flow_type="DEPOSIT",
        amount=1000.0, trade_date=_TRADE_DATE, idempotency_key=key,
    ))
    await db_session.flush()

    db_session.add(FundFlow(
        account_id=account.id, flow_type="DEPOSIT",
        amount=2000.0, trade_date=_TRADE_DATE, idempotency_key=key,
    ))
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# INT-P14-1-05b: partial unique 允许多个 NULL key 共存（兼容旧行）
# ---------------------------------------------------------------------------
async def test_int_p14_1_05b_partial_unique_allows_multiple_nulls(
    db_session: AsyncSession,
) -> None:
    """同 account 多个 idempotency_key=NULL 行不抵触 partial unique（旧行兼容）。"""
    account = await _make_account(db_session, cash=0.0)

    db_session.add(FundFlow(
        account_id=account.id, flow_type="DEPOSIT",
        amount=100.0, trade_date=_TRADE_DATE, idempotency_key=None,
    ))
    db_session.add(FundFlow(
        account_id=account.id, flow_type="DEPOSIT",
        amount=200.0, trade_date=_TRADE_DATE, idempotency_key=None,
    ))
    await db_session.flush()  # 不应抛错（partial unique WHERE key IS NOT NULL）


# ---------------------------------------------------------------------------
# INT-P14-1-06: asyncio.gather 并发 2 个同 key deposit → 兜底重查返回同 flow
# ---------------------------------------------------------------------------
async def test_int_p14_1_06_concurrent_deposit_same_key(
    db_session: AsyncSession,
) -> None:
    """两个独立 session 并发 deposit 同 key → 两返回 flow_id 相同 + cash 仅加一次。

    用独立 AsyncSessionLocal 模拟真并发（commit）。db_session 仅用来 seed account
    + 收尾清理；并发写入用 outer commit 写真 DB，测试末尾显式 DELETE 清理。
    """
    # seed account 并 commit 到真 DB（脱离 db_session 的 begin/rollback）
    # 改走独立 session 创建账户
    async with AsyncSessionLocal() as setup_sess:
        acc = Account(user_id=await seeded_user_id(setup_sess),
                       name="并发测试账户", account_type="REAL",
                       cash=0.0, total_assets=0.0)
        setup_sess.add(acc)
        await setup_sess.commit()
        await setup_sess.refresh(acc)
        account_id = acc.id

    key = "concurrent-idem-001"

    async def _one_deposit() -> FundFlow:
        async with AsyncSessionLocal() as sess:
            svc = AccountService(sess)
            flow = await svc.deposit(
                account_id=account_id, amount=10000.0,
                trade_date=_TRADE_DATE, idempotency_key=key,
            )
            await sess.commit()
            await sess.refresh(flow)
            return flow

    try:
        flow_a, flow_b = await asyncio.gather(_one_deposit(), _one_deposit())

        assert flow_a.id == flow_b.id, "并发同 key 必须返回同一个 flow_id"

        async with AsyncSessionLocal() as check_sess:
            check_svc = AccountService(check_sess)
            cash = float((await check_svc.get_account(account_id)).cash)
            assert cash == pytest.approx(10000.0), \
                "并发命中 cash 必须只加一次"

            flows, total = await check_svc.get_cashflow(
                account_id, flow_type="DEPOSIT",
            )
            assert total == 1
    finally:
        # 清理：删 fund_flow + account（脱 db_session fixture 的 rollback 边界）
        async with AsyncSessionLocal() as cleanup:
            await cleanup.execute(
                delete(FundFlow).where(FundFlow.account_id == account_id),
            )
            await cleanup.execute(
                delete(Account).where(Account.id == account_id),
            )
            await cleanup.commit()


# ---------------------------------------------------------------------------
# INT-VOID-01: 作废 BUY → 持仓订正 + 现金逆仕訳 + 费用流水联动作废
# ---------------------------------------------------------------------------
async def test_int_void_01_void_buy_reverses_all(db_session: AsyncSession) -> None:
    """作废一笔建仓 BUY → 持仓删除、cash 还原、BUY_FEE 流水作废且默认列表不再展示。"""
    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    trade = await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=25.0,
    )
    assert float((await svc.get_account(account.id)).cash) == pytest.approx(89975.0)

    voided = await svc.void_trade(trade.id, void_note="录入价格错误")
    assert voided.is_voided is True
    assert voided.voided_at is not None

    # 现金完全还原
    assert float((await svc.get_account(account.id)).cash) == pytest.approx(100000.0)
    # 持仓删除（唯一建仓被撤）
    assert await svc.get_positions(account.id) == []
    # 关联 BUY_FEE 流水作废 → 默认列表为空，include_voided 可见
    flows, total = await svc.get_cashflow(account.id)
    assert total == 0
    flows_all, total_all = await svc.get_cashflow(account.id, include_voided=True)
    assert total_all == 1
    assert flows_all[0].is_voided is True


# ---------------------------------------------------------------------------
# INT-VOID-02: 作废污染 WAC 的误买 → 成本还原（核心订正场景）
# ---------------------------------------------------------------------------
async def test_int_void_02_void_polluting_buy_restores_cost(
    db_session: AsyncSession,
) -> None:
    """正确持仓 200@12，误买 100@50 污染 WAC；作废误买后成本还原为 12（非冲正残留）。"""
    account = await _make_account(db_session, cash=1_000_000.0)
    svc = AccountService(db_session)

    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=12.0, shares=200, commission=0.0,
    )
    wrong = await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=50.0, shares=100, commission=0.0,
    )
    polluted = float((await svc.get_positions(account.id))[0].cost_price)
    assert polluted == pytest.approx(7400 / 300, abs=1e-3)  # 24.667（NUMERIC(10,3) 丸め）

    await svc.void_trade(wrong.id, void_note="买错价格")

    pos = (await svc.get_positions(account.id))[0]
    assert pos.shares == 200
    assert float(pos.cost_price) == pytest.approx(12.0)  # SELL 冲正无法做到，replay 可以


# ---------------------------------------------------------------------------
# INT-VOID-03: 作废已被后续卖出依赖的买入 → OversellError，不改动数据
# ---------------------------------------------------------------------------
async def test_int_void_03_void_buy_with_later_sell_rejected(
    db_session: AsyncSession,
) -> None:
    """买 1000 后卖 600；作废该买入会使卖出无券 → 拒绝且数据不变。"""
    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    buy = await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )
    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="SELL",
        trade_date=_TRADE_DATE, price=11.0, shares=600, commission=0.0,
    )

    with pytest.raises(ValueError, match="超过当时持仓"):
        await svc.void_trade(buy.id)

    # 未改动：买入仍有效，持仓 400 不变
    fresh_buy = await svc.list_trades(account.id, include_voided=True)
    assert all(not t.is_voided for t in fresh_buy[0])
    assert (await svc.get_positions(account.id))[0].shares == 400


# ---------------------------------------------------------------------------
# INT-VOID-04: 作废分红 → 成本回升 + 现金逆仕訳
# ---------------------------------------------------------------------------
async def test_int_void_04_void_dividend_restores_cost(
    db_session: AsyncSession,
) -> None:
    """录入分红摊低成本后作废 → cost_price 回升、cash 扣回。"""
    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )
    div = await svc.record_dividend(
        account_id=account.id, ts_code=_TS_A, amount=500.0, trade_date=_TRADE_DATE,
    )
    assert float((await svc.get_positions(account.id))[0].cost_price) == pytest.approx(9.5)
    cash_with_div = float((await svc.get_account(account.id)).cash)

    await svc.void_fund_flow(div.id, void_note="分红录错")

    assert float((await svc.get_positions(account.id))[0].cost_price) == pytest.approx(10.0)
    assert float((await svc.get_account(account.id)).cash) == pytest.approx(
        cash_with_div - 500.0
    )


# ---------------------------------------------------------------------------
# INT-VOID-05: 作废入金 → 现金逆仕訳；默认列表不展示 / include_voided 可见
# ---------------------------------------------------------------------------
async def test_int_void_05_void_deposit_reverses_cash(
    db_session: AsyncSession,
) -> None:
    """入金后作废 → cash 扣回；作废行默认不在流水列表，include_voided 才可见。"""
    account = await _make_account(db_session, cash=1000.0)
    svc = AccountService(db_session)

    flow = await svc.deposit(account_id=account.id, amount=50000.0, trade_date=_TRADE_DATE)
    assert float((await svc.get_account(account.id)).cash) == pytest.approx(51000.0)

    await svc.void_fund_flow(flow.id)
    assert float((await svc.get_account(account.id)).cash) == pytest.approx(1000.0)

    _, total = await svc.get_cashflow(account.id)
    assert total == 0
    _, total_all = await svc.get_cashflow(account.id, include_voided=True)
    assert total_all == 1


# ---------------------------------------------------------------------------
# INT-VOID-06: BUY_FEE / SELL_PROCEEDS 不可单独作废
# ---------------------------------------------------------------------------
async def test_int_void_06_cannot_void_trade_fee_flow_directly(
    db_session: AsyncSession,
) -> None:
    """直接作废 BUY_FEE 流水 → 拒绝（须经成交作废联动）。"""
    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )
    flows, _ = await svc.get_cashflow(account.id, flow_type="BUY_FEE")
    with pytest.raises(ValueError, match="不可单独作废"):
        await svc.void_fund_flow(flows[0].id)


# ---------------------------------------------------------------------------
# INT-VOID-07: 重复作废 / list_trades 过滤
# ---------------------------------------------------------------------------
async def test_int_void_07_double_void_and_list_filter(
    db_session: AsyncSession,
) -> None:
    """已作废成交再次作废 → 拒绝；list_trades 默认过滤作废行。"""
    account = await _make_account(db_session, cash=100000.0)
    svc = AccountService(db_session)

    trade = await svc.record_trade(
        account_id=account.id, ts_code=_TS_A, trade_type="BUY",
        trade_date=_TRADE_DATE, price=10.0, shares=1000, commission=0.0,
    )
    await svc.void_trade(trade.id)

    with pytest.raises(ValueError, match="已作废"):
        await svc.void_trade(trade.id)

    active, total = await svc.list_trades(account.id)
    assert total == 0
    _, total_all = await svc.list_trades(account.id, include_voided=True)
    assert total_all == 1
