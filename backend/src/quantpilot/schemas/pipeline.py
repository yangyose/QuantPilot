"""Pydantic schemas：Pipeline /pipeline（Phase 7）。"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class PipelineRunItem(BaseModel):
    """GET /pipeline/status 和 POST /pipeline/trigger 的响应结构。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    trade_date: date
    status: str | None
    started_at: datetime | None
    finished_at: datetime | None
    signal_count: int | None
    cp1_data_ready: bool
    cp1_at: datetime | None
    data_snapshot_version: str | None
    cp2_scoring_done: bool
    cp2_at: datetime | None
    cp3_signals_done: bool
    cp3_at: datetime | None
    error_msg: str | None


class PipelineTriggerRequest(BaseModel):
    """POST /pipeline/trigger 请求体（trade_date 可选）。"""

    trade_date: date | None = None
