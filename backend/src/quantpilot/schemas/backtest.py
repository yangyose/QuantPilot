"""Pydantic schemas：回测引擎（Phase 8）。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class BacktestRunRequest(BaseModel):
    """POST /backtest/run 请求体。

    Phase 10 §4.4：成本率字段改为 Optional；端点层用 `ConfigService.get_backtest_defaults()`
    partial-overlay 未提供字段，支持前端仅提交用户显式修改的字段。
    """
    start_date: date
    end_date: date
    initial_capital: float = Field(default=1_000_000.0, gt=0)
    commission_rate: float | None = Field(default=None, ge=0)
    stamp_tax_rate: float | None = Field(default=None, ge=0)
    slippage_rate: float | None = Field(default=None, ge=0)


class BacktestStatusResponse(BaseModel):
    """GET /backtest/{task_id}/status 响应体。"""
    task_id: str
    status: str
    progress_pct: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_msg: str | None = None


class BacktestResultResponse(BaseModel):
    """GET /backtest/{task_id}/result 响应体。"""
    task_id: str
    performance: dict[str, Any]
    daily_nav: dict[str, float]     # {trade_date_str: nav_value}
    disclaimer: str
