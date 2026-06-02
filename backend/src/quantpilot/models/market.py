from datetime import date, datetime

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    Date,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    desc,
)
from sqlalchemy.orm import Mapped, mapped_column

from quantpilot.models.base import Base


class StockInfo(Base):
    __tablename__ = "stock_info"

    ts_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(50))
    sw_industry_l1: Mapped[str | None] = mapped_column(String(20))
    sw_industry_l2: Mapped[str | None] = mapped_column(String(20))
    market: Mapped[str | None] = mapped_column(String(10))  # MAIN/SME/GEM/STAR
    list_date: Mapped[date | None] = mapped_column(Date)
    delist_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )


class DailyQuote(Base):
    """日线行情（原始不复权 + 累乘复权因子）。ts_code 不设 FK，应用层校验。"""

    __tablename__ = "daily_quote"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float | None] = mapped_column(Numeric(10, 3))
    high: Mapped[float | None] = mapped_column(Numeric(10, 3))
    low: Mapped[float | None] = mapped_column(Numeric(10, 3))
    close: Mapped[float | None] = mapped_column(Numeric(10, 3))
    pre_close: Mapped[float | None] = mapped_column(Numeric(10, 3))
    pct_chg: Mapped[float | None] = mapped_column(Numeric(8, 4))
    vol: Mapped[int | None] = mapped_column(BigInteger)
    amount: Mapped[float | None] = mapped_column(Numeric(15, 3))
    turnover_rate: Mapped[float | None] = mapped_column(Numeric(8, 6))
    float_mkt_cap: Mapped[float | None] = mapped_column(Numeric(18, 2))
    adj_factor: Mapped[float | None] = mapped_column(Numeric(12, 6))  # 上市首日基准=1.0
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    is_st: Mapped[bool] = mapped_column(Boolean, default=False)
    limit_up: Mapped[bool] = mapped_column(Boolean, default=False)
    limit_down: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("ts_code", "trade_date", name="uq_daily_quote_code_date"),
        Index("idx_daily_quote_date", "trade_date"),
        Index("idx_daily_quote_code", "ts_code", desc("trade_date")),
    )


class FinancialData(Base):
    """财务数据（PIT 存储，publish_date 为实际可用时点）。ts_code 不设 FK，应用层校验。"""

    __tablename__ = "financial_data"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(10), nullable=False)
    report_period: Mapped[date] = mapped_column(Date, nullable=False)
    publish_date: Mapped[date] = mapped_column(Date, nullable=False)
    pe_ttm: Mapped[float | None] = mapped_column(Numeric(10, 4))
    pb: Mapped[float | None] = mapped_column(Numeric(8, 4))
    roe: Mapped[float | None] = mapped_column(Numeric(8, 6))
    net_profit_yoy: Mapped[float | None] = mapped_column(Numeric(8, 4))
    revenue_yoy: Mapped[float | None] = mapped_column(Numeric(8, 4))
    dividend_yield: Mapped[float | None] = mapped_column(Numeric(8, 6))
    total_equity: Mapped[float | None] = mapped_column(Numeric(18, 2))
    debt_to_asset: Mapped[float | None] = mapped_column(Numeric(8, 6))

    __table_args__ = (
        UniqueConstraint(
            "ts_code", "report_period", "publish_date",
            name="uq_financial_code_period_publish",
        ),
        Index("idx_financial_code_publish", "ts_code", "publish_date"),
    )


class IndexHistory(Base):
    """指数历史（市场状态识别用；含完整 OHLCV，Phase 3 ADX 计算需要 high/low）。"""

    __tablename__ = "index_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    index_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float | None] = mapped_column(Numeric(10, 3))
    high: Mapped[float | None] = mapped_column(Numeric(10, 3))
    low: Mapped[float | None] = mapped_column(Numeric(10, 3))
    close: Mapped[float | None] = mapped_column(Numeric(10, 3))
    vol: Mapped[int | None] = mapped_column(BigInteger)
    pct_chg: Mapped[float | None] = mapped_column(Numeric(8, 4))

    __table_args__ = (
        UniqueConstraint("index_code", "trade_date", name="uq_index_history_code_date"),
        Index("idx_index_history_code_date", "index_code", "trade_date"),
    )


class IndexComponent(Base):
    """指数历史成分股（消除幸存者偏差，SDD §5.2；每月末快照）。"""

    __tablename__ = "index_component"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    index_code: Mapped[str] = mapped_column(String(10), nullable=False)
    ts_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    weight: Mapped[float | None] = mapped_column(Numeric(8, 6))  # 成分权重（小数，可为 NULL）

    __table_args__ = (
        UniqueConstraint(
            "index_code", "ts_code", "trade_date",
            name="uq_index_component_code_stock_date",
        ),
        Index("idx_index_component_date", "index_code", "trade_date"),
    )


class TradeCalendar(Base):
    """A 股交易日历（权威完整性核验基准）。

    全历法日 + is_open 标志：每个自然日一行（含闭市日 is_open=false），忠实
    Tushare trade_cal。is_trade_date 可对范围内任意日期权威作答；daily_quote /
    candidate_pool / index_history 缺交易日核验取 is_open=true 做差集。
    复合主键 (exchange, cal_date)；exchange 默认 'SSE'（A 股沪深同历）。
    见 alembic 0015 + scripts/audit_data_integrity.py。
    """

    __tablename__ = "trade_calendar"

    exchange: Mapped[str] = mapped_column(String(10), primary_key=True, default="SSE")
    cal_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )

    __table_args__ = (
        Index("idx_trade_calendar_open", "exchange", "is_open", "cal_date"),
    )
