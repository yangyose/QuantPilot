"""Signals API：信号查询与状态管理（Phase 5）。"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: F401
from fastapi.responses import JSONResponse

from quantpilot.api.deps import get_current_user, get_lineage_service, get_repo, get_signal_service
from quantpilot.core.exceptions import SignalNotFoundError
from quantpilot.data.repository import MarketDataRepository
from quantpilot.schemas.signals import (
    SignalResponse,
    SignalStatusUpdate,
)
from quantpilot.services.lineage_service import LineageService
from quantpilot.services.signal_service import SignalService

router = APIRouter()


def _today_cn() -> date:
    return datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()


async def _enrich_with_names(
    signals: list, repo: MarketDataRepository
) -> list[dict]:
    """批量查询股票名称并注入 SignalResponse 字典列表。"""
    ts_codes = [s.ts_code for s in signals]
    try:
        name_df = await repo.get_stock_info_bulk(ts_codes=ts_codes)
        names: dict[str, str] = (
            name_df["name"].to_dict()
            if not name_df.empty and "name" in name_df.columns
            else {}
        )
    except Exception:
        names = {}
    result = []
    for s in signals:
        d = SignalResponse.model_validate(s).model_dump()
        d["name"] = names.get(s.ts_code)
        result.append(d)
    return result


@router.get("")
async def get_signals(
    trade_date: date | None = Query(default=None, description="默认今日"),
    signal_type: str | None = Query(default=None, description="BUY / SELL"),
    status: str | None = Query(default=None, description="NEW / VIEWED / ACTED / EXPIRED"),
    _: str = Depends(get_current_user),
    service: SignalService = Depends(get_signal_service),
    repo: MarketDataRepository = Depends(get_repo),
):
    """GET /api/v1/signals — 今日（或指定日期）信号列表"""
    target_date = trade_date or _today_cn()
    signals = await service.get_today_signals(target_date, signal_type, status)
    signal_dicts = await _enrich_with_names(signals, repo)
    return {
        "code": 0,
        "data": {
            "trade_date": target_date.isoformat(),
            "signals": signal_dicts,
            "total": len(signal_dicts),
        },
        "msg": "ok",
    }


@router.get("/history")
async def get_signal_history(
    ts_code: str | None = Query(default=None),
    signal_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(get_current_user),
    service: SignalService = Depends(get_signal_service),
    repo: MarketDataRepository = Depends(get_repo),
):
    """GET /api/v1/signals/history — 历史信号（分页）"""
    signals = await service.get_signal_history(ts_code, signal_type, status, limit, offset)
    signal_dicts = await _enrich_with_names(signals, repo)
    return {
        "code": 0,
        "data": {
            "signals": signal_dicts,
            "limit": limit,
            "offset": offset,
        },
        "msg": "ok",
    }


@router.patch("/{signal_id}/status")
async def update_signal_status(
    signal_id: int,
    body: SignalStatusUpdate,
    _: str = Depends(get_current_user),
    service: SignalService = Depends(get_signal_service),
):
    """PATCH /api/v1/signals/{id}/status — 更新信号状态（VIEWED / ACTED）"""
    allowed = {"VIEWED", "ACTED"}
    if body.status not in allowed:
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "data": None,
                "msg": "请求参数校验失败",
                "errors": [{"field": "body.status", "reason": f"仅允许 {allowed}"}],
            },
        )
    try:
        updated = await service.update_status(signal_id, body.status)
    except SignalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "data": None, "msg": str(exc)},
        )
    return {
        "code": 0,
        "data": SignalResponse.model_validate(updated).model_dump(),
        "msg": "ok",
    }


@router.get("/{signal_id}/lineage")
async def get_signal_lineage(
    signal_id: int,
    _: str = Depends(get_current_user),
    lineage_service: LineageService = Depends(get_lineage_service),
):
    """GET /api/v1/signals/{id}/lineage — 信号数据血缘（含评分快照与流水线信息）。

    Phase 7：改由 LineageService 提供，返回 signal_id/trade_date/score_snapshot/pipeline_run。
    """
    result = await lineage_service.get_signal_lineage(signal_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")
    return {"code": 0, "data": result, "msg": "ok"}
