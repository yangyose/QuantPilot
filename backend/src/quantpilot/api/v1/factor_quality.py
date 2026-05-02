"""REST API：因子质量监控 /factor-quality（Phase 7）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from quantpilot.api.deps import get_current_user, get_factor_monitor_service
from quantpilot.schemas.factor_quality import FactorIcHistoryItem
from quantpilot.services.factor_monitor_service import FactorMonitorService

router = APIRouter()


@router.get("")
async def get_factor_quality(
    strategy_name: str | None = None,
    service: FactorMonitorService = Depends(get_factor_monitor_service),
    _: str = Depends(get_current_user),
) -> dict:
    """GET /factor-quality → 每个（strategy, factor）最新一条 IC 质量记录。

    strategy_name 可选过滤。calc_month 随每条 item 返回（不同策略可能来自不同月份）。
    """
    records = await service.get_latest(strategy_name=strategy_name)
    return {
        "code": 0,
        "data": {
            "items": [FactorIcHistoryItem.model_validate(r).model_dump() for r in records],
        },
        "msg": "ok",
    }


@router.get("/history")
async def get_factor_quality_history(
    strategy_name: str | None = None,
    factor_name: str | None = None,
    limit: int = 12,
    service: FactorMonitorService = Depends(get_factor_monitor_service),
    _: str = Depends(get_current_user),
) -> dict:
    """GET /factor-quality/history → 历史 IC 趋势（分页）。"""
    records, total = await service.get_history(
        strategy_name=strategy_name,
        factor_name=factor_name,
        limit=limit,
    )
    return {
        "code": 0,
        "data": {
            "items": [FactorIcHistoryItem.model_validate(r).model_dump() for r in records],
            "total": total,
        },
        "msg": "ok",
    }
