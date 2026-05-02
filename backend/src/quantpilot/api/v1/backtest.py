"""回测引擎 API（Phase 8，SDD §7.7）。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, WebSocket, status

from quantpilot.api.deps import get_backtest_service, get_config_service, get_current_user
from quantpilot.engine.backtest.engine import BacktestConfig
from quantpilot.schemas.backtest import BacktestRunRequest
from quantpilot.services.backtest_service import BacktestService
from quantpilot.services.config_service import ConfigService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/run")
async def run_backtest(
    body: BacktestRunRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    _: str = Depends(get_current_user),
    svc: BacktestService = Depends(get_backtest_service),
    cfg: ConfigService = Depends(get_config_service),
) -> dict:
    """POST /backtest/run — 提交回测任务，返回 task_id。

    Phase 10 §4.4：成本率字段 partial-overlay 以 `backtest_defaults` 为兜底；
    端点层并行读取 `get_all_for_snapshot()` 作为 Engine 参数快照，随任务写入
    `backtest_task.config_snapshot` 用于结果复现（与 `pipeline_run.config_snapshot` 同构）。

    Phase 10 §4.4 评审 C-02/C-03：BacktestEngine 不再作为单例从 app.state 取，
    后台任务 `_run_backtest_bg` 内根据 `task.config_snapshot` 即时构造，确保用户
    最新配置消费。前置校验仅检查 `calendar` 是否就绪。
    """
    # 前置校验：交易日历必须已初始化
    calendar = getattr(request.app.state, "calendar", None)
    if calendar is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="交易日历未初始化",
        )
    trade_dates = calendar.get_trade_dates(body.start_date, body.end_date)
    if not trade_dates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"所选日期范围 {body.start_date}～{body.end_date} "
                "内无交易日数据，请先导入行情数据"
            ),
        )

    # Phase 10 §4.4：partial-overlay 成本率
    defaults = await cfg.get_backtest_defaults()
    commission_rate = (
        body.commission_rate if body.commission_rate is not None else defaults.commission_rate
    )
    stamp_tax_rate = (
        body.stamp_tax_rate if body.stamp_tax_rate is not None else defaults.stamp_tax_rate
    )
    slippage_rate = (
        body.slippage_rate if body.slippage_rate is not None else defaults.slippage_rate
    )

    config = BacktestConfig(
        start_date=body.start_date,
        end_date=body.end_date,
        initial_capital=body.initial_capital,
        strategy_config={},
        account_config={},
        commission_rate=commission_rate,
        stamp_tax_rate=stamp_tax_rate,
        slippage_rate=slippage_rate,
    )

    # Phase 10 §4.4：同时取 Engine 参数全量快照，写入 backtest_task.config_snapshot
    engine_snapshot = await cfg.get_all_for_snapshot()
    task_id = await svc.create_task(config, engine_snapshot=engine_snapshot)

    # 在后台任务中异步执行（避免阻塞请求）
    background_tasks.add_task(
        _run_backtest_bg,
        task_id=task_id,
        config=config,
        app_state=request.app.state,
    )

    return {"code": 0, "data": {"task_id": task_id, "status": "PENDING"}, "msg": "ok"}


async def _fail_task(task_id: str, err_msg: str) -> None:
    """独立事务写 FAILED，保证任何异常都不遗留 PENDING 任务。"""
    from datetime import datetime, timezone

    from sqlalchemy import update as sql_update

    from quantpilot.core.database import AsyncSessionLocal
    from quantpilot.models.system import BacktestTask

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    sql_update(BacktestTask)
                    .where(BacktestTask.task_id == task_id)
                    .values(
                        status="FAILED",
                        error_msg=err_msg[:500],
                        finished_at=datetime.now(tz=timezone.utc),
                    )
                )
    except Exception:
        logger.exception("_fail_task_error task_id=%s", task_id)


async def _run_backtest_bg(task_id: str, config: BacktestConfig, app_state: object) -> None:
    """后台任务：根据 task.config_snapshot 构造 BacktestEngine 并执行回测。

    Phase 10 §4.4 评审 C-02/C-03：BacktestEngine 不再来自 app.state 单例，
    本函数从 DB 读出 `backtest_task.config_snapshot`，借助 `from_snapshot` 派生
    所有 dataclass，组装 4 策略 + Scorer + UniverseFilter + SignalGenerator + PositionSizer
    + MarketStateEngine 后再实例化 BacktestEngine——保证用户当前配置真正驱动 Engine 行为。
    """
    import traceback

    from sqlalchemy import select

    from quantpilot.core.database import AsyncSessionLocal
    from quantpilot.engine.backtest.engine import BacktestEngine
    from quantpilot.engine.market_state import MarketStateEngine
    from quantpilot.engine.position import PositionSizer
    from quantpilot.engine.scorer import Scorer
    from quantpilot.engine.signal import SignalGenerator
    from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
    from quantpilot.engine.strategies.momentum import MomentumStrategy
    from quantpilot.engine.strategies.trend import TrendStrategy
    from quantpilot.engine.strategies.value import ValueStrategy
    from quantpilot.engine.universe import UniverseFilter
    from quantpilot.models.system import BacktestTask
    from quantpilot.services.backtest_service import BacktestService
    from quantpilot.services.config_snapshot import from_snapshot

    logger.info("backtest_bg_started task_id=%s", task_id)

    # 确保 app_state 上有进度字典（GIL 保证线程安全的 dict 写入）
    if not hasattr(app_state, "_backtest_progress"):
        app_state._backtest_progress = {}

    try:
        calendar = getattr(app_state, "calendar", None)
        if calendar is None:
            await _fail_task(task_id, "交易日历未初始化")
            return

        # 读取 task.config_snapshot 派生所有 dataclass
        async with AsyncSessionLocal() as snap_session:
            task = (await snap_session.execute(
                select(BacktestTask).where(BacktestTask.task_id == task_id)
            )).scalar_one_or_none()
        if task is None:
            await _fail_task(task_id, "回测任务记录不存在")
            return
        snap = task.config_snapshot or {}

        ms_cfg = from_snapshot(snap, "market_state_params")
        universe_cfg = from_snapshot(snap, "universe_params")
        weights_cfg = from_snapshot(snap, "strategy_weights")
        signal_cfg = from_snapshot(snap, "signal_params")
        trend_cfg = from_snapshot(snap, "strategy_params_trend")
        momentum_cfg = from_snapshot(snap, "strategy_params_momentum")
        mr_cfg = from_snapshot(snap, "strategy_params_mean_reversion")
        value_cfg = from_snapshot(snap, "strategy_params_value")

        backtest_engine = BacktestEngine(
            strategies=[
                TrendStrategy(trend_cfg),
                MomentumStrategy(momentum_cfg),
                MeanReversionStrategy(mr_cfg),
                ValueStrategy(value_cfg),
            ],
            market_state_engine=MarketStateEngine(ms_cfg),
            universe_filter=UniverseFilter(universe_cfg),
            scorer=Scorer(weights_cfg),
            signal_engine=SignalGenerator(signal_cfg=signal_cfg, universe_cfg=universe_cfg),
            position_engine=PositionSizer(),
            price_provider=None,
            calendar=calendar,
        )

        # 初始化进度记录
        app_state._backtest_progress[task_id] = {
            "progress_pct": 0,
            "current_nav": 1.0,
            "trade_date": None,
        }

        # Redis 进度回调（若 redis 不可用则跳过）
        redis = getattr(app_state, "redis", None)
        redis_cb = _make_redis_progress_cb(task_id, redis) if redis is not None else None

        def combined_progress_cb(
            trade_date_str: str, progress_pct: int, current_nav: float
        ) -> None:
            """同步回调（在 asyncio.to_thread 子线程中调用）：更新内存进度 + Redis。"""
            app_state._backtest_progress[task_id] = {
                "progress_pct": progress_pct,
                "current_nav": round(current_nav, 6),
                "trade_date": trade_date_str,
            }
            if redis_cb is not None:
                redis_cb(trade_date_str, progress_pct, current_nav)

        async with AsyncSessionLocal() as session:
            svc = BacktestService(session, backtest_engine)
            await svc.run_task(task_id, config, combined_progress_cb)

    except Exception:
        tb = traceback.format_exc()
        logger.exception("backtest_bg_task_failed task_id=%s", task_id)
        await _fail_task(task_id, f"后台执行异常: {tb[-300:]}")
    finally:
        app_state._backtest_progress.pop(task_id, None)


def _make_redis_progress_cb(task_id: str, redis: object):
    """
    创建 Redis PUBLISH 进度回调（在 asyncio.to_thread 子线程中调用）。

    在 async 上下文调用本函数时用 asyncio.get_running_loop() 预捕获 loop，
    避免在子线程内调用已弃用的 asyncio.get_event_loop()（Python 3.12 DeprecationWarning）。
    """
    import asyncio
    import json

    loop = asyncio.get_running_loop()  # 在 async 上下文中捕获，此时 loop 确定在运行
    channel = f"backtest:{task_id}:progress"

    def cb(trade_date_str: str, progress_pct: int, current_nav: float) -> None:
        msg = json.dumps({
            "trade_date": trade_date_str,
            "progress_pct": progress_pct,
            "current_nav": round(current_nav, 6),
        })
        try:
            asyncio.run_coroutine_threadsafe(redis.publish(channel, msg), loop)
        except Exception:
            pass

    return cb


@router.get("/{task_id}/status")
async def get_backtest_status(
    task_id: str,
    request: Request,
    _: str = Depends(get_current_user),
    svc: BacktestService = Depends(get_backtest_service),
) -> dict:
    """GET /backtest/{task_id}/status — 查询回测任务状态（含实时进度）。"""
    task = await svc.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="回测任务不存在")
    # 读取内存进度（仅 RUNNING 阶段有值；其余阶段已被清理为 None）
    progress_store: dict = getattr(request.app.state, "_backtest_progress", {})
    prog = progress_store.get(task_id, {})
    return {
        "code": 0,
        "data": {
            "task_id": task.task_id,
            "status": task.status,
            "progress_pct": prog.get("progress_pct"),
            "current_nav": prog.get("current_nav"),
            "trade_date": prog.get("trade_date"),
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
            "error_msg": task.error_msg,
        },
        "msg": "ok",
    }


@router.get("/{task_id}/result")
async def get_backtest_result(
    task_id: str,
    _: str = Depends(get_current_user),
    svc: BacktestService = Depends(get_backtest_service),
) -> dict:
    """GET /backtest/{task_id}/result — 查询回测结果。"""
    task = await svc.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="回测任务不存在")
    if task.status in ("PENDING", "RUNNING"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"回测任务未完成（{task.status}）",
        )
    if task.status == "FAILED":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"回测任务失败：{task.error_msg}",
        )
    result = await svc.get_result(task_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="回测结果不存在")
    return {
        "code": 0,
        "data": {
            "task_id": task_id,
            "performance": result.performance_json,
            "daily_nav": result.daily_nav_json,
            "disclaimer": result.disclaimer,
        },
        "msg": "ok",
    }


@router.websocket("/{task_id}/progress")
async def ws_backtest_progress(
    task_id: str,
    websocket: WebSocket,
) -> None:
    """WS /backtest/{task_id}/progress — 订阅回测进度（Redis Pub/Sub）。"""
    await websocket.accept()
    redis = getattr(websocket.app.state, "redis", None)
    if redis is None:
        await websocket.send_json({"error": "Redis 未初始化，进度推送不可用"})
        await websocket.close()
        return

    channel = f"backtest:{task_id}:progress"
    try:
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except Exception as exc:
        logger.debug("ws_backtest_progress_closed task_id=%s reason=%s", task_id, exc)
    finally:
        await websocket.close()
