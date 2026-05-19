"""月度调度器：月末任务（Phase 5 P5-PRE-2 → Phase 7 扩展）。"""
from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis as AsyncRedis
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from quantpilot.data.calendar import TradingCalendar
    from quantpilot.engine.factor_monitor import FactorMonitorEngine
    from quantpilot.notification.base import NotificationChannel

logger = logging.getLogger(__name__)


class MonthlyScheduler:
    """月末任务调度。调度注册由 APScheduler 统一负责。

    Phase 7 新增：
    - run_factor_monitoring(calc_month)：因子质量监控
    - run_monthly_report(month_end)：月报生成
    - run_all(month_end)：月末总入口（含非交易日回溯）
    """

    def __init__(
        self,
        data_service: object,
        session_factory: async_sessionmaker | None = None,
        calendar: TradingCalendar | None = None,
        factor_monitor_engine: FactorMonitorEngine | None = None,
        *,
        redis: AsyncRedis | None = None,
        notification_channel: NotificationChannel | None = None,
    ) -> None:
        self._data_service = data_service
        self._session_factory = session_factory
        self._calendar = calendar
        self._factor_monitor_engine = factor_monitor_engine
        # Phase 10 §7.3：因子告警通过 NotificationService/WxPusher；二者均为可选。
        self._redis = redis
        self._notification_channel = notification_channel

    async def run_quarterly_financial_refresh(self, as_of_date: date) -> None:
        """每季末（3/6/9/12月最后一个交易日）执行全量财务补录。
        仅在 as_of_date 所在月份为 3/6/9/12 月时执行，其他月份跳过。
        调度由 Phase 7 的 APScheduler 统一注册。Phase 5 只实现方法本身。
        """
        if as_of_date.month not in (3, 6, 9, 12):
            logger.info(
                "quarterly_financial_refresh_skipped: month=%d not quarter-end",
                as_of_date.month,
            )
            return
        logger.info("quarterly_financial_refresh_start: as_of_date=%s", as_of_date)
        result = await self._data_service.refresh_financials_full()
        logger.info(
            "quarterly_financial_refresh_done: success=%d fail=%d",
            result["success_count"],
            result["fail_count"],
        )

    async def run_factor_monitoring(self, calc_month: date) -> None:
        """月末执行因子质量监控（Phase 7 + Phase 10 §7.3 告警接入）。

        需要 session_factory 和 factor_monitor_engine 已注入（若缺失则 skip）。
        已注入 notification_channel 时构造真实 NotificationService 推送因子告警；
        否则以 None 传入，FactorMonitorService 只写 factor_ic_history 不推送。
        """
        if self._session_factory is None or self._factor_monitor_engine is None:
            logger.warning("run_factor_monitoring_skipped: missing session_factory or engine")
            return

        from quantpilot.services.config_service import ConfigService
        from quantpilot.services.factor_monitor_service import FactorMonitorService
        from quantpilot.services.notification_service import NotificationService

        async with self._session_factory() as session:
            try:
                service = FactorMonitorService(session, self._factor_monitor_engine)
                notifier: NotificationService | None = None
                if self._notification_channel is not None:
                    cfg = ConfigService(session, self._redis)
                    notifier = NotificationService(session, cfg, self._notification_channel)
                written = await service.run_monthly(calc_month, notifier=notifier)
                await session.commit()
                logger.info(
                    "factor_monitoring_done: calc_month=%s written=%d", calc_month, written
                )
            except Exception:
                await session.rollback()
                logger.exception("factor_monitoring_failed: calc_month=%s", calc_month)

    async def run_icir_rebalance(self, month_end: date) -> None:
        """Phase 11 §6.1：每月末 ICIR rebalance Job。

        - 调 FactorMonitorService.apply_monthly_rebalance(session, month_end)
        - 写 factor_ic_window_state + strategy_weights_history
        - 触发 Hysteresis 判定 + R1~R4 因子下线规则

        与 run_factor_monitoring / run_monthly_report 并列，独立运行；
        任一失败不阻塞其他 Job。
        """
        if self._session_factory is None or self._factor_monitor_engine is None:
            logger.warning(
                "run_icir_rebalance_skipped: missing session_factory or engine"
            )
            return

        from quantpilot.services.factor_monitor_service import FactorMonitorService

        async with self._session_factory() as session:
            try:
                service = FactorMonitorService(session, self._factor_monitor_engine)
                result = await service.apply_monthly_rebalance(session, month_end)
                await session.commit()
                logger.info(
                    "icir_rebalance_done: month_end=%s states=%d",
                    month_end, len(result),
                )
            except Exception:
                await session.rollback()
                logger.exception("icir_rebalance_failed: month_end=%s", month_end)

    async def run_monthly_report(self, month_end: date) -> None:
        """月末生成月报（Phase 7）。

        需要 session_factory 已注入（若缺失则 skip）。
        """
        if self._session_factory is None:
            logger.warning("run_monthly_report_skipped: missing session_factory")
            return

        from quantpilot.services.report_service import ReportService

        async with self._session_factory() as session:
            try:
                service = ReportService(session)
                report = await service.generate_monthly(month_end)
                await session.commit()
                logger.info(
                    "monthly_report_done: month_end=%s report_id=%d", month_end, report.id
                )
            except Exception:
                await session.rollback()
                logger.exception("monthly_report_failed: month_end=%s", month_end)

    async def run_all(self, month_end: date) -> None:
        """月末总入口：quarterly_refresh（条件执行）+ factor_monitoring + monthly_report。

        非交易日处理：若触发日为非交易日（周末/节假日），调用
        calendar.prev_trade_date(month_end) 取当月最后交易日作为 calc_month，
        确保因子监控数据使用完整当月收益率后再计算。
        """
        calc_month = month_end
        if self._calendar is not None:
            try:
                if not self._calendar.is_trade_date(month_end):
                    calc_month = self._calendar.get_prev_trade_date(month_end)
                    logger.info(
                        "monthly_run_all_backtrack: trigger=%s calc_month=%s",
                        month_end, calc_month,
                    )
            except Exception:
                logger.warning(
                    "monthly_run_all_backtrack_failed: use raw month_end=%s", month_end
                )

        logger.info("monthly_run_all_start: calc_month=%s", calc_month)

        await self.run_quarterly_financial_refresh(calc_month)
        await self.run_factor_monitoring(calc_month)
        # Phase 11 §6.1：ICIR 月度 rebalance Job 在月报前执行（生效日 = calc_month+1）。
        # 与 run_factor_monitoring 并列：前者写 factor_ic_history（Phase 7 旧表，readonly
        # 保留作 baseline），后者写 factor_ic_window_state + strategy_weights_history（Phase 11
        # 新表，月初生效用于 next-month scoring）。
        await self.run_icir_rebalance(calc_month)
        await self.run_monthly_report(calc_month)

        logger.info("monthly_run_all_done: calc_month=%s", calc_month)
