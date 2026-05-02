"""Phase 2: index_history 补充 OHLCV；新增 index_component 表

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. index_history 补充 OHLCV 字段（Phase 3 ADX 计算所需）
    op.add_column("index_history", sa.Column("open", sa.Numeric(10, 3), nullable=True))
    op.add_column("index_history", sa.Column("high", sa.Numeric(10, 3), nullable=True))
    op.add_column("index_history", sa.Column("low",  sa.Numeric(10, 3), nullable=True))
    op.add_column("index_history", sa.Column("vol",  sa.BigInteger(),   nullable=True))

    # 2. 新增指数成分股历史表（消除幸存者偏差，SDD §5.2）
    op.create_table(
        "index_component",
        sa.Column("id",         sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("index_code", sa.String(10),   nullable=False),
        sa.Column("ts_code",    sa.String(10),   nullable=False),
        sa.Column("trade_date", sa.Date(),        nullable=False),
        sa.Column("weight",     sa.Numeric(8, 6), nullable=True),
        sa.UniqueConstraint(
            "index_code", "ts_code", "trade_date",
            name="uq_index_component_code_stock_date",
        ),
    )
    op.create_index(
        "idx_index_component_date",
        "index_component",
        ["index_code", "trade_date"],
    )


def downgrade() -> None:
    op.drop_index("idx_index_component_date", table_name="index_component")
    op.drop_table("index_component")
    op.drop_column("index_history", "vol")
    op.drop_column("index_history", "low")
    op.drop_column("index_history", "high")
    op.drop_column("index_history", "open")
