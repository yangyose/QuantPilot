"""REST API：账户管理 /account（Phase 6）。"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status

from quantpilot.api.deps import get_account_service, get_current_user, get_signal_service
from quantpilot.schemas.account import (
    AccountSummary,
    FundFlowCreate,
    FundFlowItem,
    TradeRecordCreate,
    TradeRecordItem,
)
from quantpilot.services.account_service import AccountService
from quantpilot.services.signal_service import SignalService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_account(
    account_id: int | None = None,
    service: AccountService = Depends(get_account_service),
    _: str = Depends(get_current_user),
) -> dict:
    """GET /account?account_id=1 → 账户概览（省略时返回默认账户）。"""
    if account_id is not None:
        account = await service.get_account(account_id)
    else:
        account = await service.get_default_account()

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="无账户记录，请通过初始化脚本创建账户",
        )
    return {"code": 0, "data": AccountSummary.model_validate(account), "msg": "ok"}


@router.post("/sync")
async def sync_account(
    account_id: int,
    service: AccountService = Depends(get_account_service),
    _: str = Depends(get_current_user),
) -> dict:
    """POST /account/sync?account_id=1 → 从 daily_quote 更新持仓价格/市值/total_assets。"""
    try:
        account = await service.sync_account(account_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return {"code": 0, "data": AccountSummary.model_validate(account), "msg": "ok"}


@router.post("/trades")
async def record_trade(
    body: TradeRecordCreate,
    service: AccountService = Depends(get_account_service),
    signal_service: SignalService = Depends(get_signal_service),
    _: str = Depends(get_current_user),
) -> dict:
    """POST /account/trades → 录入成交（BUY/SELL），同步更新持仓和资金流水。

    事务边界：AccountService 和 SignalService 共享同一 AsyncSession（via Depends(get_db)）。
    signal_id 非空时调用 signal_service.update_status(signal_id, "ACTED")；
    状态更新失败仅记录警告，不影响成交录入（避免已完成交易被回滚）。
    """
    try:
        trade = await service.record_trade(
            account_id=body.account_id,
            ts_code=body.ts_code,
            trade_type=body.trade_type,
            trade_date=body.trade_date,
            price=body.price,
            shares=body.shares,
            commission=body.commission,
            stamp_tax=body.stamp_tax,
            signal_id=body.signal_id,
            note=body.note,
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

    if body.signal_id is not None:
        # 【降级说明】设计文档 §7.2 要求 signal_id 不存在时返回 404，
        # 但实现采用"成交优先"策略：trade_record 写入成功后信号状态更新为尽力而为（best-effort），
        # 失败仅记录警告，不回滚成交——原因是已发生的实盘交易不应因信号状态异常被撤销。
        # 恢复条件：若需强一致性，可在 record_trade() 之前预检 signal_id 存在性（独立事务）。
        try:
            await signal_service.update_status(body.signal_id, "ACTED")
        except Exception:
            logger.warning(
                "signal_acted_update_failed signal_id=%s", body.signal_id, exc_info=True
            )

    return {"code": 0, "data": TradeRecordItem.model_validate(trade), "msg": "ok"}


@router.post("/deposit")
async def deposit(
    body: FundFlowCreate,
    service: AccountService = Depends(get_account_service),
    _: str = Depends(get_current_user),
) -> dict:
    """POST /account/deposit → 入金或分红（ts_code 决定类型）。

    隐式分支：ts_code 存在 → 分红（DIVIDEND），否则 → 入金（DEPOSIT）。
    注意：用户若误传 ts_code 会静默走分红路径；单管理员场景下可接受。
    """
    try:
        if body.ts_code:
            # 隐式分支：ts_code 存在 → 分红（DIVIDEND），否则 → 入金（DEPOSIT）
            # 注意：用户若误传 ts_code 会静默走分红路径；单管理员场景下可接受
            flow = await service.record_dividend(
                account_id=body.account_id,
                ts_code=body.ts_code,
                amount=body.amount,
                trade_date=body.trade_date,
                note=body.note,
            )
        else:
            flow = await service.deposit(
                account_id=body.account_id,
                amount=body.amount,
                trade_date=body.trade_date,
                note=body.note,
            )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return {"code": 0, "data": FundFlowItem.model_validate(flow), "msg": "ok"}


@router.post("/withdraw")
async def withdraw(
    body: FundFlowCreate,
    service: AccountService = Depends(get_account_service),
    _: str = Depends(get_current_user),
) -> dict:
    """POST /account/withdraw → 出金。cash 不足返回 400。"""
    try:
        flow = await service.withdraw(
            account_id=body.account_id,
            amount=body.amount,
            trade_date=body.trade_date,
            note=body.note,
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    return {"code": 0, "data": FundFlowItem.model_validate(flow), "msg": "ok"}


@router.get("/cashflow")
async def get_cashflow(
    account_id: int,
    flow_type: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 50,
    offset: int = 0,
    service: AccountService = Depends(get_account_service),
    _: str = Depends(get_current_user),
) -> dict:
    """GET /account/cashflow → 资金流水查询（分页 + 过滤）。

    start_date / end_date 由 FastAPI 自动解析为 date 对象，格式错误返回 422。
    """
    flows, total = await service.get_cashflow(
        account_id=account_id,
        flow_type=flow_type,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    return {
        "code": 0,
        "data": {
            "items": [FundFlowItem.model_validate(f) for f in flows],
            "total": total,
        },
        "msg": "ok",
    }
