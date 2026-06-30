"""REST API：报告 /reports（Phase 7）。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status

from quantpilot.api.deps import get_current_user_id, get_report_service
from quantpilot.schemas.report import ReportDetail, ReportGenerateRequest, ReportItem
from quantpilot.services.report_service import ReportService

router = APIRouter()


@router.get("")
async def list_reports(
    report_type: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 20,
    offset: int = 0,
    service: ReportService = Depends(get_report_service),
    _: int = Depends(get_current_user_id),
) -> dict:
    """GET /reports → 历史报告列表（分页）。

    report_type 可选过滤（WEEKLY/MONTHLY/CUSTOM）。
    start_date/end_date 按 period_end/period_start 区间过滤。
    """
    records, total = await service.get_list(
        report_type=report_type,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    return {
        "code": 0,
        "data": {
            "items": [ReportItem.model_validate(r).model_dump() for r in records],
            "total": total,
        },
        "msg": "ok",
    }


@router.get("/{report_id}")
async def get_report(
    report_id: int,
    service: ReportService = Depends(get_report_service),
    _: int = Depends(get_current_user_id),
) -> dict:
    """GET /reports/{report_id} → 报告详情（含完整 content JSON）。不存在 → 404。"""
    report = await service.get_by_id(report_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"报告 {report_id} 不存在",
        )
    return {
        "code": 0,
        "data": ReportDetail.model_validate(report).model_dump(),
        "msg": "ok",
    }


@router.post("/generate")
async def generate_report(
    body: ReportGenerateRequest,
    service: ReportService = Depends(get_report_service),
    _: int = Depends(get_current_user_id),
) -> dict:
    """POST /reports/generate → 生成自定义时间段报告。返回 ReportItem（不含 content 全文）。"""
    report = await service.generate_custom(body.start_date, body.end_date)
    return {
        "code": 0,
        "data": ReportItem.model_validate(report).model_dump(),
        "msg": "ok",
    }
