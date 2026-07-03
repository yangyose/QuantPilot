"""APScheduler 配置：日度流水线 + 月末 + 周报 + 止损预警（Phase 7 + Phase 10 §5.5）。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from redis.asyncio import Redis as AsyncRedis
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from quantpilot.data.adapters.base import DataSourceAdapter
    from quantpilot.data.calendar import TradingCalendar
    from quantpilot.data.validators import DataValidator
    from quantpilot.notification.base import NotificationChannel

logger = logging.getLogger(__name__)

# Phase 10 §5.5：止损预警距离阈值（≤ 2%）
STOP_LOSS_WARN_THRESHOLD = 0.02


def create_scheduler(
    session_factory: async_sessionmaker,
    adapter: DataSourceAdapter,
    validator: DataValidator,
    calendar: TradingCalendar,
    *,
    redis: AsyncRedis | None = None,
    notification_channel: NotificationChannel | None = None,
) -> AsyncIOScheduler:
    """创建并配置 APScheduler。

    Jobs：
    - daily_pipeline：17:30 Asia/Shanghai，DailyPipeline.run（含 CP1/CP2/CP3/盯市/分红/过期）
    - monthly_job：每月最后一日 20:00，MonthlyScheduler.run_all（因子监控 + 月报）
    - weekly_report：每周六 09:00，周报生成
    - stop_loss_warn：每日 15:05（Phase 10 §5.5），扫描持仓距止损 ≤ 2% 的推送

    redis / notification_channel：Phase 10 新增，供 DailyPipeline 消费配置快照与通知链。

    Phase 10 §4.3 评审 C-01：移除 `market_state_engine` 形参——MarketStateEngine 由
    DailyPipeline.CP1 内基于 `run.config_snapshot.market_state_params` 即时实例化。
    """
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    scheduler.add_job(
        _daily_pipeline_job,
        trigger=CronTrigger(hour=17, minute=30, timezone="Asia/Shanghai"),
        args=[
            session_factory, adapter, validator, calendar,
            redis, notification_channel,
        ],
        id="daily_pipeline",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        _monthly_job,
        trigger=CronTrigger(day="last", hour=20, timezone="Asia/Shanghai"),
        args=[
            session_factory, adapter, validator, calendar,
            redis, notification_channel,
        ],
        id="monthly_job",
        replace_existing=True,
        misfire_grace_time=7200,
    )

    scheduler.add_job(
        _weekly_report_job,
        trigger=CronTrigger(day_of_week="sat", hour=9, timezone="Asia/Shanghai"),
        args=[session_factory],
        id="weekly_report",
        replace_existing=True,
        misfire_grace_time=7200,
    )

    # Phase 10 §5.5：止损预警 Job（每日 15:05 A股收盘后）
    scheduler.add_job(
        _stop_loss_warn_job,
        trigger=CronTrigger(hour=15, minute=5, timezone="Asia/Shanghai"),
        args=[session_factory, calendar, redis, notification_channel],
        id="stop_loss_warn",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    # 交易日历月度刷新 Job（每月 1 日 06:00）：向前滚动窗口刷新 trade_calendar，
    # 让次年日历发布后自动落库，保持 DB 优先日历常新（不依赖重启）。
    scheduler.add_job(
        _trade_calendar_refresh_job,
        trigger=CronTrigger(day=1, hour=6, timezone="Asia/Shanghai"),
        args=[session_factory, adapter],
        id="trade_calendar_refresh",
        replace_existing=True,
        misfire_grace_time=7200,
    )

    return scheduler


# ─── Job 函数 ─────────────────────────────────────────────────────────────────


async def _daily_pipeline_job(
    session_factory: async_sessionmaker,
    adapter: DataSourceAdapter,
    validator: DataValidator,
    calendar: TradingCalendar,
    redis: AsyncRedis | None,
    notification_channel: NotificationChannel | None,
) -> None:
    """日度流水线 Job。自建 session，避免全局 session 长期持有连接。"""
    from quantpilot.pipeline.daily_pipeline import DailyPipeline

    today = datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()
    if not calendar.is_trade_date(today):
        logger.info("daily_pipeline_skipped_non_trade_date: date=%s", today)
        return

    pipeline = DailyPipeline(
        session_factory=session_factory,
        adapter=adapter,
        validator=validator,
        calendar=calendar,
        redis=redis,
        notification_channel=notification_channel,
    )
    run = await pipeline.run(today)
    logger.info(
        "daily_pipeline_job_done: trade_date=%s status=%s signals=%s",
        run.trade_date, run.status, run.signal_count,
    )


async def _monthly_job(
    session_factory: async_sessionmaker,
    adapter: DataSourceAdapter,
    validator: DataValidator,
    calendar: TradingCalendar,
    redis: AsyncRedis | None,
    notification_channel: NotificationChannel | None,
) -> None:
    """月末 Job：因子监控 + 月报生成 + 季度财务补录（条件执行）。

    Phase 10 §7.3：注入 `redis` + `notification_channel` 使 MonthlyScheduler
    在因子告警触发时走 NotificationService/WxPusher 真实推送。
    """
    from quantpilot.data.repository import MarketDataRepository
    from quantpilot.engine.factor_monitor import FactorMonitorEngine
    from quantpilot.pipeline.monthly_scheduler import MonthlyScheduler

    today = datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()
    logger.info("monthly_job_start: trigger_date=%s", today)

    from quantpilot.services.data_service import DataService

    async with session_factory() as session:
        repo = MarketDataRepository(session)
        data_service = DataService(adapter, validator, repo, calendar)

        scheduler = MonthlyScheduler(
            data_service=data_service,
            session_factory=session_factory,
            calendar=calendar,
            factor_monitor_engine=FactorMonitorEngine(),
            redis=redis,
            notification_channel=notification_channel,
        )
        await scheduler.run_all(today)

    logger.info("monthly_job_done: trigger_date=%s", today)


async def _trade_calendar_refresh_job(
    session_factory: async_sessionmaker,
    adapter: DataSourceAdapter,
) -> None:
    """每月刷新 trade_calendar（向前滚动 ~6y 历史 + 90 天前瞻）。

    自建 session 显式 commit（asyncio.create_task / 调度 job 不走 get_db 自动 commit）。
    """
    from datetime import timedelta

    from quantpilot.data.repository import MarketDataRepository
    from quantpilot.services.data_service import bootstrap_trade_calendar

    today = datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()
    start = today - timedelta(days=365 * 6)
    end = today + timedelta(days=90)
    try:
        async with session_factory() as session:
            repo = MarketDataRepository(session)
            written = await bootstrap_trade_calendar(adapter, repo, start, end)
            await session.commit()
        logger.info(
            "trade_calendar_refresh_job_done: start=%s end=%s rows=%d",
            start, end, written,
        )
    except Exception:
        logger.exception("trade_calendar_refresh_job_failed")


async def _weekly_report_job(session_factory: async_sessionmaker) -> None:
    """周报 Job：生成上一自然周（Mon–Fri）的周报。"""
    from datetime import timedelta

    from quantpilot.services.report_service import ReportService

    today = datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()
    # 上周五 = 今日（周六）- 1 天
    week_end = today - timedelta(days=1)

    logger.info("weekly_report_job_start: week_end=%s", week_end)
    async with session_factory() as session:
        try:
            # G-4c §6.4：报告账户层隔离——遍历所有 is_active 用户账户各自生成周报。
            from quantpilot.services.account_service import AccountService

            accounts = await AccountService(session).list_active_user_accounts()
            if not accounts:
                logger.warning("weekly_report_job_skipped: no active account")
                return
            service = ReportService(session)
            count = 0
            for account in accounts:
                report = await service.generate_weekly(week_end, account.id)
                count += 1
                logger.info(
                    "weekly_report_generated: account=%d report_id=%d",
                    account.id, report.id,
                )
            await session.commit()
            logger.info("weekly_report_job_done: accounts=%d", count)
        except Exception:
            await session.rollback()
            logger.exception("weekly_report_job_failed: week_end=%s", week_end)


async def _stop_loss_warn_job(
    session_factory: async_sessionmaker,
    calendar: TradingCalendar,
    redis: AsyncRedis | None,
    notification_channel: NotificationChannel | None,
) -> None:
    """Phase 10 §5.5 + V1.5-G G-4c/G-4d-3：每日 15:05 按账户主动告警（止损 / 回撤 / 私有 SELL）。

    逻辑（设计文档 §5.5 + §6.4 多用户化 + §2 管线解耦后主动推送）：
    1. `list_active_user_accounts()` 遍历所有 is_active 用户的账户
    2. **距止损预警**（§5.5）：每持仓查最近一条 BUY Signal（共享层）取 `stop_loss_price`，
       `0 < distance_pct <= 0.02` → `notify_stop_loss_warn(..., account_id)`
    3. **账户回撤告警**（G-4d-3，原管线 RiskChecker 回撤 WARN 移此）：`get_current_drawdown`
       ≥ `risk_limits.max_drawdown_pct` → `notify_risk_warn(event_type="account_drawdown")`
    4. **持仓私有 SELL**（G-4d-3，原管线持仓分支移此）：`evaluate_private_signals` 按账户
       重跑 SignalGenerator，对 hard_stop_loss / short_term_z_drop / mid_term_icir_flip
       各 → `notify_risk_warn(event_type=trigger_reason, account_id)`

    §2 管线与账户解耦后，管线只产账户无关共享信号；账户私有的主动推送集中在本 Job
    按账户完成。去重由 NotificationService 内部按 `(notify_type, payload, account_id)`
    24h 窗口分账户完成。非交易日跳过。
    """
    today = datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()
    if not calendar.is_trade_date(today):
        logger.info("stop_loss_warn_skipped_non_trade_date: date=%s", today)
        return

    from quantpilot.data.repository import MarketDataRepository
    from quantpilot.services.account_service import AccountService
    from quantpilot.services.config_service import ConfigService
    from quantpilot.services.notification_service import NotificationService
    from quantpilot.services.signal_service import SignalService

    warned = 0
    scanned = 0
    try:
        async with session_factory() as session:
            account_service = AccountService(session)
            cfg = ConfigService(session, redis)
            signal_service = SignalService(
                MarketDataRepository(session), config_service=cfg
            )
            notifier = NotificationService(session, cfg, notification_channel)

            # G-4d-3：账户回撤阈值一次性加载（账户无关）
            risk_limits = await cfg.get_risk_limits()
            max_drawdown_pct = risk_limits.max_drawdown_pct

            accounts = await account_service.list_active_user_accounts()
            for account in accounts:
                positions = await account_service.get_positions(account.id)
                scanned += len(positions)
                for p in positions:
                    if p.current_price is None:
                        continue
                    sig = await signal_service.get_last_buy_signal(p.ts_code)
                    if sig is None or sig.stop_loss_price is None:
                        continue

                    current = float(p.current_price)
                    stop_loss = float(sig.stop_loss_price)
                    if current <= 0:
                        continue
                    distance_pct = (current - stop_loss) / current
                    if 0 < distance_pct <= STOP_LOSS_WARN_THRESHOLD:
                        try:
                            await notifier.notify_stop_loss_warn(
                                ts_code=p.ts_code,
                                name=None,
                                current_price=current,
                                stop_loss_price=stop_loss,
                                distance_pct=distance_pct,
                                account_id=account.id,
                            )
                            warned += 1
                        except Exception:
                            logger.warning(
                                "stop_loss_warn_notify_failed: account=%d ts_code=%s",
                                account.id, p.ts_code, exc_info=True,
                            )

                # ── G-4d-3：账户回撤主动告警（原管线 RiskChecker 回撤 WARN 移此）──
                try:
                    dd = await account_service.get_current_drawdown(account.id)
                    if dd is not None and dd >= max_drawdown_pct:
                        await notifier.notify_risk_warn(
                            event_type="account_drawdown",
                            message=(
                                f"账户回撤 {dd:.1%} 达到阈值 {max_drawdown_pct:.1%}，"
                                "建议控制仓位"
                            ),
                            payload={"drawdown_pct": round(dd, 4), "date": str(today)},
                            account_id=account.id,
                        )
                        warned += 1
                except Exception:
                    logger.warning(
                        "drawdown_warn_failed: account=%d", account.id, exc_info=True
                    )

                # ── G-4d-3：持仓私有 SELL 主动告警（hard_stop_loss / 短中期因子翻转）──
                # evaluate_private_signals 复用 SignalGenerator（止损逻辑单一实现源），
                # 仅返回持仓派生的私有 SELL；共享 pct_above_sell / 加仓 BUY 已排除。
                try:
                    private = await signal_service.evaluate_private_signals(
                        today, positions
                    )
                    for ps in private:
                        try:
                            await notifier.notify_risk_warn(
                                event_type=ps.trigger_reason or "private_sell",
                                message=ps.reason,
                                payload={"ts_code": ps.ts_code, "date": str(today)},
                                account_id=account.id,
                            )
                            warned += 1
                        except Exception:
                            logger.warning(
                                "private_sell_notify_failed: account=%d ts_code=%s",
                                account.id, ps.ts_code, exc_info=True,
                            )
                except Exception:
                    logger.warning(
                        "private_sell_eval_failed: account=%d", account.id, exc_info=True
                    )
            await session.commit()
        logger.info(
            "stop_loss_warn_done: date=%s accounts=%d scanned=%d warned=%d",
            today, len(accounts), scanned, warned,
        )
    except Exception:
        logger.exception("stop_loss_warn_job_failed: date=%s", today)
