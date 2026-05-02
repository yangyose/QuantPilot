"""Pydantic schemas：因子质量 /factor-quality（Phase 7）。"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class FactorIcHistoryItem(BaseModel):
    """GET /factor-quality 和 /factor-quality/history 的 item 结构。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    calc_month: date
    strategy_name: str
    factor_name: str
    ic_value: float | None
    ic_mean_3m: float | None
    ic_std_3m: float | None
    ir_3m: float | None
    half_life_days: float | None
    return_window: int
    alert_status: str | None
