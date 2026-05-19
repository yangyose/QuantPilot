"""REST API：因子质量监控 /factor-quality（Phase 7 + Phase 11 §9.2 扩展）。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.api.deps import (
    get_current_user,
    get_factor_monitor_service,
)
from quantpilot.core.config_defaults import DEFAULT_STRATEGY_WEIGHTS
from quantpilot.core.database import get_db
from quantpilot.data.factor_ic_repository import FactorICRepository
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.schemas.factor_quality import (
    CurrentWeightsItem,
    FactorIcHistoryItem,
    ICRollingHistoryItem,
)
from quantpilot.services.factor_monitor_service import FactorMonitorService

router = APIRouter()

_VALID_STATES = (
    MarketStateEnum.UPTREND,
    MarketStateEnum.DOWNTREND,
    MarketStateEnum.OSCILLATION,
)
_STRATEGY_NAMES = ("trend", "momentum", "mean_reversion", "value")
_STATE_TO_DEFAULT_ATTR = {
    MarketStateEnum.UPTREND: "uptrend",
    MarketStateEnum.DOWNTREND: "downtrend",
    MarketStateEnum.OSCILLATION: "oscillation",
}


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


# ============================================================
# Phase 11 §9.2：滚动 ICIR 时序 + 当前 active 权重
# ============================================================


@router.get("/ic-history")
async def get_ic_rolling_history(
    strategy: str | None = Query(default=None),
    factor: str | None = Query(default=None),
    state: str | None = Query(default=None),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
    session: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user),
) -> dict:
    """Phase 11 §9.2：GET /factor-quality/ic-history → factor_ic_window_state 时序。

    支持按 strategy / factor / state / 日期窗口过滤；返回 ICIR 聚合行
    （icir IS NOT NULL），按 trade_date 升序，最多 ``limit`` 行（默认 500）。
    """
    repo = FactorICRepository()
    rows = await repo.list_aggregates(
        session,
        strategy=strategy, factor=factor, state=state,
        start_date=start, end_date=end, limit=limit,
    )
    items = [ICRollingHistoryItem.model_validate(r).model_dump(mode="json") for r in rows]
    return {
        "code": 0,
        "data": {"items": items, "total": len(items)},
        "msg": "ok",
    }


@router.get("/current-weights")
async def get_current_strategy_weights(
    as_of: date | None = Query(default=None, description="生效日上界，默认今日"),
    session: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user),
) -> dict:
    """Phase 11 §9.2：GET /factor-quality/current-weights → 3 state × 4 strategy 当前权重。

    每个 state 调 ``FactorICRepository.get_latest_strategy_weights`` 取 active 行；
    无历史时回退 ``DEFAULT_STRATEGY_WEIGHTS``（``weights_source=default_matrix``）。
    """
    repo = FactorICRepository()
    target = as_of or date.today()
    items: list[dict] = []
    for st in _VALID_STATES:
        rows = await repo.get_latest_strategy_weights(session, state=st, as_of=target)
        if rows:
            for r in rows:
                items.append(CurrentWeightsItem.model_validate(r).model_dump(mode="json"))
            # 已写入 strategy 集合补齐缺失策略（异常历史）
            present = {r.strategy for r in rows}
            default_w = getattr(DEFAULT_STRATEGY_WEIGHTS, _STATE_TO_DEFAULT_ATTR[st])
            hyst = rows[0].hysteresis_status
            trade_d = rows[0].trade_date
            for s in _STRATEGY_NAMES:
                if s in present:
                    continue
                items.append({
                    "state": st, "strategy": s, "trade_date": trade_d.isoformat(),
                    "weight_used": float(default_w.get(s, 0.0)),
                    "weights_source": "default_matrix", "hysteresis_status": hyst,
                })
        else:
            # 冷启动：state 无历史 → default_matrix
            default_w = getattr(DEFAULT_STRATEGY_WEIGHTS, _STATE_TO_DEFAULT_ATTR[st])
            for s in _STRATEGY_NAMES:
                items.append({
                    "state": st, "strategy": s, "trade_date": target.isoformat(),
                    "weight_used": float(default_w.get(s, 0.0)),
                    "weights_source": "default_matrix", "hysteresis_status": "stable",
                })
    return {
        "code": 0,
        "data": {"items": items, "total": len(items)},
        "msg": "ok",
    }
