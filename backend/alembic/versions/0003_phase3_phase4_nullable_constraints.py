"""Phase 3 and 4: fix nullable constraints on boolean/enum columns

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-02

ORM models (Phase 3/4) declare several columns as NOT NULL that the original
0001 migration left nullable.  This migration aligns the DB schema with the
current ORM definitions without touching the table structure itself.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "account", "account_type",
        existing_type=sa.VARCHAR(length=10),
        nullable=False,
        existing_server_default=sa.text("'REAL'::character varying"),
    )
    op.alter_column(
        "candidate_pool", "in_pool",
        existing_type=sa.BOOLEAN(),
        nullable=False,
        existing_server_default=sa.text("true"),
    )
    op.alter_column(
        "candidate_pool", "is_holding",
        existing_type=sa.BOOLEAN(),
        nullable=False,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "daily_quote", "is_suspended",
        existing_type=sa.BOOLEAN(),
        nullable=False,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "daily_quote", "is_st",
        existing_type=sa.BOOLEAN(),
        nullable=False,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "daily_quote", "limit_up",
        existing_type=sa.BOOLEAN(),
        nullable=False,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "daily_quote", "limit_down",
        existing_type=sa.BOOLEAN(),
        nullable=False,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "factor_ic_history", "return_window",
        existing_type=sa.INTEGER(),
        nullable=False,
        existing_server_default=sa.text("20"),
    )
    op.alter_column(
        "market_state_history", "state_changed",
        existing_type=sa.BOOLEAN(),
        nullable=False,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "pipeline_run", "cp1_data_ready",
        existing_type=sa.BOOLEAN(),
        nullable=False,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "pipeline_run", "cp2_scoring_done",
        existing_type=sa.BOOLEAN(),
        nullable=False,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "pipeline_run", "cp3_signals_done",
        existing_type=sa.BOOLEAN(),
        nullable=False,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "signal", "status",
        existing_type=sa.VARCHAR(length=15),
        nullable=False,
        existing_server_default=sa.text("'NEW'::character varying"),
    )
    op.alter_column(
        "stock_info", "is_active",
        existing_type=sa.BOOLEAN(),
        nullable=False,
        existing_server_default=sa.text("true"),
    )


def downgrade() -> None:
    op.alter_column(
        "stock_info", "is_active",
        existing_type=sa.BOOLEAN(),
        nullable=True,
        existing_server_default=sa.text("true"),
    )
    op.alter_column(
        "signal", "status",
        existing_type=sa.VARCHAR(length=15),
        nullable=True,
        existing_server_default=sa.text("'NEW'::character varying"),
    )
    op.alter_column(
        "pipeline_run", "cp3_signals_done",
        existing_type=sa.BOOLEAN(),
        nullable=True,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "pipeline_run", "cp2_scoring_done",
        existing_type=sa.BOOLEAN(),
        nullable=True,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "pipeline_run", "cp1_data_ready",
        existing_type=sa.BOOLEAN(),
        nullable=True,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "market_state_history", "state_changed",
        existing_type=sa.BOOLEAN(),
        nullable=True,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "factor_ic_history", "return_window",
        existing_type=sa.INTEGER(),
        nullable=True,
        existing_server_default=sa.text("20"),
    )
    op.alter_column(
        "daily_quote", "limit_down",
        existing_type=sa.BOOLEAN(),
        nullable=True,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "daily_quote", "limit_up",
        existing_type=sa.BOOLEAN(),
        nullable=True,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "daily_quote", "is_st",
        existing_type=sa.BOOLEAN(),
        nullable=True,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "daily_quote", "is_suspended",
        existing_type=sa.BOOLEAN(),
        nullable=True,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "candidate_pool", "is_holding",
        existing_type=sa.BOOLEAN(),
        nullable=True,
        existing_server_default=sa.text("false"),
    )
    op.alter_column(
        "candidate_pool", "in_pool",
        existing_type=sa.BOOLEAN(),
        nullable=True,
        existing_server_default=sa.text("true"),
    )
    op.alter_column(
        "account", "account_type",
        existing_type=sa.VARCHAR(length=10),
        nullable=True,
        existing_server_default=sa.text("'REAL'::character varying"),
    )
