"""Attribution API：多因子归因查询（Phase 12 §4.2）。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from quantpilot.api.deps import get_attribution_service, get_current_user_id
from quantpilot.schemas.attribution import (
    AttributionHistoryItem,
    AttributionHistoryResponse,
    AttributionSummaryResponse,
)
from quantpilot.services.attribution_service import AttributionService

router = APIRouter()


@router.get("/history")
async def get_attribution_history(
    start_date: date = Query(..., description="起始日（含）"),
    end_date: date = Query(..., description="结束日（含）"),
    factor: str | None = Query(default=None, description="可选过滤因子"),
    _: int = Depends(get_current_user_id),
    service: AttributionService = Depends(get_attribution_service),
):
    """GET /api/v1/attribution/history — 归因历史列表（按 calc_date desc）。"""
    if start_date > end_date:
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "data": None,
                "msg": "请求参数校验失败",
                "errors": [{"field": "query.start_date", "reason": "start_date > end_date"}],
            },
        )
    rows = await service.get_history(start_date, end_date, factor=factor)
    response = AttributionHistoryResponse(
        items=[AttributionHistoryItem.model_validate(r) for r in rows],
        total=len(rows),
        start_date=start_date,
        end_date=end_date,
        factor=factor,
    )
    return {"code": 0, "data": response.model_dump(mode="json"), "msg": "ok"}


@router.get("/summary")
async def get_attribution_summary(
    start_date: date = Query(..., description="起始日（含）"),
    end_date: date = Query(..., description="结束日（含）"),
    _: int = Depends(get_current_user_id),
    service: AttributionService = Depends(get_attribution_service),
):
    """GET /api/v1/attribution/summary — 区间累计归因摘要。"""
    if start_date > end_date:
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "data": None,
                "msg": "请求参数校验失败",
                "errors": [{"field": "query.start_date", "reason": "start_date > end_date"}],
            },
        )
    summary = await service.get_summary(start_date, end_date)
    response = AttributionSummaryResponse(
        start_date=summary.start,
        end_date=summary.end,
        cum_beta=summary.cum_beta,
        avg_r_squared=summary.avg_r_squared,
        total_sample=summary.total_sample,
        months=summary.months,
    )
    return {"code": 0, "data": response.model_dump(mode="json"), "msg": "ok"}
