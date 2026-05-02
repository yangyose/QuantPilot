"""Phase 4 评分与黑白名单相关 Pydantic schemas。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

# ── 黑白名单 schemas ──────────────────────────────────────────────────────────

class WatchlistItem(BaseModel):
    ts_code: str
    list_type: Literal["BLACKLIST", "WHITELIST"]
    note: str
    created_at: datetime | None


class WatchlistAddRequest(BaseModel):
    ts_code: str
    list_type: Literal["BLACKLIST", "WHITELIST"]
    note: str = ""


# ── 候选股池 schemas ──────────────────────────────────────────────────────────

class PoolStockItem(BaseModel):
    rank: int
    ts_code: str
    name: str | None
    composite_score: float | None
    trend_score: float | None
    momentum_score: float | None
    reversion_score: float | None
    value_score: float | None
    is_holding: bool
    is_watchlist: bool


class PoolResponse(BaseModel):
    """GET /market/pool 响应体 data 字段"""
    trade_date: date
    market_state: str | None
    pool: list[PoolStockItem]
    total: int


# ── 股票评分历史 schemas ───────────────────────────────────────────────────────

class StockScoreItem(BaseModel):
    trade_date: date
    composite_score: float | None
    trend_score: float | None
    momentum_score: float | None
    reversion_score: float | None
    value_score: float | None
    market_state: str | None


class StockScoreResponse(BaseModel):
    """GET /market/stock/{ts_code}/score 响应体 data 字段"""
    ts_code: str
    history: list[StockScoreItem]
