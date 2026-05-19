"""Pydantic schemas for signals API（Phase 5）。"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class SignalResponse(BaseModel):
    id: int
    ts_code: str
    name: str | None = None  # 股票名称（由 API 层批量查询注入，ORM 无此字段）
    signal_type: str
    trade_date: date
    score: float | None
    suggested_pct: float | None
    suggested_price_low: float | None
    suggested_price_high: float | None
    stop_loss_price: float | None
    signal_strength: str | None
    liquidity_note: str | None
    t1_warning: str | None
    reason: str | None
    status: str
    created_at: datetime | None
    # Phase 11 §9.1：分位主路径三层输出 + trigger_reason 细分
    composite_z: float | None = None
    composite_pct_in_market: float | None = None
    weights_source: str | None = None
    trigger_reason: str | None = None
    model_config = ConfigDict(from_attributes=True)


class SignalStatusUpdate(BaseModel):
    status: str  # VIEWED / ACTED（API 层校验，仅允许这两个值）


class SignalSnapshotResponse(BaseModel):
    trade_date: date
    composite_score: float | None
    trend_score: float | None
    reversion_score: float | None
    momentum_score: float | None
    value_score: float | None
    market_state: str | None
    score_breakdown: dict | None
    raw_factors: dict | None
    # Phase 11 §9.1：因子级溯源 5 字段（前端分层视图 Phase 12 渲染）
    score_breakdown_raw: dict | None = None
    score_breakdown_residual: dict | None = None
    factor_winsorized: dict | None = None
    factor_neutralized: dict | None = None
    factor_orthogonal: dict | None = None
    model_config = ConfigDict(from_attributes=True)


class SignalLineageResponse(BaseModel):
    signal: SignalResponse
    snapshot: SignalSnapshotResponse | None
