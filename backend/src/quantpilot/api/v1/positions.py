"""REST API：持仓管理 /positions（Phase 6）。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from quantpilot.api.deps import get_account_service, get_current_user_id
from quantpilot.schemas.account import PositionItem, PositionUpdate
from quantpilot.services.account_service import AccountService

logger = logging.getLogger(__name__)

router = APIRouter()


async def _resolve_names(service: AccountService, ts_codes: list[str]) -> dict[str, str]:
    """从 stock_info 富化股票名称（best-effort）。失败/无数据返回 {}，ts_code 始终在。"""
    try:
        names = await service.get_stock_names(ts_codes)
    except Exception:
        logger.exception("stock name enrichment failed (positions)")
        return {}
    return names if isinstance(names, dict) else {}

# 注：持仓是成交流水的派生视图（持仓 = replay(非作废成交 + 分红)）。故**不提供**直接
# 录入/插入持仓的端点——绕过成交流水的手工持仓没有 trade_record 支撑，一旦 void/replay
# 触及该 ts_code 会被重建逻辑丢弃，且不扣现金致账务不整合（2026-06-24 废除 POST /positions）。
# 建仓/导入已有持仓 → 走 POST /account/trades 录一笔开仓 BUY（先入金总本金）。


@router.get("")
async def get_positions(
    account_id: int,
    service: AccountService = Depends(get_account_service),
    _: int = Depends(get_current_user_id),
) -> dict:
    """GET /positions?account_id=1 → 持仓列表（含股票名称富化）。"""
    positions = await service.get_positions(account_id)
    names = await _resolve_names(service, [p.ts_code for p in positions])
    items = []
    for p in positions:
        item = PositionItem.model_validate(p)
        item.name = names.get(p.ts_code)
        items.append(item)
    return {"code": 0, "data": items, "msg": "ok"}


@router.patch("/{position_id}")
async def update_position(
    position_id: int,
    body: PositionUpdate,
    service: AccountService = Depends(get_account_service),
    _: int = Depends(get_current_user_id),
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
