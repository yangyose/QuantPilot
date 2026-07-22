"""INT-MON-01~05: 流水线监控护栏（2026-07 生产事故复盘产出）。

两类护栏（services/pipeline_monitor.py）：
- check_pool_size_anomaly：候选池规模较上一交易日突变 > 阈值 → notify_health_alert。
  事故当天池 2290→4379(near 2x) 无告警，静默 3 周。此护栏令 F-5 崩塌当天即暴露。
- scan_stuck_runs：pipeline_run 停 RUNNING 超 N 分钟且未完成 → 告警。由独立调度 job
  周期运行，不依赖已挂起的管线自报（事故中 12 个 run 长期 RUNNING 无人知）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.repository import MarketDataRepository
from quantpilot.models.business import CandidatePool, InAppNotification
from quantpilot.models.system import PipelineRun
from quantpilot.services.config_service import ConfigService
from quantpilot.services.notification_service import NotificationService
from quantpilot.services.pipeline_monitor import (
    check_pool_size_anomaly,
    scan_stuck_runs,
)


def _notifier(session: AsyncSession) -> NotificationService:
    return NotificationService(session, ConfigService(session, None), None)


async def _count_health_alerts(session: AsyncSession, alert_type: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(InAppNotification)
        .where(
            InAppNotification.notify_type == "HEALTH_ALERT",
            InAppNotification.payload["alert_type"].astext == alert_type,
        )
    )
    return result.scalar_one()


def _pool_rows(trade_date: date, n: int, prefix: str) -> list[CandidatePool]:
    # ts_code 为 VARCHAR(10)：1 字符前缀 + 5 位序号 + ".SZ" = 9 字符
    return [
        CandidatePool(ts_code=f"{prefix}{i:05d}.SZ", trade_date=trade_date, in_pool=True)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# INT-MON-01: 候选池规模突变（3x）→ 触发告警
# ---------------------------------------------------------------------------

async def test_mon_01_pool_anomaly_triggers(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    d_prev, d_cur = date(2026, 4, 6), date(2026, 4, 7)
    db_session.add_all(_pool_rows(d_prev, 10, "P"))
    db_session.add_all(_pool_rows(d_cur, 30, "C"))  # 3x → delta 200% > 50%
    await db_session.flush()

    fired = await check_pool_size_anomaly(repo, _notifier(db_session), d_cur)

    assert fired is True
    assert await _count_health_alerts(db_session, "candidate_pool_anomaly") == 1


# ---------------------------------------------------------------------------
# INT-MON-02: 规模变动在阈值内（+20%）→ 不告警
# ---------------------------------------------------------------------------

async def test_mon_02_pool_within_threshold_no_alert(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    d_prev, d_cur = date(2026, 4, 13), date(2026, 4, 14)
    db_session.add_all(_pool_rows(d_prev, 100, "A"))
    db_session.add_all(_pool_rows(d_cur, 120, "B"))  # +20% ≤ 50%
    await db_session.flush()

    fired = await check_pool_size_anomaly(repo, _notifier(db_session), d_cur)

    assert fired is False
    assert await _count_health_alerts(db_session, "candidate_pool_anomaly") == 0


# ---------------------------------------------------------------------------
# INT-MON-03: 无历史池（首日）→ 不告警（无可比基准）
# ---------------------------------------------------------------------------

async def test_mon_03_pool_no_history_no_alert(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    d_cur = date(2026, 4, 20)
    db_session.add_all(_pool_rows(d_cur, 50, "D"))
    await db_session.flush()

    fired = await check_pool_size_anomaly(repo, _notifier(db_session), d_cur)

    assert fired is False
    assert await _count_health_alerts(db_session, "candidate_pool_anomaly") == 0


# ---------------------------------------------------------------------------
# INT-MON-04: RUNNING 超时（60min 前启动、未完成）→ 检出并告警
# ---------------------------------------------------------------------------

async def test_mon_04_stuck_run_detected(db_session: AsyncSession) -> None:
    now = datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc)
    db_session.add(PipelineRun(
        trade_date=date(2026, 4, 21),
        status="RUNNING",
        started_at=now - timedelta(minutes=60),
        finished_at=None,
    ))
    await db_session.flush()

    count = await scan_stuck_runs(db_session, _notifier(db_session), now=now)

    assert count == 1
    assert await _count_health_alerts(db_session, "pipeline_stuck") == 1


# ---------------------------------------------------------------------------
# INT-MON-05: 近期 RUNNING（5min）+ 已完成 SUCCESS → 均不告警
# ---------------------------------------------------------------------------

async def test_mon_05_recent_and_done_runs_ignored(db_session: AsyncSession) -> None:
    now = datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc)
    db_session.add(PipelineRun(
        trade_date=date(2026, 4, 22),
        status="RUNNING",
        started_at=now - timedelta(minutes=5),  # 未超阈值
        finished_at=None,
    ))
    db_session.add(PipelineRun(
        trade_date=date(2026, 4, 21),
        status="SUCCESS",
        started_at=now - timedelta(minutes=120),
        finished_at=now - timedelta(minutes=100),  # 已完成
    ))
    await db_session.flush()

    count = await scan_stuck_runs(db_session, _notifier(db_session), now=now)

    assert count == 0
    assert await _count_health_alerts(db_session, "pipeline_stuck") == 0
