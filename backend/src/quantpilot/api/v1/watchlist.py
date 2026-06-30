"""黑白名单 API（Phase 4）：GET/POST/DELETE /api/v1/watchlist。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from quantpilot.api.deps import get_current_user_id, get_watchlist_service
from quantpilot.schemas.scoring import WatchlistAddRequest
from quantpilot.services.watchlist_service import WatchlistService

router = APIRouter()


@router.get("")
async def list_watchlist(
    list_type: Literal["BLACKLIST", "WHITELIST"] | None = Query(default=None),
    _: int = Depends(get_current_user_id),
    svc: WatchlistService = Depends(get_watchlist_service),
) -> JSONResponse:
    """GET /api/v1/watchlist — 查询黑白名单，可按 list_type 过滤。"""
    items = await svc.get_list(list_type=list_type)
    return JSONResponse({
        "code": 0,
        "data": [item.model_dump(mode="json") for item in items],
        "msg": "ok",
    })


@router.post("")
async def add_watchlist(
    body: WatchlistAddRequest,
    _: int = Depends(get_current_user_id),
    svc: WatchlistService = Depends(get_watchlist_service),
) -> JSONResponse:
    """POST /api/v1/watchlist — 添加黑白名单条目（幂等）。"""
    item = await svc.add(ts_code=body.ts_code, list_type=body.list_type, note=body.note)
    return JSONResponse({
        "code": 0,
        "data": item.model_dump(mode="json"),
        "msg": "ok",
    })


@router.delete("/{ts_code}")
async def remove_watchlist(
    ts_code: str,
    list_type: Literal["BLACKLIST", "WHITELIST"] = Query(...),
    _: int = Depends(get_current_user_id),
    svc: WatchlistService = Depends(get_watchlist_service),
) -> JSONResponse:
    """DELETE /api/v1/watchlist/{ts_code}?list_type=... — 删除条目（幂等）。"""
    await svc.remove(ts_code=ts_code, list_type=list_type)
    return JSONResponse({"code": 0, "data": None, "msg": "ok"})
