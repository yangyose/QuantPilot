"""Pydantic schemas for account/position/fund-flow API（Phase 6）。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AccountSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    account_type: str
    broker: str | None
    total_assets: float | None
    cash: float | None
    synced_at: datetime | None


class PositionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    ts_code: str
    name: str | None = None  # 股票名称（端点从 stock_info 富化；ORM 无此列故默认 None）
    shares: int
    cost_price: float | None
    current_price: float | None
    market_value: float | None
    pnl_pct: float | None
    open_date: date | None
    phase: Literal["BUILD", "HOLD", "REDUCE"] | None


class PositionUpdate(BaseModel):
    current_price: float | None = None
    phase: Literal["BUILD", "HOLD", "REDUCE"] | None = None


class TradeRecordCreate(BaseModel):
    account_id: int
    ts_code: str
    trade_type: Literal["BUY", "SELL"]
    trade_date: date
    price: float
    shares: int
    commission: float = 0.0
    stamp_tax: float = 0.0
    signal_id: int | None = None
    note: str | None = None


class TradeRecordItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    ts_code: str
    name: str | None = None  # 股票名称（端点从 stock_info 富化；ORM 无此列故默认 None）
    trade_type: str
    trade_date: date
    price: float | None
    shares: int | None
    amount: float | None
    commission: float | None
    stamp_tax: float | None
    signal_id: int | None
    note: str | None
    is_voided: bool = False
    voided_at: datetime | None = None
    void_note: str | None = None
    created_at: datetime | None


class FundFlowCreate(BaseModel):
    """POST /account/deposit 和 /account/withdraw 共用。

    deposit 路由：ts_code 有值 → DIVIDEND（分红），无值 → DEPOSIT（入金）。
    withdraw 路由：flow_type 固定为 WITHDRAW，ts_code 忽略。

    Phase 14 §14-1：新增 idempotency_key 可选字段保护 deposit/dividend 重复提交
    （客户端网络抖动 / 浏览器双击）。withdraw 路径默认忽略 idempotency_key
    （出金本身已有现金余额二次校验）。
    """

    account_id: int
    amount: float
    trade_date: date
    ts_code: str | None = None
    note: str | None = None
    idempotency_key: str | None = Field(
        None, max_length=36, pattern=r"^[A-Za-z0-9_\-]+$",
    )


class FundFlowItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    flow_type: str
    amount: float
    trade_date: date
    ts_code: str | None
    related_trade_id: int | None
    note: str | None
    idempotency_key: str | None = None
    is_voided: bool = False
    voided_at: datetime | None = None
    void_note: str | None = None
    created_at: datetime | None


class CashflowResponse(BaseModel):
    items: list[FundFlowItem]
    total: int


class VoidRequest(BaseModel):
    """作废成交 / 资金流水的请求体。void_note 为订正说明（可选但建议填写）。"""

    void_note: str | None = Field(None, max_length=500)
