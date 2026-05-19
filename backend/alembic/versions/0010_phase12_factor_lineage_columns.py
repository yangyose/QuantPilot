"""Phase 12 因子级溯源 — candidate_pool 补 3 个 JSONB 列

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-19

依据：docs/design/phases/phase12_factor_lineage.md v1.1 + Phase 12 设计评审
P1-3/P1-4 修订 — 让 5 步管线产物 winsorized / neutralized / orthogonal 在
candidate_pool 持久化（与 signal_score_snapshot 3 列对应，但 candidate_pool 覆盖
全 pool ~50 只股票，AttributionService 多因子归因取数据更全）。

Phase 11 alembic 0009 已给 signal_score_snapshot 加这 3 列，本迁移给 candidate_pool
对齐补齐。后续 Scorer.aggregate + ScoringService.write_candidate_pool +
SignalService._build_snapshot_rows 一并补写入逻辑（Phase 11 实施缺陷 — Phase 12
设计评审 P1-4 抓到）。
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_pool",
        sa.Column("factor_winsorized", JSONB, nullable=True),
    )
    op.add_column(
        "candidate_pool",
        sa.Column("factor_neutralized", JSONB, nullable=True),
    )
    op.add_column(
        "candidate_pool",
        sa.Column("factor_orthogonal", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidate_pool", "factor_orthogonal")
    op.drop_column("candidate_pool", "factor_neutralized")
    op.drop_column("candidate_pool", "factor_winsorized")
