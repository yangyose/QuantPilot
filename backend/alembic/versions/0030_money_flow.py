"""V1.5-C C4：新建 money_flow（个股资金流向，Tushare `moneyflow` 列裁剪版）

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-16

**前向且非破坏**：纯 CREATE TABLE，不动任何现有表。

列裁剪与单位（元，adapter 内由万元换算）见 `models/market.py::MoneyFlow` 与设计文档
`docs/design/phases/v1_5_c_strategy_expansion.md` §6.2。北向副因子表 `hk_hold` 已于
v0.13 砍掉（数据源日频停更），本迁移**不建**它。
"""
import sqlalchemy as sa

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "money_flow",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts_code", sa.String(length=10), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("net_mf_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("buy_elg_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("sell_elg_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("buy_lg_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("sell_lg_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ts_code", "trade_date", name="uq_money_flow_code_date"),
    )
    op.create_index("idx_money_flow_date", "money_flow", ["trade_date"])
    op.create_index(
        "idx_money_flow_code_date_desc", "money_flow", ["ts_code", sa.text("trade_date DESC")]
    )


def downgrade() -> None:
    op.drop_index("idx_money_flow_code_date_desc", table_name="money_flow")
    op.drop_index("idx_money_flow_date", table_name="money_flow")
    op.drop_table("money_flow")
