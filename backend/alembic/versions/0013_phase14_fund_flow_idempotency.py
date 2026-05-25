"""Phase 14 §14-1 fund_flow.idempotency_key（RM-13 deposit 幂等）

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-25

依据：docs/design/phases/phase14_account_integrity.md §3.2.1。
- 加 idempotency_key VARCHAR(36) NULL
- 加 CHECK 约束 length <= 36（防止超长字符串注入；UUID4 含 4 个 `-` 共 36 字符）
- 加 partial unique (account_id, idempotency_key) WHERE idempotency_key IS NOT NULL
  partial 允许旧行 NULL 共存（兼容历史无 key 数据）
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fund_flow",
        sa.Column("idempotency_key", sa.String(length=36), nullable=True),
    )
    op.create_check_constraint(
        "ck_fund_flow_idempotency_key_len",
        "fund_flow",
        "idempotency_key IS NULL OR length(idempotency_key) <= 36",
    )
    op.create_index(
        "uq_fund_flow_account_idempotency",
        "fund_flow",
        ["account_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_fund_flow_account_idempotency", table_name="fund_flow")
    op.drop_constraint(
        "ck_fund_flow_idempotency_key_len", "fund_flow", type_="check",
    )
    op.drop_column("fund_flow", "idempotency_key")
