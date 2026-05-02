"""Pydantic schemas：报告 /reports（Phase 7）。"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class ReportItem(BaseModel):
    """GET /reports 列表条目（不含 content 全文）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    report_type: str
    period_start: date
    period_end: date
    summary: str | None
    generated_at: datetime | None


class ReportDetail(ReportItem):
    """GET /reports/{id} 详情（含完整 content JSON）。"""

    content: dict


class ReportGenerateRequest(BaseModel):
    """POST /reports/generate 请求体。"""

    start_date: date
    end_date: date
