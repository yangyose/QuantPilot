from datetime import date

from pydantic import BaseModel


class MarketStateItem(BaseModel):
    trade_date: date
    market_state: str  # "UPTREND" / "DOWNTREND" / "OSCILLATION"
    trend_strength: float
    adx_value: float
    ma20: float
    ma60: float
    state_changed: bool
    description: str


class MarketStateResponse(BaseModel):
    """GET /market/state 响应体 data 字段"""
    current: MarketStateItem | None  # None 表示尚未计算（数据库为空）


class MarketStateHistoryResponse(BaseModel):
    """GET /market/state/history 响应体 data 字段"""
    items: list[MarketStateItem]
    total: int
