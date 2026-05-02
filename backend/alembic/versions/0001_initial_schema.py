"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ===========================================================
    # 第 1 层：无 FK 依赖
    # ===========================================================

    op.create_table(
        "stock_info",
        sa.Column("ts_code", sa.String(10), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("industry", sa.String(50)),
        sa.Column("sw_industry_l1", sa.String(20)),
        sa.Column("sw_industry_l2", sa.String(20)),
        sa.Column("market", sa.String(10)),
        sa.Column("list_date", sa.Date),
        sa.Column("delist_date", sa.Date),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "account",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("account_type", sa.String(10), server_default="REAL"),
        sa.Column("broker", sa.String(50)),
        sa.Column("total_assets", sa.Numeric(15, 2)),
        sa.Column("cash", sa.Numeric(15, 2)),
        sa.Column("synced_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "index_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("index_code", sa.String(10), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("close", sa.Numeric(10, 3)),
        sa.Column("pct_chg", sa.Numeric(8, 4)),
        sa.UniqueConstraint("index_code", "trade_date", name="uq_index_history_code_date"),
    )
    op.create_index("idx_index_history_code_date", "index_history", ["index_code", "trade_date"])

    op.create_table(
        "system_config",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "user_config",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("config_key", sa.String(100), nullable=False, unique=True),
        sa.Column("config_value", JSONB, nullable=False),
        sa.Column("user_level", sa.String(5), nullable=False, server_default="L2"),
        sa.Column("description", sa.Text),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "pipeline_run",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date, nullable=False, unique=True),
        sa.Column("status", sa.String(10)),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("signal_count", sa.Integer),
        sa.Column("error_msg", sa.Text),
        sa.Column("cp1_data_ready", sa.Boolean, server_default="false"),
        sa.Column("cp1_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("data_snapshot_version", sa.String(64)),
        sa.Column("cp2_scoring_done", sa.Boolean, server_default="false"),
        sa.Column("cp2_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("cp3_signals_done", sa.Boolean, server_default="false"),
        sa.Column("cp3_at", sa.TIMESTAMP(timezone=True)),
    )

    op.create_table(
        "report",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("report_type", sa.String(15), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("generated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_report_type_period", "report", ["report_type", "period_end"])

    op.create_table(
        "factor_ic_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("calc_month", sa.Date, nullable=False),
        sa.Column("strategy_name", sa.String(30), nullable=False),
        sa.Column("factor_name", sa.String(50), nullable=False),
        sa.Column("ic_value", sa.Numeric(8, 6)),
        sa.Column("ic_mean_3m", sa.Numeric(8, 6)),
        sa.Column("ic_std_3m", sa.Numeric(8, 6)),
        sa.Column("ir_3m", sa.Numeric(8, 6)),
        sa.Column("half_life_days", sa.Numeric(6, 1)),
        sa.Column("return_window", sa.Integer, server_default="20"),
        sa.Column("alert_status", sa.String(20)),
        sa.UniqueConstraint(
            "calc_month", "strategy_name", "factor_name", "return_window",
            name="uq_ic_history_month_strategy_factor_window",
        ),
    )
    op.create_index("idx_ic_history_strategy", "factor_ic_history", ["strategy_name", "calc_month"])

    op.create_table(
        "market_state_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date, nullable=False, unique=True),
        sa.Column("market_state", sa.String(20), nullable=False),
        sa.Column("trend_strength", sa.Numeric(5, 2)),
        sa.Column("adx_value", sa.Numeric(6, 3)),
        sa.Column("ma20", sa.Numeric(10, 3)),
        sa.Column("ma60", sa.Numeric(10, 3)),
        sa.Column("state_changed", sa.Boolean, server_default="false"),
        sa.Column("description", sa.Text),
    )

    op.create_table(
        "user_config_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("config_key", sa.String(100), nullable=False),
        sa.Column("old_value", JSONB),
        sa.Column("new_value", JSONB, nullable=False),
        sa.Column("changed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("change_note", sa.Text),
    )
    op.create_index("idx_config_history_key", "user_config_history", ["config_key", "changed_at"])

    # ===========================================================
    # 第 2 层：仅依赖第 1 层
    # ===========================================================

    op.create_table(
        "daily_quote",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts_code", sa.String(10), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("open", sa.Numeric(10, 3)),
        sa.Column("high", sa.Numeric(10, 3)),
        sa.Column("low", sa.Numeric(10, 3)),
        sa.Column("close", sa.Numeric(10, 3)),
        sa.Column("pre_close", sa.Numeric(10, 3)),
        sa.Column("pct_chg", sa.Numeric(8, 4)),
        sa.Column("vol", sa.BigInteger),
        sa.Column("amount", sa.Numeric(15, 3)),
        sa.Column("turnover_rate", sa.Numeric(8, 6)),
        sa.Column("float_mkt_cap", sa.Numeric(18, 2)),
        sa.Column("adj_factor", sa.Numeric(12, 6)),
        sa.Column("is_suspended", sa.Boolean, server_default="false"),
        sa.Column("is_st", sa.Boolean, server_default="false"),
        sa.Column("limit_up", sa.Boolean, server_default="false"),
        sa.Column("limit_down", sa.Boolean, server_default="false"),
        sa.UniqueConstraint("ts_code", "trade_date", name="uq_daily_quote_code_date"),
    )
    op.create_index("idx_daily_quote_date", "daily_quote", ["trade_date"])
    op.create_index("idx_daily_quote_code", "daily_quote", ["ts_code", sa.text("trade_date DESC")])

    op.create_table(
        "financial_data",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts_code", sa.String(10), nullable=False),
        sa.Column("report_period", sa.Date, nullable=False),
        sa.Column("publish_date", sa.Date, nullable=False),
        sa.Column("pe_ttm", sa.Numeric(10, 4)),
        sa.Column("pb", sa.Numeric(8, 4)),
        sa.Column("roe", sa.Numeric(8, 6)),
        sa.Column("net_profit_yoy", sa.Numeric(8, 4)),
        sa.Column("revenue_yoy", sa.Numeric(8, 4)),
        sa.Column("dividend_yield", sa.Numeric(8, 6)),
        sa.Column("total_equity", sa.Numeric(18, 2)),
        sa.Column("debt_to_asset", sa.Numeric(8, 6)),
        sa.UniqueConstraint(
            "ts_code", "report_period", "publish_date",
            name="uq_financial_code_period_publish",
        ),
    )
    op.create_index("idx_financial_code_publish", "financial_data", ["ts_code", "publish_date"])

    op.create_table(
        "signal",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts_code", sa.String(10), nullable=False),
        sa.Column("signal_type", sa.String(10), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("score", sa.Numeric(5, 2)),
        sa.Column("suggested_pct", sa.Numeric(5, 4)),
        sa.Column("suggested_price_low", sa.Numeric(10, 3)),
        sa.Column("suggested_price_high", sa.Numeric(10, 3)),
        sa.Column("stop_loss_price", sa.Numeric(10, 3)),
        sa.Column("signal_strength", sa.String(10)),
        sa.Column("liquidity_note", sa.Text),
        sa.Column("t1_warning", sa.Text),
        sa.Column("reason", sa.Text),
        sa.Column("status", sa.String(15), server_default="NEW"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("ts_code", "trade_date", "signal_type", name="uq_signal_code_date_type"),
    )
    op.create_index("idx_signal_code_date", "signal", ["ts_code", "trade_date"])
    op.create_index("idx_signal_date_type", "signal", ["trade_date", "signal_type"])

    op.create_table(
        "candidate_pool",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts_code", sa.String(10), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("composite_score", sa.Numeric(5, 2)),
        sa.Column("trend_score", sa.Numeric(5, 2)),
        sa.Column("reversion_score", sa.Numeric(5, 2)),
        sa.Column("momentum_score", sa.Numeric(5, 2)),
        sa.Column("value_score", sa.Numeric(5, 2)),
        sa.Column("market_state", sa.String(20)),
        sa.Column("in_pool", sa.Boolean, server_default="true"),
        sa.Column("is_holding", sa.Boolean, server_default="false"),
        sa.UniqueConstraint("ts_code", "trade_date", name="uq_candidate_pool_code_date"),
    )
    op.create_index("idx_pool_date_score", "candidate_pool", ["trade_date", "composite_score"])
    op.create_index("idx_pool_code_date", "candidate_pool", ["ts_code", "trade_date"])

    op.create_table(
        "user_watchlist",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts_code", sa.String(10), nullable=False),
        sa.Column("list_type", sa.String(10), nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("ts_code", "list_type", name="uq_watchlist_code_type"),
    )

    op.create_table(
        "position",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("account.id"), nullable=False),
        sa.Column("ts_code", sa.String(10), nullable=False),
        sa.Column("shares", sa.Integer, nullable=False),
        sa.Column("cost_price", sa.Numeric(10, 3)),
        sa.Column("current_price", sa.Numeric(10, 3)),
        sa.Column("market_value", sa.Numeric(15, 2)),
        sa.Column("pnl_pct", sa.Numeric(8, 4)),
        sa.Column("open_date", sa.Date),
        sa.Column("phase", sa.String(10)),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("account_id", "ts_code", name="uq_position_account_code"),
    )

    # ===========================================================
    # 第 3 层：依赖第 1+2 层
    # ===========================================================

    op.create_table(
        "signal_score_snapshot",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "signal_id",
            sa.BigInteger,
            sa.ForeignKey("signal.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.String(10), nullable=False),
        sa.Column("composite_score", sa.Numeric(5, 2)),
        sa.Column("trend_score", sa.Numeric(5, 2)),
        sa.Column("reversion_score", sa.Numeric(5, 2)),
        sa.Column("momentum_score", sa.Numeric(5, 2)),
        sa.Column("value_score", sa.Numeric(5, 2)),
        sa.Column("market_state", sa.String(20)),
        sa.Column("score_breakdown", JSONB),
        sa.Column("raw_factors", JSONB),
    )
    op.create_index("idx_snapshot_signal", "signal_score_snapshot", ["signal_id"])

    op.create_table(
        "trade_record",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("account.id"), nullable=False),
        sa.Column("ts_code", sa.String(10), nullable=False),
        sa.Column("trade_type", sa.String(10), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("price", sa.Numeric(10, 3)),
        sa.Column("shares", sa.Integer),
        sa.Column("amount", sa.Numeric(15, 2)),
        sa.Column("commission", sa.Numeric(10, 2)),
        sa.Column("stamp_tax", sa.Numeric(10, 2)),
        sa.Column("signal_id", sa.BigInteger, sa.ForeignKey("signal.id")),
        sa.Column("note", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_trade_record_account_date", "trade_record", ["account_id", "trade_date"])

    # ===========================================================
    # 第 4 层：依赖第 1+3 层
    # ===========================================================

    op.create_table(
        "fund_flow",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("account.id"), nullable=False),
        sa.Column("flow_type", sa.String(15), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.String(10)),
        sa.Column("related_trade_id", sa.BigInteger, sa.ForeignKey("trade_record.id")),
        sa.Column("note", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("idx_fund_flow_account_date", "fund_flow", ["account_id", "trade_date"])


def downgrade() -> None:
    # 反序：第 4 → 3 → 2 → 1 层
    op.drop_table("fund_flow")
    op.drop_table("trade_record")
    op.drop_table("signal_score_snapshot")
    op.drop_table("position")
    op.drop_table("user_watchlist")
    op.drop_table("candidate_pool")
    op.drop_table("signal")
    op.drop_table("financial_data")
    op.drop_table("daily_quote")
    op.drop_table("user_config_history")
    op.drop_table("market_state_history")
    op.drop_table("factor_ic_history")
    op.drop_table("report")
    op.drop_table("pipeline_run")
    op.drop_table("user_config")
    op.drop_table("system_config")
    op.drop_table("index_history")
    op.drop_table("account")
    op.drop_table("stock_info")
