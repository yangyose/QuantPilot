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
    # 本地算力中心回流回测的数据基线日（None=生产机直接跑、无戳）
    data_baseline: str | None = None
    # V1.5-A A1b：滑点情景对比（本地算力中心跑 run_slippage_comparison 回流；
    # 无则 None，前端不展示滑点表）。每档 {slippage, total_return, max_drawdown,
    # sharpe, annualized_return, pipeline_mode}。
    slippage_comparison: list[dict[str, Any]] | None = None


class BacktestImportRequest(BaseModel):
    """POST /backtest/import 请求体（2026-06-15 本地算力中心回流）。

    长区间回测在本地大内存机跑（避开 2GB 生产机 OOM），跑完把 task+result 两行
    经此端点回流生产 DB，使生产 Web 也能查看。task_id 是本地生成的 UUID4，与
    生产任务永不撞号；端点按 task_id 幂等去重（已存在则跳过、不覆盖）。

    config_snapshot 应含 `data_baseline`（本地库 daily_quote 的 max trade_date），
    供生产 Web 标注「本结果基于截至 X 日的数据」。
    """
    task_id: str
    config_json: dict[str, Any]
    config_snapshot: dict[str, Any] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    performance: dict[str, Any]
    daily_nav: dict[str, float]
    disclaimer: str
    # V1.5-A A1：本地跑出的每日持仓明细，回流到 backtest_daily_position（可选，向后兼容）
    daily_positions: list[dict[str, Any]] | None = None
    # V1.5-A A1b：滑点情景对比（可选）；回流时折入 performance_json["slippage_comparison"]，
    # GET /result 再提出为顶层字段返回（无需新表/迁移）。
    slippage_comparison: list[dict[str, Any]] | None = None
