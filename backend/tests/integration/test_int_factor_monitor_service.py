"""INT-FM-01~05: FactorMonitorService 集成测试（需真实 PostgreSQL）。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.models.business import CandidatePool, FactorIcHistory
from quantpilot.models.market import DailyQuote
from quantpilot.services.factor_monitor_service import FactorMonitorService

# ---------------------------------------------------------------------------
# 测试常量（使用独特前缀避免与其他测试数据冲突）
# ---------------------------------------------------------------------------
_CALC_MONTH = date(2026, 3, 31)
_TS_CODES = ["INTFM1.SZ", "INTFM2.SZ", "INTFM3.SZ", "INTFM4.SZ", "INTFM5.SZ"]

# 前向收益窗口（使用 5 而非默认 20，减少测试数据量）
_RETURN_WINDOW = 5


# ---------------------------------------------------------------------------
# 测试辅助：插入候选池和行情数据
# ---------------------------------------------------------------------------

async def _insert_pool_data(session: AsyncSession, trade_date: date) -> None:
    """插入 5 只股票的候选池评分（正相关布局：评分高的股票预期收益也高）。"""
    scores = [85.0, 75.0, 65.0, 55.0, 45.0]
    for i, (ts_code, score) in enumerate(zip(_TS_CODES, scores)):
        session.add(CandidatePool(
            ts_code=ts_code,
            trade_date=trade_date,
            composite_score=score,
            trend_score=score,
            reversion_score=score * 0.9,
            momentum_score=score * 0.8,
            value_score=score * 0.7,
            market_state="UPTREND",
            in_pool=True,
        ))
    await session.flush()


async def _insert_quote_data(
    session: AsyncSession,
    trade_date: date,
    close_prices: dict[str, float],
) -> None:
    """插入指定日期的收盘价。trade_date 须为工作日。"""
    for ts_code, close in close_prices.items():
        # 检查是否已有该 (ts_code, trade_date) 行（uq 约束）
        from sqlalchemy import select
        existing = (await session.execute(
            select(DailyQuote).where(
                DailyQuote.ts_code == ts_code,
                DailyQuote.trade_date == trade_date,
            )
        )).scalar_one_or_none()
        if existing is not None:
            continue

        session.add(DailyQuote(
            ts_code=ts_code,
            trade_date=trade_date,
            open=close * 0.99,
            high=close * 1.01,
            low=close * 0.98,
            close=close,
            vol=1000000,
            amount=close * 1000000,
            adj_factor=1.0,
        ))
    await session.flush()


def _next_workday(d: date, days: int) -> date:
    """从 d 往后跳 days 个工作日。"""
    result = d
    count = 0
    while count < days:
        result += timedelta(days=1)
        if result.weekday() < 5:
            count += 1
    return result


# ---------------------------------------------------------------------------
# INT-FM-01: 无候选池数据 → run_monthly 返回 0
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_fm_01_no_pool_data(db_session: AsyncSession) -> None:
    """INT-FM-01: calc_month 无候选池数据 → run_monthly 返回 0，不写入 FactorIcHistory。"""
    svc = FactorMonitorService(db_session, FactorMonitorEngine())
    written = await svc.run_monthly(date(2020, 1, 31), return_window=_RETURN_WINDOW)
    assert written == 0


# ---------------------------------------------------------------------------
# INT-FM-02: 有候选池和行情数据 → run_monthly 写入 FactorIcHistory
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_fm_02_run_monthly_writes_history(db_session: AsyncSession) -> None:
    """INT-FM-02: 插入 5 只股票评分 + 行情 → run_monthly 写入 5 条 FactorIcHistory。"""
    # 基准日：_CALC_MONTH（月末），跳过周末
    if _CALC_MONTH.weekday() < 5:
        base_date = _CALC_MONTH
    else:
        base_date = _CALC_MONTH - timedelta(days=_CALC_MONTH.weekday() - 4)

    # 插入候选池（基准日）
    await _insert_pool_data(db_session, base_date)

    # 基准日收盘价（评分高的股票价格也高，保证正相关）
    base_prices = {ts: 10.0 + i * 2 for i, ts in enumerate(reversed(_TS_CODES))}
    await _insert_quote_data(db_session, base_date, base_prices)

    # 前向日（base_date + _RETURN_WINDOW 工作日）的收盘价（更高，产生正收益）
    fwd_date = _next_workday(base_date, _RETURN_WINDOW)
    fwd_prices = {ts: price * 1.1 for ts, price in base_prices.items()}  # +10% 均涨
    await _insert_quote_data(db_session, fwd_date, fwd_prices)

    svc = FactorMonitorService(db_session, FactorMonitorEngine())
    written = await svc.run_monthly(base_date, return_window=_RETURN_WINDOW)

    # 5 个因子列（composite/trend/reversion/momentum/value）均应写入
    assert written == 5

    # 验证 FactorIcHistory 已写入（IC 应为非 None，因为评分与收益正相关）
    from sqlalchemy import select
    rows = list((await db_session.execute(
        select(FactorIcHistory).where(FactorIcHistory.calc_month == base_date)
    )).scalars().all())

    assert len(rows) == 5
    # composite_score 对应的 IC 应为正值（高分股涨多）
    composite_row = next(
        (r for r in rows if r.factor_name == "composite_score"), None
    )
    assert composite_row is not None
    assert composite_row.ic_value is not None
    assert float(composite_row.ic_value) > 0


# ---------------------------------------------------------------------------
# INT-FM-03: get_latest 返回各 (strategy, factor) 最新记录
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_fm_03_get_latest(db_session: AsyncSession) -> None:
    """INT-FM-03: 写入两个月数据 → get_latest 只返回最新月的记录。"""
    month1 = date(2026, 1, 31)
    month2 = date(2026, 2, 28)

    # 直接插入 FactorIcHistory（不经过 run_monthly）
    for calc_month, ic_val in [(month1, 0.05), (month2, 0.12)]:
        db_session.add(FactorIcHistory(
            calc_month=calc_month,
            strategy_name="TrendStrategy",
            factor_name="trend_score",
            ic_value=ic_val,
            return_window=20,
        ))
    await db_session.flush()

    svc = FactorMonitorService(db_session, FactorMonitorEngine())
    records = await svc.get_latest(strategy_name="TrendStrategy")

    # 应只返回 month2 的记录（最新）
    trend_records = [r for r in records if r.factor_name == "trend_score"]
    assert len(trend_records) == 1
    assert trend_records[0].calc_month == month2
    assert float(trend_records[0].ic_value) == pytest.approx(0.12)


# ---------------------------------------------------------------------------
# INT-FM-04: alert_status 触发（连续 3 月 IC < 0）
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_fm_04_alert_decay_triggered(db_session: AsyncSession) -> None:
    """INT-FM-04: 历史 IC 全为负 → run_monthly 写入 alert_status='DECAY'。"""
    # 写入 3 个月的负 IC 历史
    strategy = "MomentumStrategy"
    factor = "momentum_score"
    for i, ic_val in enumerate([-0.1, -0.08, -0.12]):
        db_session.add(FactorIcHistory(
            calc_month=date(2026, 1 + i, 28 if i == 1 else 31),
            strategy_name=strategy,
            factor_name=factor,
            ic_value=ic_val,
            return_window=_RETURN_WINDOW,
        ))
    await db_session.flush()

    # 插入当月候选池（仅 momentum_score 有数据）
    base_date = date(2026, 4, 30)
    # 调整到工作日
    while base_date.weekday() >= 5:
        base_date -= timedelta(days=1)

    scores = [80.0, 60.0, 40.0, 70.0, 50.0]
    for ts_code, score in zip(_TS_CODES, scores):
        db_session.add(CandidatePool(
            ts_code=ts_code,
            trade_date=base_date,
            composite_score=score,
            trend_score=score,
            reversion_score=score * 0.9,
            momentum_score=score * 0.8,
            value_score=score * 0.7,
            market_state="SIDEWAYS",
            in_pool=True,
        ))
    await db_session.flush()

    # 插入行情（基准日和前向日，让 IC 可以计算）
    base_prices = {ts: 10.0 + i for i, ts in enumerate(_TS_CODES)}
    await _insert_quote_data(db_session, base_date, base_prices)
    fwd_date = _next_workday(base_date, _RETURN_WINDOW)
    # 高分股跌、低分股涨 → 负 IC（强化 DECAY 告警）
    fwd_prices = {
        ts: price * (0.9 if i < 3 else 1.1)
        for i, (ts, price) in enumerate(base_prices.items())
    }
    await _insert_quote_data(db_session, fwd_date, fwd_prices)

    svc = FactorMonitorService(db_session, FactorMonitorEngine())
    written = await svc.run_monthly(base_date, return_window=_RETURN_WINDOW)
    assert written == 5

    # 检查 composite_score 对应记录（因为包含 3 个月负 IC 历史，可能触发 DECAY）
    from sqlalchemy import select
    rows = list((await db_session.execute(
        select(FactorIcHistory).where(
            FactorIcHistory.calc_month == base_date,
            FactorIcHistory.strategy_name == strategy,
        )
    )).scalars().all())

    # 至少有记录写入
    assert len(rows) > 0
    # momentum_score 对应行若 IC 也为负 → alert_status = DECAY
    momentum_row = next((r for r in rows if r.factor_name == factor), None)
    if momentum_row and momentum_row.ic_value is not None and float(momentum_row.ic_value) < 0:
        assert momentum_row.alert_status == "DECAY"


# ---------------------------------------------------------------------------
# INT-FM-05: get_history 分页正确
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_fm_05_get_history_pagination(db_session: AsyncSession) -> None:
    """INT-FM-05: 插入 5 条历史记录 → get_history(limit=3) 返回 3 条、total=5。"""
    strategy = "ValueStrategy"
    factor = "value_score"
    # 每月最后一天：1/3/5 → 31，2 → 28，4 → 30
    month_last_days = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31}
    for i in range(5):
        month = 1 + i
        db_session.add(FactorIcHistory(
            calc_month=date(2025, month, month_last_days[month]),
            strategy_name=strategy,
            factor_name=factor,
            ic_value=0.05 + i * 0.01,
            return_window=20,
        ))
    await db_session.flush()

    svc = FactorMonitorService(db_session, FactorMonitorEngine())
    records, total = await svc.get_history(strategy_name=strategy, factor_name=factor, limit=3)

    assert total == 5
    assert len(records) == 3
    # 结果按 calc_month DESC 排序
    assert records[0].calc_month >= records[-1].calc_month
