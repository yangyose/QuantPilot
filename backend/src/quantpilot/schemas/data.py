from datetime import date

from pydantic import BaseModel


class DataStatus(BaseModel):
    latest_quote_date: date | None
    stock_count: int
    index_codes: list[str]
    is_up_to_date: bool
    latest_financial_date: date | None


class IngestDailyRequest(BaseModel):
    trade_date: date | None = None  # None 时默认最近交易日


class IngestHistoryRequest(BaseModel):
    start_date: date
    end_date: date


class IngestResultSchema(BaseModel):
    """POST /api/v1/data/ingest/daily 响应中的 data 字段"""

    trade_date: date
    quote_count: int
    financial_count: int
    snapshot_version: str
    duration_seconds: float = 0.0
    errors: list[str] = []
