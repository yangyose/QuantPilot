from __future__ import annotations

import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from quantpilot.api.deps import get_current_user, get_data_service
from quantpilot.schemas.data import (
    DataStatus,
    IngestDailyRequest,
    IngestHistoryRequest,
    IngestResultSchema,
)
from quantpilot.services.data_service import DataService

router = APIRouter()


@router.get("/status")
async def get_status(
    _: str = Depends(get_current_user),
    service: DataService = Depends(get_data_service),
):
    """GET /api/v1/data/status — 数据新鲜度摘要"""
    raw = await service.get_status()
    status = DataStatus(**raw)  # 校验字段完整性，确保与 schema 一致
    return {"code": 0, "data": status.model_dump(), "msg": "ok"}


@router.post("/ingest/daily")
async def ingest_daily(
    body: IngestDailyRequest,
    _: str = Depends(get_current_user),
    service: DataService = Depends(get_data_service),
):
    """POST /api/v1/data/ingest/daily — 手动触发单日采集"""
    trade_date: date = body.trade_date or datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()
    start = time.perf_counter()
    try:
        result = await service.ingest_daily(trade_date)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "data": None, "msg": str(exc)},
        )
    duration = time.perf_counter() - start
    schema = IngestResultSchema(
        trade_date=result.trade_date,
        quote_count=result.quote_count,
        financial_count=result.financial_count,
        snapshot_version=result.snapshot_version,
        duration_seconds=round(duration, 2),
        errors=result.errors,
    )
    return {"code": 0, "data": schema.model_dump(), "msg": "ok"}


@router.post("/ingest/history")
async def ingest_history(
    body: IngestHistoryRequest,
    _: str = Depends(get_current_user),
    service: DataService = Depends(get_data_service),
):
    """POST /api/v1/data/ingest/history — 历史回填（Phase 2 同步执行）

    【降级说明】Phase 2 同步阻塞执行，HTTP 状态码返回 200；设计规格（Phase 2 §4.2）要求
    202 Accepted + 真实 task_id。Phase 9 接入 APScheduler 任务队列后改为真实异步模式，
    task_id 改为真实任务 ID，状态码改为 202。
    """
    summary = await service.ingest_history(body.start_date, body.end_date)
    return {
        "code": 0,
        "data": {
            "task_id": f"backfill-{body.start_date}-{body.end_date}",
            "status": "completed",
            **summary,
        },
        "msg": "ok",
    }


@router.post("/refresh/stock-list")
async def refresh_stock_list(
    _: str = Depends(get_current_user),
    service: DataService = Depends(get_data_service),
):
    """POST /api/v1/data/refresh/stock-list — 刷新全市场股票基础信息"""
    result = await service.refresh_stock_list()
    return {"code": 0, "data": result, "msg": "ok"}
