"""APScheduler 配置：日度流水线 + 月末 + 周报 + 止损预警（Phase 7 + Phase 10 §5.5）。"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# 纯函数，模块级导入（其余重依赖仍在 job 函数内懒加载，保持既有约定）
from quantpilot.services.factor_monitor_service import plan_catchup_dates

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

# V1.5-C C0：日级 IC 生产者 Job 参数
_DAILY_IC_LAG_DAYS = 20          # SDD §7.4 前向收益窗口（交易日）；= 可算的最晚因子值日回溯量
# 单次运行处理上限。2026-08-17 实证：一次全 universe 评分在生产 2GB 机上 RSS 达
# 1.58 GB，独立进程跑单日即触发 OOM killer（站点 530 共 43 分钟）。17:30 每日管线
# 长期稳定跑的正是"每次运行一次评分"，故上限取 1——单次运行绝不连做多次评分。
# 长断档不靠本 Job 追平（那需要 N 天），走本地算力中心批量回填后导入，见 CLAUDE.md §6 运维红线。
_DAILY_IC_CATCHUP_MAX_DAYS = 1
_DAILY_IC_LOOKBACK_DAYS = 400    # 追平回看窗口（日历日），覆盖长期断档


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

    # 收盘后复评持仓私有信号（每日 18:30）：修复 hard_stop_loss 的一日延迟。
    # ⚠️ 必须晚于 daily_pipeline（17:30）——它依赖管线 step4 盯市写入的当日收盘价。
    # misfire_grace_time 给足 1h：管线偶有跑长，宁可晚跑也不要错过整晚。
    scheduler.add_job(
        _private_signal_recheck_job,
        trigger=CronTrigger(hour=18, minute=30, timezone="Asia/Shanghai"),
        args=[session_factory, calendar, redis, notification_channel],
        id="private_signal_recheck",
        replace_existing=True,
        misfire_grace_time=3600,
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

    # 日级 IC 生产者 Job（每日 19:30）：V1.5-C C0——ICIR 权重校准的上游生产者。
    # 19:30 避开 17:30 日线管线高峰（2GB 机不并发跑两次全 universe 评分）。
    # 滞后消费：处理 t-20 交易日那天（其前向收益今日已实现），与 SDD §7.4 lag 20 一致。
    # 此前日级 IC 只有一次性回填脚本能产出 → 生产 2026-05-11 后停更、ICIR 窗口样本
    # 逐日流失（V1.5-C 设计 §2.1）；手动脚本不算功能闭环。
    scheduler.add_job(
        _daily_ic_producer_job,
        trigger=CronTrigger(hour=19, minute=30, timezone="Asia/Shanghai"),
        args=[session_factory, adapter, calendar],
        id="daily_ic_producer",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # 流水线卡死看门狗 Job（每 30min）：扫描 RUNNING 超时未完成的 pipeline_run 告警。
    # 2026-07 事故：12 个 run 长期 RUNNING 无人知、静默 3 周。看门狗独立于管线进程，
    # 挂起的管线无法自报，必须由此周期扫描兜底（pipeline_monitor.scan_stuck_runs）。
    scheduler.add_job(
        _pipeline_watchdog_job,
        trigger=IntervalTrigger(minutes=30),
        args=[session_factory, redis, notification_channel],
        id="pipeline_watchdog",
        replace_existing=True,
        misfire_grace_time=600,
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


def _set_daily_ic_lag_gauge(
    today: date, existing: set[date], lookback_start: date,
) -> None:
    """刷新 `quantpilot_factor_ic_daily_lag_days`（V1.5-C C0 §2.2 C0-4）。

    存量为空时报回看窗口全长（而非 0）——"查不到"必须表现为"滞后很大"触发告警，
    否则生产者停摆会伪装成健康（正是 2026-05 断档 3 个月无人发现的形态）。
    """
    from quantpilot.core.metrics import FACTOR_IC_DAILY_LAG

    newest = max(existing) if existing else None
    lag = (today - newest).days if newest else (today - lookback_start).days
    FACTOR_IC_DAILY_LAG.set(float(lag))


async def _daily_ic_producer_job(
    session_factory: async_sessionmaker,
    adapter: DataSourceAdapter,
    calendar: TradingCalendar,
) -> None:
    """日级 IC 生产者 Job（V1.5-C C0，每日 19:30）。

    每次运行：查已有 daily IC 日 → `plan_catchup_dates` 取最旧的至多
    ``_DAILY_IC_CATCHUP_MAX_DAYS`` 天（≤ ``t-20`` 交易日）→ 逐日
    ``FactorMonitorService.produce_daily_ic``。稳态下每次恰好补上新满足 t-20 的那
    一天；小断档逐日自愈。**大断档不靠本 Job 追平**（1 天/次 → N 天才追平，期间
    ICIR 窗口持续流失样本），走本地算力中心批量回填后导入生产。

    每日一个独立 session 显式 commit（调度 Job 不走 `get_db` 自动 commit）；
    单日失败 `logger.exception` 后继续下一日——一天算不出来不该让整条追平停摆。
    """
    from datetime import timedelta

    from quantpilot.data.factor_ic_repository import FactorICRepository
    from quantpilot.engine.factor_monitor import FactorMonitorEngine
    from quantpilot.services.factor_monitor_service import FactorMonitorService
    from quantpilot.services.scoring_factory import build_default_scoring_service

    today = datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()
    try:
        last_eligible = calendar.get_prev_trade_date(today, _DAILY_IC_LAG_DAYS)
    except (ValueError, IndexError):
        logger.exception("daily_ic_producer_job_calendar_failed: today=%s", today)
        return

    # 回看窗口：足够覆盖长期断档（生产 2026-05 起断档约 60 交易日），
    # 上限由 plan_catchup_dates 的 max_days 控制。
    lookback_start = last_eligible - timedelta(days=_DAILY_IC_LOOKBACK_DAYS)
    try:
        async with session_factory() as session:
            existing = await FactorICRepository().get_existing_daily_ic_dates(
                session, lookback_start, last_eligible,
            )
        trade_dates = calendar.get_trade_dates(lookback_start, last_eligible)
        planned = plan_catchup_dates(
            trade_dates=trade_dates,
            existing=existing,
            last_eligible=last_eligible,
            max_days=_DAILY_IC_CATCHUP_MAX_DAYS,
        )
        _set_daily_ic_lag_gauge(today, existing, lookback_start)
    except Exception:
        logger.exception("daily_ic_producer_job_plan_failed: today=%s", today)
        return

    if not planned:
        logger.info("daily_ic_producer_nothing_to_do: last_eligible=%s", last_eligible)
        return

    total_rows = ok = failed = 0
    for td in planned:
        try:
            async with session_factory() as session:
                service = FactorMonitorService(
                    session, FactorMonitorEngine(), FactorICRepository(),
                    calendar=calendar,
                )
                scoring_service = build_default_scoring_service(session, calendar)
                n = await service.produce_daily_ic(session, td, scoring_service)
                await session.commit()
            total_rows += n
            ok += 1
        except Exception:
            failed += 1
            logger.exception("daily_ic_producer_date_failed: trade_date=%s", td)

    logger.info(
        "daily_ic_producer_job_done: planned=%d ok=%d failed=%d rows=%d "
        "last_eligible=%s",
        len(planned), ok, failed, total_rows, last_eligible,
    )


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


async def _notify_private_signals(
    signal_service,
    notifier,
    account,
    positions: list,
    today: date,
) -> int:
    """评估并推送某账户的持仓私有信号，返回成功推送条数。

    G-4d-3/4：`evaluate_private_signals` 复用 SignalGenerator（止损/加仓逻辑
    **单一实现源**），返回持仓派生的私有 SELL（hard_stop_loss / 短中期因子翻转
    → `notify_risk_warn`）+ 加仓 BUY（SDD §10.1 can_add，用户 2026-07-03 拍板
    同路走 Job 通知 → `notify("SIGNAL_BUY")`）；共享 pct_above_sell 已排除（管线已产）。

    由 15:05 `_stop_loss_warn_job` 与 18:30 `_private_signal_recheck_job` **共用**——
    在任一处另写一份阈值判定都会产生第二实现源、迟早漂移。

    去重：`payload` 含 `date`，同日两次评估被 NotificationService 的 24h 窗口挡住
    （每个突破每天至多一条），跨日则各自成立。
    """
    sent = 0
    try:
        private = await signal_service.evaluate_private_signals(today, positions)
    except Exception:
        logger.warning(
            "private_signal_eval_failed: account=%d", account.id, exc_info=True
        )
        return 0

    for ps in private:
        try:
            if ps.signal_type == "BUY":
                await notifier.notify(
                    "SIGNAL_BUY",
                    f"加仓提示：{ps.ts_code}",
                    ps.reason or "持仓达买入条件且满足加仓规则",
                    payload={
                        "ts_code": ps.ts_code,
                        "date": str(today),
                        "kind": "add_position",
                    },
                    account_id=account.id,
                )
            else:
                await notifier.notify_risk_warn(
                    event_type=ps.trigger_reason or "private_sell",
                    message=ps.reason,
                    payload={"ts_code": ps.ts_code, "date": str(today)},
                    account_id=account.id,
                )
            sent += 1
        except Exception:
            logger.warning(
                "private_signal_notify_failed: account=%d ts_code=%s",
                account.id, ps.ts_code, exc_info=True,
            )
    return sent


async def _todays_pipeline_succeeded(session, trade_date: date) -> bool:
    """当日管线是否已跑完（= step4 盯市已把当日收盘价写进 position）。

    收盘后复评的**新鲜度前提**。SUCCESS 是在 step4/5/6 全部完成后才写的，
    故它成立即意味着 `position.current_price` 已是当日收盘价。
    """
    from sqlalchemy import select

    from quantpilot.models.system import PipelineRun

    status = (
        await session.execute(
            select(PipelineRun.status).where(PipelineRun.trade_date == trade_date)
        )
    ).scalar_one_or_none()
    return status == "SUCCESS"


async def _private_signal_recheck_job(
    session_factory: async_sessionmaker,
    calendar: TradingCalendar,
    redis: AsyncRedis | None,
    notification_channel: NotificationChannel | None,
) -> None:
    """收盘后复评持仓私有信号（硬止损一日延迟修复，2026-09-04）。

    ## 为什么必须有这个 Job

    `hard_stop_loss` 此前**唯一**的评估点是 15:05 的 `_stop_loss_warn_job`，
    而它读的 `position.current_price` 由 **17:30 管线 step4 盯市**写入
    → 15:05 看到的永远是**前一交易日**收盘价，当日收盘价入库后再无人复评。

    实证（001258.SZ，cost 13.594）：9/3 15:05 按 9/2 收盘算得 −7.90%（不触发，
    按当时数据正确）→ 9/3 18:43 盯市后实为 **−14.59%** → 却要等 9/4 15:05 才通知。
    A 股 15:00 已收盘，用户最早次日开盘才能卖，从「数据可知」到「可执行」约 2 个交易日。
    旁证：`RISK_WARN` 71 条中 `hard_stop_loss` **0 条**，机制上线以来从未发出过。

    ⚠️ 本 Job 与 15:05 那条**互补而非替代**：15:05 用昨收算「距止损 ≤2%」是面向
    次日的**前瞻预警**，本 Job 用当日收盘算「已经破了」的**事后止损**。

    ⚠️ 排程时刻必须晚于 daily_pipeline——排在它之前则读到的仍是昨日价，
    等于没修。该不变量由 `tests/unit/test_private_signal_recheck_job.py` 钉死
    （断言的是两个 Job 的**相对顺序**，不是写死 18:30）。
    """
    from quantpilot.data.repository import MarketDataRepository
    from quantpilot.services.account_service import AccountService
    from quantpilot.services.config_service import ConfigService
    from quantpilot.services.notification_service import NotificationService
    from quantpilot.services.signal_service import SignalService

    today = datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()
    if not calendar.is_trade_date(today):
        logger.info("private_signal_recheck_skipped_non_trade_date: date=%s", today)
        return

    sent = 0
    accounts: list = []
    try:
        async with session_factory() as session:
            # 新鲜度守卫：盯市没跑就不评估。用昨日价评估会产出一条 payload 带今日
            # 日期的通知，把 24h 去重窗口占掉 → 次日那条**正确**的反而被挡住，
            # 比不评估更糟。故此处宁可跳过，且必须留 WARNING（C-4 不静默）。
            if not await _todays_pipeline_succeeded(session, today):
                logger.warning(
                    "private_signal_recheck_skipped: date=%s reason=当日管线未成功完成"
                    "（盯市未写入当日收盘价），本次不评估以免用陈旧价占用去重窗口",
                    today,
                )
                return

            account_service = AccountService(session)
            cfg = ConfigService(session, redis)
            signal_service = SignalService(
                MarketDataRepository(session), config_service=cfg
            )
            notifier = NotificationService(session, cfg, notification_channel)

            accounts = await account_service.list_active_user_accounts()
            for account in accounts:
                positions = await account_service.get_positions(account.id)
                if not positions:
                    continue
                sent += await _notify_private_signals(
                    signal_service, notifier, account, positions, today
                )
            await session.commit()
        logger.info(
            "private_signal_recheck_done: date=%s accounts=%d sent=%d",
            today, len(accounts), sent,
        )
    except Exception:
        logger.exception("private_signal_recheck_job_failed: date=%s", today)


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

                # ── G-4d-3/4：持仓私有信号主动推送 ──────────────────────────────
                # 提取为 _notify_private_signals 后由本 Job 与 18:30 收盘后复评
                # 共用（单一实现源）。
                warned += await _notify_private_signals(
                    signal_service, notifier, account, positions, today
                )
            await session.commit()
        logger.info(
            "stop_loss_warn_done: date=%s accounts=%d scanned=%d warned=%d",
            today, len(accounts), scanned, warned,
        )
    except Exception:
        logger.exception("stop_loss_warn_job_failed: date=%s", today)


async def _pipeline_watchdog_job(
    session_factory: async_sessionmaker,
    redis: AsyncRedis | None,
    notification_channel: NotificationChannel | None,
) -> None:
    """流水线卡死看门狗（每 30min）：扫描 RUNNING 超时未完成的 run 并告警。

    2026-07 事故复盘产出。自建 session 显式 commit（调度 job 不走 get_db 自动 commit）。
    去重由 NotificationService 按 (notify_type, payload, account_id) 24h 窗口完成，
    同一卡死 run 每天至多告警一次。
    """
    from quantpilot.services.config_service import ConfigService
    from quantpilot.services.notification_service import NotificationService
    from quantpilot.services.pipeline_monitor import scan_stuck_runs

    try:
        async with session_factory() as session:
            cfg = ConfigService(session, redis)
            notifier = NotificationService(session, cfg, notification_channel)
            n = await scan_stuck_runs(session, notifier)
            await session.commit()
        if n:
            logger.warning("pipeline_watchdog_alerted: stuck=%d", n)
    except Exception:
        logger.exception("pipeline_watchdog_job_failed")
