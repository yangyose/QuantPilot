from datetime import date, datetime

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from quantpilot.models.base import Base


class Account(Base):
    __tablename__ = "account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    account_type: Mapped[str] = mapped_column(String(10), default="REAL")  # REAL/PAPER
    broker: Mapped[str | None] = mapped_column(String(50))
    total_assets: Mapped[float | None] = mapped_column(Numeric(15, 2))
    cash: Mapped[float | None] = mapped_column(Numeric(15, 2))
    synced_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )


class Position(Base):
    """持仓。ts_code 不设 FK，应用层校验；account_id FK 保留。"""

    __tablename__ = "position"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account.id"), nullable=False
    )
    ts_code: Mapped[str] = mapped_column(String(10), nullable=False)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_price: Mapped[float | None] = mapped_column(Numeric(10, 3))
    current_price: Mapped[float | None] = mapped_column(Numeric(10, 3))
    market_value: Mapped[float | None] = mapped_column(Numeric(15, 2))
    pnl_pct: Mapped[float | None] = mapped_column(Numeric(8, 4))
    open_date: Mapped[date | None] = mapped_column(Date)
    phase: Mapped[str | None] = mapped_column(String(10))  # BUILD/HOLD/REDUCE
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )

    __table_args__ = (
        UniqueConstraint("account_id", "ts_code", name="uq_position_account_code"),
    )


class TradeRecord(Base):
    """成交记录。ts_code 不设 FK，应用层校验；account_id/signal_id FK 保留。"""

    __tablename__ = "trade_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account.id"), nullable=False
    )
    ts_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_type: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY/SELL
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    price: Mapped[float | None] = mapped_column(Numeric(10, 3))
    shares: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    commission: Mapped[float | None] = mapped_column(Numeric(10, 2))
    stamp_tax: Mapped[float | None] = mapped_column(Numeric(10, 2))
    signal_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("signal.id")
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )

    __table_args__ = (
        Index("idx_trade_record_account_date", "account_id", "trade_date"),
    )


class FundFlow(Base):
    """资金流水。account_id/related_trade_id FK 保留；ts_code 可选，不设 FK。"""

    __tablename__ = "fund_flow"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account.id"), nullable=False
    )
    flow_type: Mapped[str] = mapped_column(String(15), nullable=False)
    # DEPOSIT/WITHDRAW/DIVIDEND/BUY_FEE/SELL_PROCEEDS
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    # 正值=流入，负值=流出
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    ts_code: Mapped[str | None] = mapped_column(String(10))  # 分红时关联股票（可选）
    related_trade_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("trade_record.id")
    )
    note: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )

    __table_args__ = (
        Index("idx_fund_flow_account_date", "account_id", "trade_date"),
    )


class DailyPortfolioValue(Base):
    """净值曲线快照：每日账户总资产/现金/持仓市值（Phase 7 D-06，SDD §12）。"""

    __tablename__ = "daily_portfolio_value"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_value: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    cash: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    position_value: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )

    __table_args__ = (
        UniqueConstraint("account_id", "trade_date", name="uq_dpv_account_date"),
        Index("ix_dpv_account_date", "account_id", text("trade_date DESC")),
    )
