"""REST API：流水线管理 /pipeline（Phase 7）。"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.api.deps import get_current_user, get_db
from quantpilot.models.system import PipelineRun
from quantpilot.schemas.pipeline import PipelineRunItem, PipelineTriggerRequest

router = APIRouter()


def _today_cn() -> date:
    return datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()


@router.get("/status")
async def get_pipeline_status(
    trade_date: date | None = None,
    session: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user),
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
    _: str = Depends(get_current_user),
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

    # 后台触发真实 DailyPipeline（依赖 app.state 中的长期对象）
    adapter = getattr(request.app.state, "adapter", None)
    if adapter is not None:
        from quantpilot.core.database import AsyncSessionLocal
        from quantpilot.data.validators import DataValidator
        from quantpilot.pipeline.daily_pipeline import DailyPipeline

        pipeline = DailyPipeline(
            session_factory=AsyncSessionLocal,
            adapter=adapter,
            validator=DataValidator(),
            calendar=calendar,
        )
        background_tasks.add_task(pipeline.run, trade_date)

    return {
        "code": 0,
        "data": PipelineRunItem.model_validate(run).model_dump(),
        "msg": "ok",
    }
