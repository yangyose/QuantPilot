"""REST API：流水线管理 /pipeline（Phase 7）。

Phase 13 §3.7.1：新增 WS /pipeline/progress 端点，订阅 Redis pubsub
quantpilot:pipeline:progress 频道，实时推送 DailyPipeline 各 CP 进度。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.api.deps import get_current_user_id, get_db
from quantpilot.models.system import PipelineRun
from quantpilot.schemas.pipeline import PipelineRunItem, PipelineTriggerRequest

router = APIRouter()
logger = logging.getLogger(__name__)


def _today_cn() -> date:
    return datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()


@router.get("/status")
async def get_pipeline_status(
    trade_date: date | None = None,
    session: AsyncSession = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> dict:
    """GET /pipeline/status — 查询指定日期（或最新）的流水线运行状态。

    无记录 → data: null，HTTP 200。
    """
    if trade_date is None:
        # 取最新一条
        result = await session.execute(
            select(PipelineRun).order_by(PipelineRun.trade_date.desc()).limit(1)
        )
    else:
        result = await session.execute(
            select(PipelineRun).where(PipelineRun.trade_date == trade_date)
        )

    run = result.scalar_one_or_none()
    return {
        "code": 0,
        "data": PipelineRunItem.model_validate(run).model_dump() if run else None,
        "msg": "ok",
    }


@router.post("/trigger")
async def trigger_pipeline(
    request: Request,
    body: PipelineTriggerRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> dict:
    """POST /pipeline/trigger — 手动触发日度流水线。

    - 非交易日 → 400
    - 返回当日 PipelineRun 记录（流水线在后台异步执行）
    """
    trade_date: date = body.trade_date or _today_cn()

    # 检查 calendar（若未初始化抛 503）
    calendar = getattr(request.app.state, "calendar", None)
    if calendar is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="调度服务未初始化（TUSHARE_TOKEN 未配置）",
        )

    if not calendar.is_trade_date(trade_date):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="非交易日，无法触发流水线",
        )

    # 取或创建 PipelineRun（返回给调用方，流水线在后台跑）
    result = await session.execute(
        select(PipelineRun).where(PipelineRun.trade_date == trade_date)
    )
    run = result.scalar_one_or_none()
    if run is None:
        run = PipelineRun(
            trade_date=trade_date,
            status="RUNNING",
            started_at=datetime.now(tz=timezone.utc),
        )
        session.add(run)
        await session.flush()
        await session.refresh(run)

    # Bug 14 修复：必须在 add_task 之前显式 commit，否则形成死锁——
    # Starlette BG tasks 在 get_db async with 上下文内 await，commit 推迟到所有 BG
    # task 结束；但 BG 内 DailyPipeline 自己开 session 也要写同 trade_date 这行，
    # 被 trade_date UNIQUE 约束阻塞等 trigger session commit → 循环死锁。
    await session.commit()

    # 后台触发真实 DailyPipeline（依赖 app.state 中的长期对象）
    adapter = getattr(request.app.state, "adapter", None)
    if adapter is not None:
        from quantpilot.core.database import AsyncSessionLocal
        from quantpilot.data.validators import DataValidator
        from quantpilot.pipeline.daily_pipeline import DailyPipeline

        # R13-P0-1：传入 redis + notification_channel —— 缺失 redis 会让所有
        # 通过 /trigger 启动的流水线 _publish_progress 静默降级为 logger.debug，
        # 导致前端 PipelineProgressCard 对手动触发场景完全无进度推送（仅
        # scheduler cron 触发的流水线才能看到，但 17:00 cron 时段用户已下班）。
        pipeline = DailyPipeline(
            session_factory=AsyncSessionLocal,
            adapter=adapter,
            validator=DataValidator(),
            calendar=calendar,
            redis=getattr(request.app.state, "redis", None),
            notification_channel=getattr(request.app.state, "wxpusher", None),
        )
        background_tasks.add_task(pipeline.run, trade_date)

    return {
        "code": 0,
        "data": PipelineRunItem.model_validate(run).model_dump(),
        "msg": "ok",
    }


@router.websocket("/progress")
async def ws_pipeline_progress(websocket: WebSocket) -> None:
    """WS /pipeline/progress — 订阅 DailyPipeline 实时进度（Phase 13 §3.7.1）。

    消息格式：
        {"trade_date": "2026-05-22", "step": "CP1", "status": "started",
         "progress_pct": 5}

    Redis 未配置时连接接受后立即发送 error 帧并关闭。
    """
    await websocket.accept()
    redis = getattr(websocket.app.state, "redis", None)
    if redis is None:
        # Phase 14 §14-7 R13-P2-5：WS error 帧统一为 REST API 响应格式
        # {code, data, msg}，避免前端为 WS 单独维护一套兼容 schema。
        # （前端 PipelineProgressCard 同批改读 data.code===503 + data.msg。）
        await websocket.send_json(
            {"code": 503, "data": None, "msg": "Redis 未初始化，进度推送不可用"}
        )
        await websocket.close()
        return
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe("quantpilot:pipeline:progress")
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message["data"]
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            await websocket.send_text(data)
    except WebSocketDisconnect:
        logger.debug("ws_pipeline_progress_client_disconnected")
    except Exception as exc:
        logger.debug("ws_pipeline_progress_closed reason=%s", exc)
    finally:
        try:
            await pubsub.unsubscribe("quantpilot:pipeline:progress")
            await pubsub.aclose()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
