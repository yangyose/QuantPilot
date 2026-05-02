"""INT-PS-01~03：PerformanceService 集成测试（需真实 PostgreSQL）。"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.account import Account, DailyPortfolioValue, FundFlow, TradeRecord
from quantpilot.models.business import Signal, SignalScoreSnapshot
from quantpilot.services.performance_service import PerformanceService

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

_TRADE_DATE_1 = date(2026, 1, 3)
_TRADE_DATE_2 = date(2026, 1, 10)
_TS_A = "INTPS1.SZ"
_TS_B = "INTPS2.SZ"


async def _make_account(session: AsyncSession, cash: float = 1_000_000.0) -> Account:
    account = Account(name="绩效测试账户", account_type="REAL", cash=cash, total_assets=cash)
    session.add(account)
    await session.flush()
    await session.refresh(account)
    return account


# ---------------------------------------------------------------------------
# INT-PS-01：get_summary 返回 7 项指标
# ---------------------------------------------------------------------------

async def test_int_ps_01_get_summary_returns_metrics(db_session: AsyncSession) -> None:
    """INT-PS-01：插入 account + fund_flow + daily_portfolio_value → get_summary 返回 7 项指标。"""
    account = await _make_account(db_session, cash=1_000_000.0)

    # 插入资金流水（DEPOSIT 100万）
    db_session.add(FundFlow(
        account_id=account.id,
        flow_type="DEPOSIT",
        amount=1_000_000.0,
        trade_date=_TRADE_DATE_1,
    ))

    # 插入 2 条净值曲线（涨了 10%）
    db_session.add(DailyPortfolioValue(
        account_id=account.id,
        trade_date=_TRADE_DATE_1,
        total_value=1_000_000.0,
        cash=1_000_000.0,
        position_value=0.0,
    ))
    db_session.add(DailyPortfolioValue(
        account_id=account.id,
        trade_date=_TRADE_DATE_2,
        total_value=1_100_000.0,
        cash=500_000.0,
        position_value=600_000.0,
    ))
    await db_session.flush()

    svc = PerformanceService(db_session)
    result = await svc.get_summary(account_id=account.id)

    assert result is not None
    # 7 项必须全部存在
    required_keys = {
        "cumulative_return", "annualized_return", "max_drawdown",
        "sharpe_ratio", "win_rate", "profit_loss_ratio", "benchmark_return",
    }
    assert required_keys.issubset(result.keys())
    # cumulative_return = (1100000 - 1000000) / 1000000 = 0.1
    assert result["cumulative_return"] == pytest.approx(0.1, rel=1e-4)


# ---------------------------------------------------------------------------
# INT-PS-02：get_attribution.by_strategy 按 score_breakdown 正确聚合
# ---------------------------------------------------------------------------

async def test_int_ps_02_attribution_by_strategy(db_session: AsyncSession) -> None:
    """INT-PS-02：trade_record + SignalScoreSnapshot → get_attribution.by_strategy 正确聚合。"""
    account = await _make_account(db_session)

    # 插入信号
    signal = Signal(
        ts_code=_TS_A,
        signal_type="BUY",
        trade_date=_TRADE_DATE_1,
        score=85.0,
    )
    db_session.add(signal)
    await db_session.flush()

    # 插入快照（TrendStrategy 最高分）
    db_session.add(SignalScoreSnapshot(
        signal_id=signal.id,
        trade_date=_TRADE_DATE_1,
        ts_code=_TS_A,
        composite_score=85.0,
        score_breakdown={"TrendStrategy": 90, "MomentumStrategy": 60},
    ))

    # 插入成交记录（BUY + SELL，指向同一信号）
    db_session.add(TradeRecord(
        account_id=account.id,
        ts_code=_TS_A,
        trade_type="BUY",
        trade_date=_TRADE_DATE_1,
        price=10.0,
        shares=1000,
        amount=10_000.0,
        signal_id=signal.id,
    ))
    db_session.add(TradeRecord(
        account_id=account.id,
        ts_code=_TS_A,
        trade_type="SELL",
        trade_date=_TRADE_DATE_2,
        price=11.0,
        shares=1000,
        amount=11_000.0,
        signal_id=signal.id,
    ))
    await db_session.flush()

    svc = PerformanceService(db_session)
    result = await svc.get_attribution(
        account_id=account.id,
        period_start=_TRADE_DATE_1,
        period_end=_TRADE_DATE_2,
    )

    assert "by_strategy" in result
    # 主导策略为 TrendStrategy（score=90 最高）
    strategies = {s["strategy_name"]: s for s in result["by_strategy"]}
    assert "TrendStrategy" in strategies


# ---------------------------------------------------------------------------
# INT-PS-03：get_behavioral_analysis.signal_compliance_rate
# ---------------------------------------------------------------------------

async def test_int_ps_03_signal_compliance_rate(db_session: AsyncSession) -> None:
    """INT-PS-03：一半 trade_record 有 signal_id → signal_compliance_rate = 0.5。"""
    account = await _make_account(db_session)

    # 插入一个信号
    signal = Signal(
        ts_code=_TS_A,
        signal_type="BUY",
        trade_date=_TRADE_DATE_1,
        score=80.0,
    )
    db_session.add(signal)
    await db_session.flush()

    # 有 signal_id 的成交
    db_session.add(TradeRecord(
        account_id=account.id,
        ts_code=_TS_A,
        trade_type="BUY",
        trade_date=_TRADE_DATE_1,
        price=10.0,
        shares=1000,
        amount=10_000.0,
        signal_id=signal.id,
    ))
    # 无 signal_id 的成交（手动录入）
    db_session.add(TradeRecord(
        account_id=account.id,
        ts_code=_TS_B,
        trade_type="BUY",
        trade_date=_TRADE_DATE_2,
        price=20.0,
        shares=500,
        amount=10_000.0,
        signal_id=None,
    ))
    await db_session.flush()

    svc = PerformanceService(db_session)
    result = await svc.get_behavioral_analysis(account_id=account.id)

    assert result["signal_compliance_rate"] == pytest.approx(0.5, abs=1e-9)
