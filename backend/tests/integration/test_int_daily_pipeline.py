"""INT-DP-01~03: DailyPipeline 集成测试（需真实 PostgreSQL）。"""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from quantpilot.models.account import Account, DailyPortfolioValue, Position
from quantpilot.models.market import DailyQuote
from quantpilot.models.system import PipelineRun
from quantpilot.pipeline.daily_pipeline import DailyPipeline

# ---------------------------------------------------------------------------
# 测试常量（使用独特前缀避免与其他测试数据冲突）
# ---------------------------------------------------------------------------
_DATE_01 = date(2026, 3, 10)  # INT-DP-01 全流程
_DATE_02 = date(2026, 3, 11)  # INT-DP-02 断点续传
_DATE_03 = date(2026, 3, 12)  # INT-DP-03 mark_to_market
_TS_CODE = "INTDP1.SZ"        # INT-DP-03 专用股票代码


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

def _make_pipeline(factory: async_sessionmaker) -> DailyPipeline:
    """创建 DailyPipeline（adapter/calendar 均为 MagicMock）。"""
    return DailyPipeline(
        session_factory=factory,
        adapter=MagicMock(),
        validator=MagicMock(),
        calendar=MagicMock(),
    )


def _make_ingest_result() -> MagicMock:
    r = MagicMock()
    r.quote_count = 10
    r.financial_count = 5
    return r


async def _delete_pipeline_run(factory: async_sessionmaker, trade_date: date) -> None:
    """清理测试产生的 PipelineRun 记录。"""
    async with factory() as session:
        async with session.begin():
            result = await session.execute(
                select(PipelineRun).where(PipelineRun.trade_date == trade_date)
            )
            run = result.scalar_one_or_none()
            if run is not None:
                await session.delete(run)


# ---------------------------------------------------------------------------
# INT-DP-01: 全流程 → PipelineRun status=SUCCESS，三个 CP 均完成
# ---------------------------------------------------------------------------

async def test_int_dp_01_full_pipeline_success(db_engine: AsyncEngine) -> None:
    """INT-DP-01: 全流程（mock Tushare）→ PipelineRun status=SUCCESS，三 CP 均标记完成。"""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    pipeline = _make_pipeline(factory)

    # 准备 mock 对象（所有 service 均 mock，仅让 PipelineRun 写入真实 DB）
    mock_ds = AsyncMock()
    mock_ds.ingest_daily.return_value = _make_ingest_result()
    mock_ds.fetch_dividends.return_value = 0

    mock_scoring = AsyncMock()
    mock_scoring.run_daily_scoring.return_value = []

    mock_signal = AsyncMock()
    mock_signal.generate_for_date.return_value = []
    mock_signal.expire_old_signals.return_value = 0

    mock_account = AsyncMock()
    mock_account.mark_to_market.return_value = []

    try:
        with (
            patch("quantpilot.services.data_service.DataService", return_value=mock_ds),
            patch(
                "quantpilot.services.market_state_service.MarketStateService",
                return_value=AsyncMock(),
            ),
            patch(
                "quantpilot.services.strategy_service.ScoringService",
                return_value=mock_scoring,
            ),
            patch(
                "quantpilot.services.signal_service.SignalService",
                return_value=mock_signal,
            ),
            patch(
                "quantpilot.services.account_service.AccountService",
                return_value=mock_account,
            ),
        ):
            run = await pipeline.run(_DATE_01)
    finally:
        await _delete_pipeline_run(factory, _DATE_01)

    assert run.status == "SUCCESS"
    assert run.cp1_data_ready is True
    assert run.cp2_scoring_done is True
    assert run.cp3_signals_done is True
    assert run.data_snapshot_version is not None


# ---------------------------------------------------------------------------
# INT-DP-02: 断点续传（cp1_data_ready=True）→ CP1 跳过，CP2/CP3 执行
# ---------------------------------------------------------------------------

async def test_int_dp_02_checkpoint_resume(db_engine: AsyncEngine) -> None:
    """INT-DP-02: 预写 PipelineRun(cp1_data_ready=True) → CP1 跳过，CP2/CP3 完成。"""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    pipeline = _make_pipeline(factory)

    # 预先写入已完成 CP1 的 PipelineRun（模拟之前已跑完 CP1 的断点）
    existing_version = "20260311T090000Z"
    async with factory() as session:
        async with session.begin():
            session.add(PipelineRun(
                trade_date=_DATE_02,
                status="RUNNING",
                started_at=datetime.now(tz=timezone.utc),
                cp1_data_ready=True,
                cp1_at=datetime.now(tz=timezone.utc),
                data_snapshot_version=existing_version,
            ))

    mock_ds = AsyncMock()
    mock_ds.ingest_daily.return_value = _make_ingest_result()
    mock_ds.fetch_dividends.return_value = 0

    mock_scoring = AsyncMock()
    mock_scoring.run_daily_scoring.return_value = []

    mock_signal = AsyncMock()
    mock_signal.generate_for_date.return_value = []
    mock_signal.expire_old_signals.return_value = 0

    mock_account = AsyncMock()
    mock_account.mark_to_market.return_value = []

    try:
        with (
            patch("quantpilot.services.data_service.DataService", return_value=mock_ds),
            patch(
                "quantpilot.services.market_state_service.MarketStateService",
                return_value=AsyncMock(),
            ),
            patch(
                "quantpilot.services.strategy_service.ScoringService",
                return_value=mock_scoring,
            ),
            patch(
                "quantpilot.services.signal_service.SignalService",
                return_value=mock_signal,
            ),
            patch(
                "quantpilot.services.account_service.AccountService",
                return_value=mock_account,
            ),
        ):
            run = await pipeline.run(_DATE_02)
    finally:
        await _delete_pipeline_run(factory, _DATE_02)

    # CP1 已完成 → ingest_daily 不应被调用（跳过了 CP1）
    mock_ds.ingest_daily.assert_not_called()

    # CP2/CP3 应执行
    mock_scoring.run_daily_scoring.assert_called_once()
    mock_signal.generate_for_date.assert_called_once()

    assert run.status == "SUCCESS"
    assert run.cp1_data_ready is True
    assert run.cp2_scoring_done is True
    assert run.cp3_signals_done is True
    # data_snapshot_version 保持原值（CP1 未重跑）
    assert run.data_snapshot_version == existing_version


# ---------------------------------------------------------------------------
# INT-DP-03: mark_to_market 写入 daily_portfolio_value
# ---------------------------------------------------------------------------

async def test_int_dp_03_mark_to_market_writes_dpv(db_engine: AsyncEngine) -> None:
    """INT-DP-03: 预写 Account+Position+DailyQuote → mark_to_market 写入 daily_portfolio_value。"""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    pipeline = _make_pipeline(factory)

    # 插入真实 Account / Position / DailyQuote（让 mark_to_market 有数据可处理）
    account_id: int = -1
    async with factory() as session:
        async with session.begin():
            account = Account(name="INT-DP-03 账户", account_type="PAPER", cash=50000.0)
            session.add(account)
            await session.flush()
            account_id = account.id

            session.add(Position(
                account_id=account_id,
                ts_code=_TS_CODE,
                shares=1000,
                cost_price=10.0,
                current_price=10.0,
                market_value=10000.0,
            ))
            session.add(DailyQuote(
                ts_code=_TS_CODE,
                trade_date=_DATE_03,
                open=11.0,
                high=11.5,
                low=10.8,
                close=11.2,
                vol=500000,
                amount=5600000.0,
                adj_factor=1.0,
            ))

    mock_ds = AsyncMock()
    mock_ds.ingest_daily.return_value = _make_ingest_result()
    mock_ds.fetch_dividends.return_value = 0

    mock_scoring = AsyncMock()
    mock_scoring.run_daily_scoring.return_value = []

    mock_signal = AsyncMock()
    mock_signal.generate_for_date.return_value = []
    mock_signal.expire_old_signals.return_value = 0

    try:
        with (
            patch("quantpilot.services.data_service.DataService", return_value=mock_ds),
            patch(
                "quantpilot.services.market_state_service.MarketStateService",
                return_value=AsyncMock(),
            ),
            patch(
                "quantpilot.services.strategy_service.ScoringService",
                return_value=mock_scoring,
            ),
            patch(
                "quantpilot.services.signal_service.SignalService",
                return_value=mock_signal,
            ),
            # 注意：不 mock AccountService，让 mark_to_market 真实执行
        ):
            run = await pipeline.run(_DATE_03)

        # 验证 daily_portfolio_value 已写入
        async with factory() as session:
            result = await session.execute(
                select(DailyPortfolioValue).where(
                    DailyPortfolioValue.account_id == account_id,
                    DailyPortfolioValue.trade_date == _DATE_03,
                )
            )
            dpv = result.scalar_one_or_none()

        assert dpv is not None
        assert dpv.account_id == account_id
        assert float(dpv.position_value) == pytest.approx(11.2 * 1000, rel=1e-3)
        assert float(dpv.cash) == pytest.approx(50000.0, rel=1e-3)

    finally:
        # 清理：按依赖顺序删除（daily_portfolio_value → position → daily_quote → account）
        async with factory() as session:
            async with session.begin():
                # DailyPortfolioValue（ON DELETE CASCADE 会自动删，但 account 删后才触发）
                await session.execute(
                    select(DailyPortfolioValue).where(
                        DailyPortfolioValue.account_id == account_id
                    )
                )
                dpv_result = await session.execute(
                    select(DailyPortfolioValue).where(
                        DailyPortfolioValue.account_id == account_id
                    )
                )
                for row in dpv_result.scalars().all():
                    await session.delete(row)

                pos_result = await session.execute(
                    select(Position).where(Position.account_id == account_id)
                )
                for row in pos_result.scalars().all():
                    await session.delete(row)

                quote_result = await session.execute(
                    select(DailyQuote).where(
                        DailyQuote.ts_code == _TS_CODE,
                        DailyQuote.trade_date == _DATE_03,
                    )
                )
                for row in quote_result.scalars().all():
                    await session.delete(row)

                run_result = await session.execute(
                    select(PipelineRun).where(PipelineRun.trade_date == _DATE_03)
                )
                run_row = run_result.scalar_one_or_none()
                if run_row is not None:
                    await session.delete(run_row)

                account_result = await session.execute(
                    select(Account).where(Account.id == account_id)
                )
                account_row = account_result.scalar_one_or_none()
                if account_row is not None:
                    await session.delete(account_row)

    assert run.status == "SUCCESS"
