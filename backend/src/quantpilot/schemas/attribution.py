"""Pydantic schemas for /attribution/* API（Phase 12 §4.2）。"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class AttributionHistoryItem(BaseModel):
    """单条归因记录响应项。"""

    calc_date: date
    factor: str
    beta: float
    t_stat: float | None
    residual_std: float | None
    r_squared: float | None
    sample_size: int
    window_days: int
    created_at: datetime | None
    model_config = ConfigDict(from_attributes=True)


class AttributionHistoryResponse(BaseModel):
    """GET /attribution/history 响应 data。"""

    items: list[AttributionHistoryItem]
    total: int
    start_date: date
    end_date: date
    factor: str | None = None


class AttributionSummaryResponse(BaseModel):
    """GET /attribution/summary 响应 data：区间累计每因子 cum_beta + 平均 R² + 总样本。"""

    start_date: date
    end_date: date
    cum_beta: dict[str, float]
    avg_r_squared: float | None
    total_sample: int
    months: int
