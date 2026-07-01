"""绩效归因 API（Phase 8，SDD §12.1~12.4）。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends

from quantpilot.api.deps import get_current_account_id, get_performance_service
from quantpilot.services.performance_service import PerformanceService

router = APIRouter()


@router.get("/summary")
async def get_performance_summary(
    account_id: int = Depends(get_current_account_id),
    svc: PerformanceService = Depends(get_performance_service),
) -> dict:
    """GET /performance/summary — 7 项基础绩效指标（SDD §12.1）。account_id 由 token 推。"""
    data = await svc.get_summary(account_id=account_id)
    return {"code": 0, "data": data, "msg": "ok"}


@router.get("/history")
async def get_performance_history(
    limit: int = 252,
    account_id: int = Depends(get_current_account_id),
    svc: PerformanceService = Depends(get_performance_service),
) -> dict:
    """GET /performance/history — 净值曲线 + HS300 基准序列。account_id 由 token 推。"""
    data = await svc.get_history(account_id=account_id, limit=limit)
    return {"code": 0, "data": data, "msg": "ok"}


@router.get("/attribution")
async def get_performance_attribution(
    period_start: date,
    period_end: date,
    account_id: int = Depends(get_current_account_id),
    svc: PerformanceService = Depends(get_performance_service),
) -> dict:
    """GET /performance/attribution — 三维归因（SDD §12.2）。period_start/period_end 必填。"""
    data = await svc.get_attribution(
        account_id=account_id,
        period_start=period_start,
        period_end=period_end,
    )
    return {"code": 0, "data": data, "msg": "ok"}


@router.get("/behavior")
async def get_performance_behavior(
    account_id: int = Depends(get_current_account_id),
    svc: PerformanceService = Depends(get_performance_service),
) -> dict:
    """GET /performance/behavior — 行为分析 6 项指标（SDD §12.4）。account_id 由 token 推。"""
    data = await svc.get_behavioral_analysis(account_id=account_id)
    return {"code": 0, "data": data, "msg": "ok"}
