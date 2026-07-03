"""Signals API：信号查询与状态管理（Phase 5）。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: F401
from fastapi.responses import JSONResponse

from quantpilot.api.deps import (
    get_current_account_id,
    get_current_user_id,
    get_lineage_service,
    get_repo,
    get_signal_service,
    get_signal_view_service,
)
from quantpilot.core.exceptions import SignalNotFoundError
from quantpilot.data.repository import MarketDataRepository
from quantpilot.schemas.signals import (
    SignalLineageResponse,
    SignalResponse,
    SignalStatusUpdate,
)
from quantpilot.services.lineage_service import LineageService
from quantpilot.services.signal_service import SignalService
from quantpilot.services.signal_view_service import SignalViewService

router = APIRouter()


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
    trade_date: date | None = Query(
        default=None, description="缺省返回最新有信号的交易日；指定则查该日"
    ),
    signal_type: str | None = Query(default=None, description="BUY / SELL"),
    status: str | None = Query(default=None, description="NEW / VIEWED / ACTED / EXPIRED"),
    _: int = Depends(get_current_user_id),
    account_id: int = Depends(get_current_account_id),
    service: SignalService = Depends(get_signal_service),
    repo: MarketDataRepository = Depends(get_repo),
    view_service: SignalViewService = Depends(get_signal_view_service),
):
    """GET /api/v1/signals — 最新可用信号列表（或指定日期）。

    信号是收盘后每日一次产出，缺省查字面今天在盘中/周末/节假日必然为空。
    故 trade_date 缺省时回退到最近一个有信号的交易日；显式传 trade_date 则查该日。
    响应 trade_date 反映实际信号日期（无任何信号时为 null）。

    V1.5-G G-4d-2（§2 派生语义）：信号本身是账户无关的共享数据（管线产出），响应
    组装期经 SignalViewService 按当前账户叠加 is_holding + 仓位建议 suggested_pct。
    """
    if trade_date is not None:
        target_date: date | None = trade_date
        signals = await service.get_today_signals(trade_date, signal_type, status)
    else:
        signals, target_date = await service.get_latest_signals(signal_type, status)
    signal_dicts = await _enrich_with_names(signals, repo)
    await view_service.apply_account_overlay(signal_dicts, account_id)
    return {
        "code": 0,
        "data": {
            "trade_date": target_date.isoformat() if target_date else None,
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
    _: int = Depends(get_current_user_id),
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
    _: int = Depends(get_current_user_id),
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
    _: int = Depends(get_current_user_id),
    lineage_service: LineageService = Depends(get_lineage_service),
):
    """GET /api/v1/signals/{id}/lineage — 信号数据血缘（三层 schema，Phase 12 P12-A）。

    返回 `SignalLineageResponse`（19 字段 score_snapshot + pipeline_run），
    详见 phase12_factor_lineage.md §3.1.3。响应 data 字段经
    `SignalLineageResponse` 校验后序列化，确保字段名、可空性与文档一致。
    """
    result = await lineage_service.get_signal_lineage(signal_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")
    validated = SignalLineageResponse.model_validate(result)
    return {"code": 0, "data": validated.model_dump(), "msg": "ok"}
