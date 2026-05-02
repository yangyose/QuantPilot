"""Pydantic schemas：绩效归因（Phase 8）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PerformanceSummary(BaseModel):
    """GET /performance/summary 响应体（SDD §12.1 七项基础指标）。"""
    cumulative_return: float | None = None
    annualized_return: float | None = None
    max_drawdown: float | None = None
    sharpe_ratio: float | None = None
    win_rate: float | None = None
    profit_loss_ratio: float | None = None
    benchmark_return: float | None = None


class PerformanceHistory(BaseModel):
    """GET /performance/history 响应体。"""
    nav_series: list[dict[str, Any]]   # [{date: str, nav: float}, ...]
    benchmark_series: list[dict[str, Any]]  # [{date: str, value: float}, ...]


class StockAttribution(BaseModel):
    ts_code: str
    holding_days: int | None = None
    realized_pnl: float | None = None
    realized_pnl_pct: float | None = None


class IndustryAttribution(BaseModel):
    industry: str
    realized_pnl: float | None = None
    trade_count: int | None = None


class StrategyAttribution(BaseModel):
    strategy_name: str
    trade_count: int | None = None
    win_rate: float | None = None
    profit_loss_ratio: float | None = None


class Attribution(BaseModel):
    """GET /performance/attribution 响应体（SDD §12.2 三维归因）。"""
    by_stock: list[StockAttribution] = []
    by_industry: list[IndustryAttribution] = []
    by_strategy: list[StrategyAttribution] = []


class PnlBucket(BaseModel):
    label: str
    count: int


class BehavioralAnalysis(BaseModel):
    """GET /performance/behavior 响应体（SDD §12.4 六项行为指标）。"""
    avg_holding_days: float | None = None
    monthly_trade_count: float | None = None
    signal_compliance_rate: float | None = None
    stop_loss_execution_rate: float | None = None
    chase_up_rate: float | None = None
    pnl_distribution: list[PnlBucket] = []
