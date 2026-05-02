"""REST API：持仓管理 /positions（Phase 6）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from quantpilot.api.deps import get_account_service, get_current_user
from quantpilot.schemas.account import PositionCreate, PositionItem, PositionUpdate
from quantpilot.services.account_service import AccountService

router = APIRouter()


@router.get("")
async def get_positions(
    account_id: int,
    service: AccountService = Depends(get_account_service),
    _: str = Depends(get_current_user),
) -> dict:
    """GET /positions?account_id=1 → 持仓列表。"""
    positions = await service.get_positions(account_id)
    return {"code": 0, "data": [PositionItem.model_validate(p) for p in positions], "msg": "ok"}


@router.post("")
async def create_position(
    body: PositionCreate,
    service: AccountService = Depends(get_account_service),
    _: str = Depends(get_current_user),
) -> dict:
    """POST /positions → 直接录入持仓（导入历史数据）。"""
    try:
        position = await service.add_position(
            account_id=body.account_id,
            ts_code=body.ts_code,
            shares=body.shares,
            cost_price=body.cost_price,
            open_date=body.open_date,
            phase=body.phase,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return {"code": 0, "data": PositionItem.model_validate(position), "msg": "ok"}


@router.patch("/{position_id}")
async def update_position(
    position_id: int,
    body: PositionUpdate,
    service: AccountService = Depends(get_account_service),
    _: str = Depends(get_current_user),
) -> dict:
    """PATCH /positions/{id} → 更新当前价或 phase。phase 枚举由 Pydantic Literal 校验。"""
    try:
        position = await service.update_position(
            position_id=position_id,
            current_price=body.current_price,
            phase=body.phase,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return {"code": 0, "data": PositionItem.model_validate(position), "msg": "ok"}
