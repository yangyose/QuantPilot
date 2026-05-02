"""Pydantic schemas for account/position/fund-flow API（Phase 6）。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


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
    shares: int
    cost_price: float | None
    current_price: float | None
    market_value: float | None
    pnl_pct: float | None
    open_date: date | None
    phase: Literal["BUILD", "HOLD", "REDUCE"] | None


class PositionCreate(BaseModel):
    account_id: int
    ts_code: str
    shares: int
    cost_price: float
    open_date: date | None = None
    phase: Literal["BUILD", "HOLD", "REDUCE"] | None = "BUILD"


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
    trade_type: str
    trade_date: date
    price: float | None
    shares: int | None
    amount: float | None
    commission: float | None
    stamp_tax: float | None
    signal_id: int | None
    note: str | None
    created_at: datetime | None


class FundFlowCreate(BaseModel):
    """POST /account/deposit 和 /account/withdraw 共用。

    deposit 路由：ts_code 有值 → DIVIDEND（分红），无值 → DEPOSIT（入金）。
    withdraw 路由：flow_type 固定为 WITHDRAW，ts_code 忽略。
    """

    account_id: int
    amount: float
    trade_date: date
    ts_code: str | None = None
    note: str | None = None


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
    created_at: datetime | None


class CashflowResponse(BaseModel):
    items: list[FundFlowItem]
    total: int
