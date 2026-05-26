"""DailyPipeline：日度流水线 CP1→CP2→CP3→盯市→自动分红→信号过期（Phase 7）。

Phase 10 §4.3 扩展：Pipeline 启动时一次性写入 `pipeline_run.config_snapshot`
（§4.3 v1.1 评审 Q-5 收敛语义），CP3 完成后对每条新信号推送通知（§7.2）。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis as AsyncRedis
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from quantpilot.data.adapters.base import DataSourceAdapter
    from quantpilot.data.calendar import TradingCalendar
    from quantpilot.data.validators import DataValidator
    from quantpilot.models.business import Signal
    from quantpilot.models.system import PipelineRun
    from quantpilot.notification.base import NotificationChannel

logger = logging.getLogger(__name__)


class DailyPipeline:
    """日度流水线：CP1→CP2→CP3→Step4（盯市）→Step5（自动分红）→Step6（信号过期扫描）。

    断点续传语义：
    - cp1_data_ready=True → 跳过 CP1，复用 data_snapshot_version
    - cp2_scoring_done=True → 跳过 CP2
    - cp3_signals_done=True → 跳过 CP3
    Step4/5/6 为 best-effort，失败仅记录日志，不影响 pipeline_run.status。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        adapter: DataSourceAdapter,
        validator: DataValidator,
        calendar: TradingCalendar,
        *,
        redis: AsyncRedis | None = None,
        notification_channel: NotificationChannel | None = None,
    ) -> None:
        """Phase 10 §4.3 评审 C-01：MarketStateEngine 不再由构造方注入；
        CP1 内根据 `run.config_snapshot.market_state_params` 实例化，确保用户配置生效。"""
        self._session_factory = session_factory
        self._adapter = adapter
        self._validator = validator
        self._calendar = calendar
        # Phase 10：Redis 供 ConfigService 缓存；WxPusherAdapter 供 NotificationService 推送
        # 二者均为可选；None 时分别降级为「无缓存直查 DB」和「仅写 in_app_notification」。
        self._redis = redis
        self._notification_channel = notification_channel

    # Phase 13 §3.7.2：12 个进度上报点
    async def _publish_progress(
        self, trade_date: date, step: str, status: str, progress_pct: int,
    ) -> None:
        """向 Redis pubsub 推送 pipeline 进度（best-effort）。

        【降级说明】redis=None 时降级 logger.debug；推送异常吞掉
        （进度上报是观测增强，不阻断流水线）。
        """
        import json
        payload = json.dumps({
            "trade_date": str(trade_date),
            "step": step,
            "status": status,
            "progress_pct": progress_pct,
        })
        if self._redis is None:
            logger.debug("pipeline_progress %s", payload)
            return
        try:
            await self._redis.publish("quantpilot:pipeline:progress", payload)
        except Exception:
            logger.debug("pipeline_progress_publish_failed", exc_info=True)

    async def run(self, trade_date: date) -> PipelineRun:
        """运行完整流水线。返回更新后的 PipelineRun 记录。

        每个 CP 使用独立 session + commit，确保检查点持久化。

        Phase 10 §4.3：首次运行（非断点续传）时一次性写入 `config_snapshot`，
        后续 CP 如需消费配置应从 `run.config_snapshot` 反序列化，禁止再调 ConfigService。
        """
        run = await self._get_or_create_run(trade_date)

        # Phase 10 §4.3：启动时一次性写 snapshot（仅首次，断点续传不覆盖）
        if run.config_snapshot is None:
            await self._write_config_snapshot(run)

        # R13-P1-1：PIPELINE_DURATION histogram 接入——每个 CP/Step 单独 observe
        # + 总耗时 observe（step=pipeline_total），让 Grafana
        # "Pipeline 单次耗时 p50/p95/p99" panel 在生产期实际有数据。
        import time

        from quantpilot.core.metrics import PIPELINE_DURATION

        _t_pipeline = time.perf_counter()
        try:
            await self._publish_progress(trade_date, "pipeline", "started", 0)

            if not run.cp1_data_ready:
                await self._publish_progress(trade_date, "CP1", "started", 5)
                _t = time.perf_counter()
                await self._cp1_ingest(run, trade_date)
                PIPELINE_DURATION.labels(step="cp1").observe(time.perf_counter() - _t)
                await self._publish_progress(trade_date, "CP1", "completed", 25)

            if not run.cp2_scoring_done:
                await self._publish_progress(trade_date, "CP2", "started", 30)
                _t = time.perf_counter()
                await self._cp2_scoring(run, trade_date)
                PIPELINE_DURATION.labels(step="cp2").observe(time.perf_counter() - _t)
                await self._publish_progress(trade_date, "CP2", "completed", 50)

            new_signals: list[Signal] = []
            if not run.cp3_signals_done:
                await self._publish_progress(trade_date, "CP3", "started", 55)
                _t = time.perf_counter()
                new_signals = await self._cp3_signals(run, trade_date)
                PIPELINE_DURATION.labels(step="cp3").observe(time.perf_counter() - _t)
                await self._publish_progress(trade_date, "CP3", "completed", 70)

            # Phase 10 §7.2：CP3 新生成信号推送（best-effort，失败不影响流水线）
            if new_signals:
                await self._notify_new_signals(new_signals, trade_date, run.config_snapshot)

            # Step4~6 best-effort（失败不回滚整个流水线）
            await self._publish_progress(trade_date, "Step4", "started", 75)
            _t = time.perf_counter()
            await self._step4_mark_to_market(run, trade_date)
            PIPELINE_DURATION.labels(step="step4").observe(time.perf_counter() - _t)

            await self._publish_progress(trade_date, "Step5", "started", 85)
            _t = time.perf_counter()
            await self._step5_auto_dividends(run, trade_date)
            PIPELINE_DURATION.labels(step="step5").observe(time.perf_counter() - _t)

            await self._publish_progress(trade_date, "Step6", "started", 95)
            _t = time.perf_counter()
            await self._step6_expire_signals(run, trade_date)
            PIPELINE_DURATION.labels(step="step6").observe(time.perf_counter() - _t)

            run = await self._update_run_status(run.id, "SUCCESS")
            await self._publish_progress(trade_date, "pipeline", "completed", 100)
            PIPELINE_DURATION.labels(step="pipeline_total").observe(
                time.perf_counter() - _t_pipeline,
            )
            logger.info("pipeline_completed: trade_date=%s", trade_date)
            # Phase 13 §3.1.2 埋点
            from quantpilot.core.metrics import PIPELINE_RUNS
            PIPELINE_RUNS.labels(status="success").inc()

        except Exception as exc:
            logger.exception("pipeline_failed: trade_date=%s", trade_date)
            run = await self._update_run_status(run.id, "FAILED")
            await self._publish_progress(trade_date, "pipeline", "failed", 100)
            # Phase 13 §3.1.2 埋点
            from quantpilot.core.metrics import PIPELINE_RUNS
            PIPELINE_RUNS.labels(status="failed").inc()
            # Phase 10 §7.2：Pipeline 失败告警（best-effort）
            await self._notify_pipeline_failure(
                run.id, trade_date, exc, run.config_snapshot,
            )

        return run

    # ------------------------------------------------------------------ 获取/创建

    async def _get_or_create_run(self, trade_date: date) -> PipelineRun:
        from sqlalchemy import select

        from quantpilot.models.system import PipelineRun

        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRun).where(PipelineRun.trade_date == trade_date)
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                logger.info(
                    "pipeline_resume: trade_date=%s cp1=%s cp2=%s cp3=%s",
                    trade_date, existing.cp1_data_ready,
                    existing.cp2_scoring_done, existing.cp3_signals_done,
                )
                return existing

            run = PipelineRun(
                trade_date=trade_date,
                status="RUNNING",
                started_at=datetime.now(tz=timezone.utc),
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)
            logger.info("pipeline_started: trade_date=%s id=%d", trade_date, run.id)
            return run

    async def _update_run_status(self, run_id: int, status: str) -> PipelineRun:
        from sqlalchemy import select

        from quantpilot.models.system import PipelineRun

        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRun).where(PipelineRun.id == run_id)
            )
            run = result.scalar_one()
            run.status = status
            run.finished_at = datetime.now(tz=timezone.utc)
            await session.commit()
            await session.refresh(run)
            return run

    # ------------------------------------------------------------------ CP1

    async def _cp1_ingest(self, run: PipelineRun, trade_date: date) -> None:
        """CP1：数据采集 + 校验 + 市场状态识别。写 cp1_data_ready + data_snapshot_version。

        Phase 10 §4.3 评审 C-01：MarketStateEngine 由 `run.config_snapshot.market_state_params`
        实例化（不再复用 self._market_state_engine 单例的默认配置）；
        ConfigService 进入 snapshot 冻结模式，避免 NotificationService 在 CP1 期间再读 DB。
        """
        from sqlalchemy import select

        from quantpilot.data.repository import MarketDataRepository
        from quantpilot.engine.market_state import MarketStateEngine
        from quantpilot.models.system import PipelineRun
        from quantpilot.services.config_service import ConfigService
        from quantpilot.services.config_snapshot import from_snapshot
        from quantpilot.services.data_service import DataService
        from quantpilot.services.market_state_service import MarketStateService
        from quantpilot.services.notification_service import NotificationService

        version = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ms_cfg = from_snapshot(run.config_snapshot, "market_state_params")
        ms_engine = MarketStateEngine(ms_cfg)

        async with self._session_factory() as session:
            repo = MarketDataRepository(session)
            service = DataService(self._adapter, self._validator, repo, self._calendar)
            ingest_result = await service.ingest_daily(trade_date)
            await session.commit()
            logger.info(
                "cp1_ingest_done: quotes=%d financials=%d version=%s",
                ingest_result.quote_count, ingest_result.financial_count, version,
            )
            # R13-P1-1：CP1 入库成功后即时刷新 DATA_LATENCY Gauge
            # （即时反馈，不等 /health/data 端点被调用才更新）
            if ingest_result.quote_count > 0:
                from quantpilot.core.metrics import DATA_LATENCY
                today = datetime.now(tz=timezone.utc).date()
                latency = max((today - trade_date).days, 0)
                DATA_LATENCY.labels(data_type="daily_quote").set(latency)
                if ingest_result.financial_count > 0:
                    DATA_LATENCY.labels(data_type="financial_data").set(latency)

            # 市场状态识别（Phase 10 §5.4：注入 notifier，状态切换时推送 MARKET_STATE）
            # snapshot 模式 ConfigService：完全不查 DB/Redis，直接派生 dataclass
            cp1_cfg = ConfigService(session, self._redis, snapshot=run.config_snapshot)
            cp1_notifier = NotificationService(session, cp1_cfg, self._notification_channel)
            ms_service = MarketStateService(ms_engine, repo, notifier=cp1_notifier)
            await ms_service.identify_and_save(trade_date)
            await session.commit()

            # 更新 PipelineRun
            result = await session.execute(
                select(PipelineRun).where(PipelineRun.id == run.id)
            )
            db_run = result.scalar_one()
            db_run.cp1_data_ready = True
            db_run.cp1_at = datetime.now(tz=timezone.utc)
            db_run.data_snapshot_version = version
            await session.commit()

        # 同步回调用方 run 对象（避免后续判断失效）
        run.cp1_data_ready = True
        run.data_snapshot_version = version
        logger.info("cp1_done: trade_date=%s version=%s", trade_date, version)

    # ------------------------------------------------------------------ CP2

    async def _cp2_scoring(self, run: PipelineRun, trade_date: date) -> None:
        """CP2：全市场评分（ScoringService.run_daily_scoring）。写 cp2_scoring_done。

        Phase 10 §4.3 评审 C-01：UniverseFilter / 4 个策略 / Scorer / CandidatePoolManager
        全部从 `run.config_snapshot` 派生 dataclass 实例化，不再使用默认配置。

        Phase 11 §6.3：注入 FactorMonitorService → ScoringService 自动走 5 步管线
        （score_universe + write_candidate_pool）；调用形态不变，
        ``run_daily_scoring`` 内部根据 ``self._factor_monitor`` 是否注入切换。
        """
        from sqlalchemy import select

        from quantpilot.data.factor_ic_repository import FactorICRepository
        from quantpilot.data.repository import MarketDataRepository
        from quantpilot.engine.factor_monitor import FactorMonitorEngine
        from quantpilot.engine.factor_pipeline import FactorPipeline, FactorPipelineConfig
        from quantpilot.engine.pool import CandidatePoolManager
        from quantpilot.engine.scorer import Scorer
        from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
        from quantpilot.engine.strategies.momentum import MomentumStrategy
        from quantpilot.engine.strategies.trend import TrendStrategy
        from quantpilot.engine.strategies.value import ValueStrategy
        from quantpilot.engine.universe import UniverseFilter
        from quantpilot.models.system import PipelineRun
        from quantpilot.services.config_snapshot import from_snapshot
        from quantpilot.services.factor_monitor_service import FactorMonitorService
        from quantpilot.services.strategy_service import ScoringService

        snap = run.config_snapshot
        universe_cfg = from_snapshot(snap, "universe_params")
        weights_cfg = from_snapshot(snap, "strategy_weights")
        trend_cfg = from_snapshot(snap, "strategy_params_trend")
        momentum_cfg = from_snapshot(snap, "strategy_params_momentum")
        mr_cfg = from_snapshot(snap, "strategy_params_mean_reversion")
        value_cfg = from_snapshot(snap, "strategy_params_value")
        # Phase 11 §7.2：派生 FactorPipelineConfig（snapshot 缺失时回退默认）
        sp_dict = (snap or {}).get("scoring_pipeline_params") or {}
        fp_cfg = FactorPipelineConfig(
            winsorize_lower_pct=sp_dict.get("winsorize_lower_pct", 0.01),
            winsorize_upper_pct=sp_dict.get("winsorize_upper_pct", 0.99),
            neutralize_industry=sp_dict.get("neutralize_industry", True),
            neutralize_market_cap=sp_dict.get("neutralize_market_cap", True),
            neutralize_beta=sp_dict.get("neutralize_beta", False),
        )

        async with self._session_factory() as session:
            repo = MarketDataRepository(session)
            # Phase 11 §6.3：FactorMonitorService 注入用于 score_universe 内
            # get_active_weights 查询 strategy_weights_history（冷启动 fallback default_matrix）
            # Phase 14 §14-5：注入 calendar 让 rolling_icir_state 走严格交易日窗口
            factor_monitor = FactorMonitorService(
                session, FactorMonitorEngine(), FactorICRepository(),
                calendar=self._calendar,
            )
            scoring_service = ScoringService(
                repo=repo,
                universe_filter=UniverseFilter(universe_cfg),
                strategies=[
                    TrendStrategy(trend_cfg),
                    MomentumStrategy(momentum_cfg),
                    MeanReversionStrategy(mr_cfg),
                    ValueStrategy(value_cfg),
                ],
                # Phase 11 §7.2：scoring_pipeline_params 驱动 FactorPipeline 5 步管线开关
                scorer=Scorer(weights_cfg, pipeline=FactorPipeline(fp_cfg)),
                pool_manager=CandidatePoolManager(universe_cfg),
                calendar=self._calendar,
                factor_monitor=factor_monitor,
            )
            scores = await scoring_service.run_daily_scoring(trade_date)
            await session.commit()
            logger.info("cp2_scoring_done: trade_date=%s scored=%d", trade_date, len(scores))

            result = await session.execute(
                select(PipelineRun).where(PipelineRun.id == run.id)
            )
            db_run = result.scalar_one()
            db_run.cp2_scoring_done = True
            db_run.cp2_at = datetime.now(tz=timezone.utc)
            await session.commit()

        run.cp2_scoring_done = True
        logger.info("cp2_done: trade_date=%s", trade_date)

    # ------------------------------------------------------------------ CP3

    async def _cp3_signals(self, run: PipelineRun, trade_date: date) -> list[Signal]:
        """CP3：信号生成（SignalService.generate_for_date）。

        Phase 10 §7.1：注入 AccountService + ConfigService 驱动
        SignalGenerator→PositionSizer→RiskChecker 全链路；
        返回本次新写入的 Signal ORM 列表供 `_notify_new_signals` 使用。

        Phase 10 §4.3 评审 C-01：ConfigService 进入 snapshot 冻结模式，
        SignalService 内 `signal_params/universe_params/risk_limits` 全部来自快照。
        """
        from sqlalchemy import select

        from quantpilot.data.repository import MarketDataRepository
        from quantpilot.models.system import PipelineRun
        from quantpilot.services.account_service import AccountService
        from quantpilot.services.config_service import ConfigService
        from quantpilot.services.notification_service import NotificationService
        from quantpilot.services.signal_service import SignalService

        async with self._session_factory() as session:
            repo = MarketDataRepository(session)
            account_service = AccountService(session)
            config_service = ConfigService(session, self._redis, snapshot=run.config_snapshot)
            # Phase 10 §5.4：注入 notifier，RiskChecker WARN/BLOCK 告警将推送 RISK_WARN
            notifier = NotificationService(
                session, config_service, self._notification_channel
            )
            signal_service = SignalService(
                repo,
                account_service=account_service,
                config_service=config_service,
                notification_service=notifier,
            )
            signals = await signal_service.generate_for_date(trade_date)
            await session.commit()
            signal_count = len(signals)
            logger.info("cp3_signals_done: trade_date=%s count=%d", trade_date, signal_count)

            result = await session.execute(
                select(PipelineRun).where(PipelineRun.id == run.id)
            )
            db_run = result.scalar_one()
            db_run.cp3_signals_done = True
            db_run.cp3_at = datetime.now(tz=timezone.utc)
            db_run.signal_count = signal_count
            await session.commit()

        run.cp3_signals_done = True
        logger.info("cp3_done: trade_date=%s signals=%d", trade_date, signal_count)
        return list(signals)

    # ------------------------------------------------------------------ Step4

    async def _step4_mark_to_market(self, run: PipelineRun, trade_date: date) -> None:
        """Step4：盯市 + daily_portfolio_value 快照（best-effort）。"""
        try:
            from quantpilot.services.account_service import AccountService

            async with self._session_factory() as session:
                account_service = AccountService(session)
                accounts = await account_service.mark_to_market(trade_date)
                await session.commit()
                logger.info(
                    "step4_mark_to_market_done: trade_date=%s accounts=%d",
                    trade_date, len(accounts),
                )
        except Exception:
            logger.warning(
                "step4_mark_to_market_failed: trade_date=%s", trade_date, exc_info=True
            )

    # ------------------------------------------------------------------ Step5

    async def _step5_auto_dividends(self, run: PipelineRun, trade_date: date) -> None:
        """Step5：自动分红处理（best-effort）。"""
        try:
            from quantpilot.data.repository import MarketDataRepository
            from quantpilot.services.data_service import DataService

            async with self._session_factory() as session:
                repo = MarketDataRepository(session)
                data_service = DataService(self._adapter, self._validator, repo, self._calendar)
                processed = await data_service.fetch_dividends(trade_date)
                await session.commit()
                logger.info(
                    "step5_auto_dividends_done: trade_date=%s processed=%d",
                    trade_date, processed,
                )
        except Exception:
            logger.warning(
                "step5_auto_dividends_failed: trade_date=%s", trade_date, exc_info=True
            )

    # ------------------------------------------------------------------ Step6

    async def _step6_expire_signals(self, run: PipelineRun, trade_date: date) -> None:
        """Step6：信号过期扫描（best-effort，Phase 5 SignalService 委托）。"""
        try:
            from quantpilot.data.repository import MarketDataRepository
            from quantpilot.services.signal_service import SignalService

            async with self._session_factory() as session:
                repo = MarketDataRepository(session)
                signal_service = SignalService(repo)
                expired = await signal_service.expire_old_signals(trade_date)
                await session.commit()
                logger.info(
                    "step6_expire_signals_done: trade_date=%s expired=%d",
                    trade_date, expired,
                )
        except Exception:
            logger.warning(
                "step6_expire_signals_failed: trade_date=%s", trade_date, exc_info=True
            )

    # ---------------------------------------------- Phase 10 §4.3 配置快照

    async def _write_config_snapshot(self, run: PipelineRun) -> None:
        """启动时一次性读 12 个 config_key → 写 `pipeline_run.config_snapshot`（§4.3 Q-5）。

        best-effort：读取异常时记 WARN 并继续（快照缺失不阻塞流水线主链路）。
        """
        from sqlalchemy import select

        from quantpilot.models.system import PipelineRun
        from quantpilot.services.config_service import ConfigService

        try:
            async with self._session_factory() as session:
                cfg = ConfigService(session, self._redis)
                # Phase 10 §4.3：pipeline_run.config_snapshot 只含运行时参数，
                # 不写 backtest_defaults / risk_free_rate（它们只在回测/绩效路径消费）
                snapshot = await cfg.get_pipeline_snapshot()

                result = await session.execute(
                    select(PipelineRun).where(PipelineRun.id == run.id)
                )
                db_run = result.scalar_one()
                db_run.config_snapshot = snapshot
                await session.commit()
            run.config_snapshot = snapshot
            logger.info("pipeline_config_snapshot_written: run_id=%d", run.id)
        except Exception:
            logger.warning(
                "pipeline_config_snapshot_failed: run_id=%d", run.id, exc_info=True
            )

    # ---------------------------------------------- Phase 10 §7.2 通知

    async def _notify_new_signals(
        self,
        signals: list[Signal],
        trade_date: date,
        config_snapshot: dict | None,
    ) -> None:
        """CP3 新信号推送（best-effort，每条独立异常隔离）。

        Phase 10 §4.3 评审 C-01：通知偏好从 `run.config_snapshot.notification_prefs` 派生，
        不再调用 ConfigService DB 查询。
        """
        try:
            from quantpilot.services.config_service import ConfigService
            from quantpilot.services.notification_service import NotificationService

            async with self._session_factory() as session:
                cfg = ConfigService(session, self._redis, snapshot=config_snapshot)
                notifier = NotificationService(session, cfg, self._notification_channel)
                for sig in signals:
                    try:
                        await notifier.notify_signal(sig)
                    except Exception:
                        logger.warning(
                            "notify_signal_failed: signal_id=%s ts_code=%s",
                            sig.id, sig.ts_code, exc_info=True,
                        )
                await session.commit()
            logger.info(
                "pipeline_notify_new_signals_done: trade_date=%s count=%d",
                trade_date, len(signals),
            )
        except Exception:
            logger.warning(
                "pipeline_notify_new_signals_failed: trade_date=%s", trade_date,
                exc_info=True,
            )

    async def _notify_pipeline_failure(
        self,
        run_id: int,
        trade_date: date,
        exc: BaseException,
        config_snapshot: dict | None,
    ) -> None:
        """Pipeline 失败告警（best-effort）。

        R13-P1-3：notify("PIPELINE_FAILURE", ...) → notify_health_alert("pipeline_failed", ...)：
        - 与 Phase 13 §3.4.1 "运维告警统一入口" 设计意图一致
        - `notify_type` 列统一为 HEALTH_ALERT，让运维仪表盘"近 7 日健康告警数"
          下钻能聚合 pipeline 失败（之前 PIPELINE_FAILURE 类型会漏掉这条最高频
          的健康告警）
        - 复用 notify_risk_warn 父开关（HEALTH_ALERT 已在 _TYPE_PREF_MAP 中注册）

        Phase 10 §4.3 评审 C-01：使用 snapshot 模式 ConfigService。
        """
        try:
            from quantpilot.services.config_service import ConfigService
            from quantpilot.services.notification_service import NotificationService

            async with self._session_factory() as session:
                cfg = ConfigService(session, self._redis, snapshot=config_snapshot)
                notifier = NotificationService(session, cfg, self._notification_channel)
                await notifier.notify_health_alert(
                    "pipeline_failed",
                    f"交易日 {trade_date} 流水线异常：{exc!r}",
                    payload={
                        "run_id": run_id,
                        "trade_date": str(trade_date),
                        "error": type(exc).__name__,
                    },
                )
                await session.commit()
        except Exception:
            logger.warning(
                "pipeline_failure_notify_failed: run_id=%d", run_id, exc_info=True
            )
