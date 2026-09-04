"""INT-PSR-01~03：收盘后私有信号复评 Job 的接线（硬止损一日延迟修复，2026-09-04）。

## 与 `test_int_signal_private.py` 的分工

那个文件覆盖 `evaluate_private_signals` **函数**层（INT-G4D3-01 断言 hard_stop_loss
会浮出）。本文件覆盖的是**新 Job 的接线**——函数产出信号 ≠ 用户收到通知，
中间隔着 Job 调度、新鲜度守卫、共用 helper、NotificationService、真实落库。
生产实证正是断在这一段：`evaluate_private_signals` 一直是好的，
但 `RISK_WARN` 71 条里 `hard_stop_loss` **0 条**（自 2026-06-28）。

## 为什么用「今天」而不是固定日期

Job 内部取 `datetime.now(Asia/Shanghai).date()`，不接受注入的日期——这本身是
对的（生产就该按真实今天跑）。故 seed 跟着今天走，而不是去 patch 时间。

⚠️ 本文件走**真 commit** 路径（Job 自建 session）。按 CLAUDE.md §4.11，
写出去的行必须在 finally 清干净，否则泄漏给同库中按字母序后跑的**别的测试文件**
——本地只跑本文件抓不到。

**要清的是「本文件写过的每一张表」，不是「想得起来的那几张」**：
`stock_info` / `daily_quote` / `candidate_pool` / `market_state_history` /
`account` / `position` / `pipeline_run` / `in_app_notification` 共 8 张。
初版只清了其中 5 张（漏 `market_state_history` 与 `candidate_pool`，
两者都不以 `_TS` 命名、也不挂 account，写清理时最容易想不到），
结果 `test_market_state_service.py` 5 个用例集体转红。详见 `_cleanup` docstring。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.market_state import MarketStateEnum, MarketStateRecord
from quantpilot.models.account import Account, Position
from quantpilot.models.business import (
    CandidatePool,
    InAppNotification,
    MarketStateHistory,
)
from quantpilot.models.market import DailyQuote, StockInfo
from quantpilot.models.system import PipelineRun
from quantpilot.pipeline.scheduler import _private_signal_recheck_job
from tests.integration._helpers import seeded_user_id

_TS = "PSR001.SZ"


def _today() -> date:
    return datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()


def _calendar() -> MagicMock:
    cal = MagicMock()
    cal.is_trade_date.return_value = True
    return cal


async def _seed(session: AsyncSession, day: date, *, pipeline_status: str | None) -> int:
    """建一个「浮亏 14.6% 且评分处于中性区」的持仓场景，返回 account_id。

    评分取 pct=0.30：既不进 BUY、也不触 pct_above_sell（阈值 0.70）
    → 只剩 hard_stop_loss 一条可能，避免用例被别的触发器混淆。
    """
    session.add(StockInfo(
        ts_code=_TS, name="复评测试", sw_industry_l1="银行", market="MAIN",
        list_date=date(2020, 1, 1), is_active=True,
    ))
    session.add(DailyQuote(
        ts_code=_TS, trade_date=day, open=11.6, high=11.7, low=11.5,
        close=11.61, pre_close=12.52, pct_chg=-7.3,
        vol=1_000_000, amount=10_000_000.0, adj_factor=1.0,
        is_suspended=False, is_st=False, limit_up=False, limit_down=False,
    ))
    await session.flush()

    repo = MarketDataRepository(session)
    await repo.upsert_candidate_pool_bulk([{
        "ts_code": _TS, "trade_date": day, "composite_score": 55.0,
        "trend_score": 55.0, "momentum_score": 55.0,
        "reversion_score": 55.0, "value_score": 55.0,
        "market_state": "OSCILLATION", "in_pool": True, "is_holding": True,
        "composite_z": 0.5, "composite_pct_in_market": 0.30,
        "weights_source": "icir", "hysteresis_status": "stable",
    }])
    await repo.upsert_market_state(MarketStateRecord(
        trade_date=day, market_state=MarketStateEnum.OSCILLATION,
        trend_strength=20.0, adx_value=20.0, ma20=11.0, ma60=11.0,
        state_changed=False, description="seed",
    ))

    acc = Account(
        user_id=await seeded_user_id(session), name="复评测试账户",
        account_type="REAL", broker="MOCK", total_assets=500_000.0, cash=400_000.0,
    )
    session.add(acc)
    await session.flush()
    session.add(Position(
        account_id=acc.id, ts_code=_TS, shares=3900,
        cost_price=13.594, current_price=11.61,
        market_value=45_279.0, pnl_pct=-0.1459,   # 生产 001258.SZ 的真实数字
    ))
    if pipeline_status is not None:
        session.add(PipelineRun(
            trade_date=day, status=pipeline_status,
            cp1_data_ready=True, cp2_scoring_done=True, cp3_signals_done=True,
        ))
    await session.flush()
    return acc.id


async def _cleanup(factory: async_sessionmaker, day: date, account_id: int | None) -> None:
    """清掉本文件真 commit 出去的**全部**副作用行。

    ⚠️ `market_state_history` 是最容易漏的一张——它不以 `_TS` 命名、也不挂 account，
    很难在写清理时联想到。漏了的后果不在本文件：`test_market_state_service.py`
    按字母序排在本文件**之后**，读「最新市场状态」时会读到这里 seed 的今日行，
    5 个用例集体转红（2026-09-04 实际发生）。单独跑本文件或单独跑它都全绿，
    只有全量集成才暴露——正是 CLAUDE.md §4.11「副作用表泄漏」那条的标准形态。
    """
    async with factory() as s:
        for model, cond in (
            (InAppNotification, InAppNotification.account_id == account_id),
            (Position, Position.ts_code == _TS),
            (PipelineRun, PipelineRun.trade_date == day),
            (MarketStateHistory, MarketStateHistory.trade_date == day),
            (CandidatePool, CandidatePool.trade_date == day),
            (DailyQuote, DailyQuote.ts_code == _TS),
            (StockInfo, StockInfo.ts_code == _TS),
        ):
            for row in (await s.execute(select(model).where(cond))).scalars().all():
                await s.delete(row)
        await s.commit()
    async with factory() as s:
        for row in (
            await s.execute(select(Account).where(Account.id == account_id))
        ).scalars().all():
            await s.delete(row)
        await s.commit()


# ============================================================
# INT-PSR-01：盯市已完成 → Job 把 hard_stop_loss 真正写成通知
# ============================================================
async def test_int_psr_01_job_persists_hard_stop_loss_notification(
    db_engine: AsyncEngine,
) -> None:
    """端到端：持仓越阈值 + 当日管线 SUCCESS → `in_app_notification` 落一行。

    这是生产从未走通过的那一段——函数层一直是对的，通知却一条都没有。
    """
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    day = _today()
    acc_id: int | None = None
    try:
        async with factory() as s:
            acc_id = await _seed(s, day, pipeline_status="SUCCESS")
            await s.commit()

        await _private_signal_recheck_job(factory, _calendar(), None, None)

        async with factory() as s:
            rows = (
                await s.execute(
                    select(InAppNotification).where(
                        InAppNotification.account_id == acc_id
                    )
                )
            ).scalars().all()

        assert rows, "复评 Job 未产出任何通知——正是生产 hard_stop_loss 0 条的形态"
        hard = [r for r in rows if "硬止损" in (r.body or "")]
        assert len(hard) == 1, f"应恰有 1 条硬止损通知，实得 {[r.title for r in rows]}"
        assert hard[0].notify_type == "RISK_WARN"
        assert hard[0].payload.get("ts_code") == _TS
        # payload 带日期：同日两次评估靠它被 24h 窗口去重，跨日则各自成立
        assert hard[0].payload.get("date") == str(day)
    finally:
        await _cleanup(factory, day, acc_id)


# ============================================================
# INT-PSR-02：盯市未完成 → 守卫拦下，且不静默
# ============================================================
async def test_int_psr_02_guard_blocks_when_pipeline_not_success(
    db_engine: AsyncEngine, caplog
) -> None:
    """管线 RUNNING（= 2026-09-03 那次 OOM 后的真实状态）→ 不评估、留 WARNING。

    此时 `position.current_price` 还是前一交易日的价。若照常评估，会写一条
    payload 带**今日**日期的通知，把 24h 去重窗口占掉 → 次日那条正确的反被挡住。
    """
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    day = _today()
    acc_id: int | None = None
    try:
        async with factory() as s:
            acc_id = await _seed(s, day, pipeline_status="RUNNING")
            await s.commit()

        with caplog.at_level(logging.WARNING):
            await _private_signal_recheck_job(factory, _calendar(), None, None)

        async with factory() as s:
            rows = (
                await s.execute(
                    select(InAppNotification).where(
                        InAppNotification.account_id == acc_id
                    )
                )
            ).scalars().all()
        assert rows == [], "盯市未完成时不应产出通知"
        assert "private_signal_recheck_skipped" in caplog.text, "跳过必须留痕（C-4）"
    finally:
        await _cleanup(factory, day, acc_id)


# ============================================================
# INT-PSR-03：当日无 pipeline_run 行 → 同样拦下（缺行 ≠ 成功）
# ============================================================
async def test_int_psr_03_guard_blocks_when_no_pipeline_row(
    db_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    day = _today()
    acc_id: int | None = None
    try:
        async with factory() as s:
            acc_id = await _seed(s, day, pipeline_status=None)
            await s.commit()

        await _private_signal_recheck_job(factory, _calendar(), None, None)

        async with factory() as s:
            rows = (
                await s.execute(
                    select(InAppNotification).where(
                        InAppNotification.account_id == acc_id
                    )
                )
            ).scalars().all()
        assert rows == []
    finally:
        await _cleanup(factory, day, acc_id)
