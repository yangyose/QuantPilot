from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from quantpilot.api.v1 import (
    account,
    attribution,
    auth,
    backtest,
    data,
    factor_quality,
    health,
    market,
    metrics,
    notifications,
    performance,
    pipeline,
    positions,
    reports,
    setup,
    signals,
    watchlist,
)
from quantpilot.api.v1 import settings as settings_router
from quantpilot.core.config import settings
from quantpilot.core.exceptions import register_exception_handlers
from quantpilot.core.logging_config import setup_logging

setup_logging(
    log_dir=settings.log_dir,
    level=settings.log_level,
    enable_json=settings.log_json,
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """初始化长期对象（adapter/calendar）并启动调度器。

    只有在 TUSHARE_TOKEN 已配置时才尝试初始化；未配置时数据 API 返回 503。
    """
    app.state.adapter = None
    app.state.calendar = None
    app.state.scheduler = None
    # Phase 13 §3.6：AKShareAdapter 始终实例化作 fallback（无需 token）；
    # DataService 通过 get_data_service 注入，Tushare 失败时自动降级。
    from quantpilot.data.adapters.akshare import AKShareAdapter
    app.state.fallback_adapter = AKShareAdapter()

    # Phase 10 §4.3 评审 C-02：保留 MarketStateEngine 单例供非流水线
    # `/market/state/identify` API 路径使用；DailyPipeline 不再消费此单例，
    # CP1 内根据 `run.config_snapshot.market_state_params` 即时实例化。
    from quantpilot.engine.market_state import MarketStateEngine
    app.state.market_state_engine = MarketStateEngine()
    # Phase 8/13 §3.7：Redis 客户端（WS 进度推送 + pubsub）；
    # 连接失败时降级 None，DailyPipeline._publish_progress 自动走 logger.debug
    app.state.redis = None
    try:
        from redis import asyncio as redis_asyncio
        app.state.redis = redis_asyncio.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True,
        )
        await app.state.redis.ping()
        logger.info("redis_connected url=%s", settings.redis_url)
    except Exception as exc:
        logger.warning("redis_connect_failed url=%s reason=%s", settings.redis_url, exc)
        app.state.redis = None

    # Phase 10：WxPusher 通知渠道（app_token/uid 未配置时实例化后自动降级为 no-op）
    from quantpilot.notification.wxpusher import WxPusherAdapter
    app.state.wxpusher = WxPusherAdapter(
        app_token=settings.wxpusher_app_token,
        uid=settings.wxpusher_uid,
    )

    if settings.tushare_token:
        from quantpilot.core.database import AsyncSessionLocal
        from quantpilot.data.adapters.tushare import TushareAdapter
        from quantpilot.data.calendar import TradingCalendar
        from quantpilot.data.validators import DataValidator
        from quantpilot.pipeline.scheduler import create_scheduler

        adapter = TushareAdapter(settings.tushare_token)
        app.state.adapter = adapter

        today = datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()
        try:
            # 交易日历 DB 优先：从持久化的 trade_calendar 加载；若 DB 未覆盖所需
            # 范围（首次部署 / 新机 / 跨年前瞻不足）则自愈拉 Tushare 落库再加载。
            # 范围 ~6y 历史覆盖 5y 数据 + 30 天前瞻。
            from quantpilot.data.repository import MarketDataRepository
            from quantpilot.services.data_service import bootstrap_trade_calendar

            # required（触发自愈的最低前瞻 +30d）与 fill（自愈时一次性填到的前瞻
            # +90d，与月度刷新 Job 一致）分离：若自愈只填 +30d，月内每日重启都会因
            # 前瞻滑动而反复重拉 6y trade_cal（评审 CAL-C-02）。
            cal_start = today - timedelta(days=365 * 6)
            required_end = today + timedelta(days=30)
            fill_end = today + timedelta(days=90)
            async with AsyncSessionLocal() as cal_session:
                cal_repo = MarketDataRepository(cal_session)
                coverage = await cal_repo.get_trade_calendar_coverage()
                if (
                    coverage is None
                    or coverage[0] > cal_start
                    or coverage[1] < required_end
                ):
                    await bootstrap_trade_calendar(
                        adapter, cal_repo, cal_start, fill_end
                    )
                    await cal_session.commit()
                    logger.info("trade_calendar_self_healed: start=%s end=%s",
                                cal_start, fill_end)
                calendar = await TradingCalendar.from_repo(
                    cal_repo, cal_start, required_end
                )
            app.state.calendar = calendar

            # Phase 10 §4.4 评审 C-02/C-03：BacktestEngine 不再作为单例驻留。
            # 每次 POST /backtest/run 在后台任务中根据 task.config_snapshot 即时构造，
            # 确保用户最新的策略/风险/池配置被消费。

            scheduler = create_scheduler(
                AsyncSessionLocal, adapter, DataValidator(), calendar,
                redis=app.state.redis,
                notification_channel=app.state.wxpusher,
            )
            scheduler.start()
            app.state.scheduler = scheduler
            # Phase 13 §3.2.2：注入 SchedulerHealthService（监听 EVENT_JOB_*
            # 累积 failure_count + 暴露 /health/scheduler 端点）
            from quantpilot.services.scheduler_health import SchedulerHealthService
            app.state.scheduler_health = SchedulerHealthService(scheduler)
            logger.info("scheduler_started")
        except Exception:
            logger.exception("lifespan_init_failed_scheduler_not_started")
    else:
        logger.warning(
            "TUSHARE_TOKEN not configured — data API endpoints will return 503"
        )
        # 无 Tushare token 时仍构造工作日历，支持前端回测演示（BacktestEngine 按任务即时构造）
        try:
            from quantpilot.data.calendar import TradingCalendar
            _today = datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()
            _start = _today - timedelta(days=365 * 3)
            _weekdays = [
                _start + timedelta(days=i)
                for i in range((_today - _start).days + 60)
                if (_start + timedelta(days=i)).weekday() < 5
            ]
            fallback_calendar = TradingCalendar(_weekdays)
            app.state.calendar = fallback_calendar
            logger.info("fallback_calendar_initialized_no_tushare")
        except Exception:
            logger.exception("fallback_calendar_init_failed_no_tushare")

    # 启动回收孤儿回测任务（上次进程因部署/重启/OOM 中断残留的 RUNNING/PENDING → FAILED），
    # 独立 try/except 不阻塞启动。
    try:
        from quantpilot.core.database import AsyncSessionLocal
        from quantpilot.services.backtest_service import reconcile_orphan_backtests

        recovered = await reconcile_orphan_backtests(AsyncSessionLocal)
        if recovered:
            logger.info("orphan_backtests_reconciled count=%d", recovered)
    except Exception:
        logger.exception("orphan_backtest_reconcile_failed")

    yield

    if app.state.scheduler is not None:
        app.state.scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")

    # Phase 14 §14-7 R13-P2-6：lifespan shutdown 释放 Redis client，避免多 worker
    # 启停 + hot reload 下连接泄漏（best-effort：失败仅 warn 不阻断关闭流程）。
    if getattr(app.state, "redis", None) is not None:
        try:
            await app.state.redis.aclose()
            logger.info("redis_closed")
        except Exception:
            logger.warning("redis_close_failed", exc_info=True)


app = FastAPI(
    title="QuantPilot",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Phase 13 §3.1.2：API 请求耗时埋点（middleware）
@app.middleware("http")
async def _api_request_duration_middleware(request, call_next):
    import time

    from quantpilot.core.metrics import API_REQUEST_DURATION

    # 只测 /api/v1/* 路径，避免 /metrics + /health + /docs 自身污染数据
    path = request.url.path
    if not path.startswith("/api/v1/"):
        return await call_next(request)

    started = time.perf_counter()
    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception:
        status_code = 500
        raise
    finally:
        elapsed = time.perf_counter() - started
        # Phase 14 §14-7 R13-P2-4：endpoint 用 route template（如
        # /api/v1/signals/{signal_id}/lineage）替代 raw URL（如
        # /api/v1/signals/123/lineage），避免 Prometheus series 基数爆炸（每
        # signal_id 一个独立 time series）。route 未匹配（404）时 fallback
        # 用 raw path 保留可观测性。
        route = request.scope.get("route")
        endpoint_label = getattr(route, "path", None) or path
        API_REQUEST_DURATION.labels(
            endpoint=endpoint_label, method=request.method, status=str(status_code),
        ).observe(elapsed)
    return response

register_exception_handlers(app)
app.include_router(auth.router, prefix="/api/v1/auth", tags=["认证"])
app.include_router(data.router, prefix="/api/v1/data", tags=["数据"])
app.include_router(market.router, prefix="/api/v1/market", tags=["市场状态"])
app.include_router(watchlist.router, prefix="/api/v1/watchlist", tags=["黑白名单"])
app.include_router(signals.router, prefix="/api/v1/signals", tags=["信号"])
app.include_router(positions.router, prefix="/api/v1/positions", tags=["持仓"])
app.include_router(account.router, prefix="/api/v1/account", tags=["账户"])
app.include_router(settings_router.router, prefix="/api/v1/settings", tags=["设置"])
app.include_router(factor_quality.router, prefix="/api/v1/factor-quality", tags=["因子质量"])
app.include_router(reports.router, prefix="/api/v1/reports", tags=["报告"])
app.include_router(pipeline.router, prefix="/api/v1/pipeline", tags=["流水线"])
app.include_router(performance.router, prefix="/api/v1/performance", tags=["绩效归因"])
app.include_router(backtest.router, prefix="/api/v1/backtest", tags=["回测引擎"])
app.include_router(notifications.router, prefix="/api/v1/notifications", tags=["通知"])
app.include_router(setup.router, prefix="/api/v1/setup", tags=["向导"])
app.include_router(attribution.router, prefix="/api/v1/attribution", tags=["多因子归因"])
# Phase 13 §4.1：Prometheus exposition + 调度器/数据健康端点
app.include_router(metrics.router, prefix="/metrics", tags=["监控"])
app.include_router(health.router, prefix="/api/v1/health", tags=["健康检查"])
# WebSocket 路由（/ws/backtest/{task_id}/progress）复用 backtest.router 中的 ws 端点
# 实际路径：/api/v1/backtest/{task_id}/progress（WebSocket）


@app.get("/health", tags=["系统"])
async def health():
    return {"status": "ok", "version": "1.0.0"}
