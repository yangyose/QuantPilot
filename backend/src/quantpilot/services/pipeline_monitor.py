"""流水线监控护栏（2026-07 生产事故复盘产出）。

事故：financial_data 重复报告期污染 F-5 → 候选池近翻倍（2290→4379）→ 2GB 机
CP2 评分挂起 → pipeline_run 长期停 RUNNING，连续 12 交易日无信号，用户静默 3 周。
两个信号本可在事故当天暴露，却都缺告警。本模块补两类 best-effort 护栏，均走
NotificationService.notify_health_alert（站内信 + WxPusher，24h 去重）：

- check_pool_size_anomaly：候选池规模较上一交易日突变 > 阈值 → 告警。硬过滤（F-5
  等）失效或数据污染会使池规模跳变，此护栏令异常当天即暴露。
- scan_stuck_runs：pipeline_run 停 RUNNING 超 STUCK_RUN_MINUTES 且未 finished →
  告警。由独立调度 job 周期运行，**不依赖已挂起的管线自报**（挂起的进程无法告警）。

阈值暂用模块常量（sensible default）；后续如需运营可调，再提升为 config_key。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.repository import MarketDataRepository
from quantpilot.models.system import PipelineRun
from quantpilot.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

# 候选池规模较上一交易日的相对变动阈值（±50%）。事故当天 2290→4379 = +91% 会触发。
POOL_ANOMALY_THRESHOLD = 0.5
# RUNNING 超过此分钟数且未 finished_at → 判定卡死。正常单次 ~13-17min，45min 为宽松上界。
STUCK_RUN_MINUTES = 45


async def check_pool_size_anomaly(
    repo: MarketDataRepository,
    notifier: NotificationService,
    trade_date: date,
    *,
    threshold: float = POOL_ANOMALY_THRESHOLD,
) -> bool:
    """候选池规模突变检测（best-effort）。触发告警返回 True，否则 False。

    与「≤ trade_date-1 的最近一个有池数据的交易日」对比，相对变动 > threshold 即告警。
    无历史池 / 上一日池为空 → 无可比基准，返回 False。异常内部吞并记 WARNING（不影响
    调用方管线）。
    """
    try:
        current = len(await repo.get_pool_codes(trade_date))
        prev_date = await repo.get_latest_pool_date(trade_date - timedelta(days=1))
        if prev_date is None:
            return False
        prev = len(await repo.get_pool_codes(prev_date))
        if prev == 0:
            return False
        delta = abs(current - prev) / prev
        if delta <= threshold:
            return False
        await notifier.notify_health_alert(
            "candidate_pool_anomaly",
            f"候选池规模异常：{trade_date} = {current} 只，较上一交易日 {prev_date} = "
            f"{prev} 只变动 {delta:.0%}（阈值 {threshold:.0%}）。"
            "可能是硬过滤（如 F-5 连续亏损）失效或财务数据污染，请核查。",
            payload={
                "trade_date": str(trade_date),
                "current": current,
                "prev_date": str(prev_date),
                "prev": prev,
                "delta_pct": round(delta, 4),
            },
        )
        logger.warning(
            "candidate_pool_anomaly: trade_date=%s current=%d prev_date=%s prev=%d delta=%.2f",
            trade_date, current, prev_date, prev, delta,
        )
        return True
    except Exception:
        logger.warning(
            "pool_anomaly_check_failed: trade_date=%s", trade_date, exc_info=True
        )
        return False


async def scan_stuck_runs(
    session: AsyncSession,
    notifier: NotificationService,
    *,
    now: datetime | None = None,
    stuck_minutes: int = STUCK_RUN_MINUTES,
) -> int:
    """扫描卡死的 RUNNING pipeline_run 并逐条告警。返回卡死 run 数。

    卡死判定：status='RUNNING' 且 started_at 早于 now-stuck_minutes 且 finished_at IS NULL。
    每条告警 payload 含 run_id → NotificationService 24h 去重使同一卡死 run 每天至多告警
    一次（周期 job 反复扫描不会刷屏）。单条告警失败不影响其余（逐条 try）。
    """
    now = now or datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(minutes=stuck_minutes)
    result = await session.execute(
        select(PipelineRun).where(
            PipelineRun.status == "RUNNING",
            PipelineRun.started_at < cutoff,
            PipelineRun.finished_at.is_(None),
        )
    )
    stuck = list(result.scalars().all())
    for run in stuck:
        try:
            elapsed = int((now - run.started_at).total_seconds() // 60)
            await notifier.notify_health_alert(
                "pipeline_stuck",
                f"流水线卡死：交易日 {run.trade_date}（run id={run.id}）已 RUNNING "
                f"{elapsed} 分钟未完成（阈值 {stuck_minutes} 分钟）。请检查 CP2 评分是否"
                "挂起（候选池膨胀/内存不足）或数据源阻塞。",
                payload={
                    "run_id": run.id,
                    "trade_date": str(run.trade_date),
                    "elapsed_minutes": elapsed,
                },
            )
        except Exception:
            logger.warning("stuck_run_alert_failed: run_id=%d", run.id, exc_info=True)
    if stuck:
        logger.warning("pipeline_stuck_runs_detected: count=%d", len(stuck))
    return len(stuck)
