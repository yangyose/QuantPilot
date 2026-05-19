"""Phase 11 评分公式工业化 schema

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-15

依据：docs/design/phases/phase11_scoring_industrialization.md v1.2 §2.1
- 新表 factor_ic_window_state（Phase 11 ICIR + state 维度，replaces Phase 7
  既有 factor_ic_history 的语义；旧表保留 readonly 作 5y 真机 baseline）
- 新表 strategy_weights_history（每月生效权重审计）
- candidate_pool 扩展 6 列（三层输出 + 审计 + JSONB breakdown）
- signal_score_snapshot 扩展 3 列（5 步管线各阶段因子值 JSONB）
- signal 扩展 3 列（composite_z / composite_pct_in_market / trigger_reason）

旧 candidate_pool / signal_score_snapshot 列保留不动（Q8 锁定决策：保留旧字段 + 新字段并存）。
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ============================================================
    # 1. 新表 factor_ic_window_state
    # ============================================================
    op.create_table(
        "factor_ic_window_state",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("factor", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ic_value", sa.Numeric(8, 4), nullable=True),
        sa.Column("ic_mean_state", sa.Numeric(8, 4), nullable=True),
        sa.Column("ic_std_state", sa.Numeric(8, 4), nullable=True),
        sa.Column("icir", sa.Numeric(8, 4), nullable=True),
        sa.Column("sample_size", sa.Integer, nullable=False),
        sa.Column("ic_ci_low", sa.Numeric(8, 4), nullable=True),
        sa.Column("ic_ci_high", sa.Numeric(8, 4), nullable=True),
        sa.Column("t_stat", sa.Numeric(8, 4), nullable=True),
        sa.Column("half_life", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "strategy", "factor", "state", "trade_date",
            name="uq_factor_ic_window_state_skft",
        ),
    )
    op.create_index(
        "idx_factor_ic_window_state_date_strategy",
        "factor_ic_window_state",
        [sa.text("trade_date DESC"), "strategy"],
    )

    # ============================================================
    # 2. 新表 strategy_weights_history
    # ============================================================
    op.create_table(
        "strategy_weights_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("weight_used", sa.Numeric(6, 4), nullable=False),
        sa.Column("weights_source", sa.String(32), nullable=False),
        sa.Column("icir_inputs", JSONB, nullable=True),
        sa.Column("hysteresis_status", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "state", "strategy", "trade_date",
            name="uq_strategy_weights_history_sst",
        ),
    )
    op.create_index(
        "idx_strategy_weights_history_date",
        "strategy_weights_history",
        [sa.text("trade_date DESC")],
    )

    # ============================================================
    # 3. candidate_pool 扩展 6 列
    # ============================================================
    op.add_column("candidate_pool", sa.Column("composite_z", sa.Numeric(8, 4), nullable=True))
    op.add_column(
        "candidate_pool",
        sa.Column("composite_pct_in_market", sa.Numeric(6, 4), nullable=True),
    )
    op.add_column("candidate_pool", sa.Column("weights_source", sa.String(32), nullable=True))
    op.add_column("candidate_pool", sa.Column("hysteresis_status", sa.String(32), nullable=True))
    op.add_column("candidate_pool", sa.Column("score_breakdown_raw", JSONB, nullable=True))
    op.add_column("candidate_pool", sa.Column("score_breakdown_residual", JSONB, nullable=True))

    # ============================================================
    # 4. signal_score_snapshot 扩展 3 列（5 步管线各阶段快照）
    # ============================================================
    op.add_column(
        "signal_score_snapshot",
        sa.Column("factor_winsorized", JSONB, nullable=True),
    )
    op.add_column(
        "signal_score_snapshot",
        sa.Column("factor_neutralized", JSONB, nullable=True),
    )
    op.add_column(
        "signal_score_snapshot",
        sa.Column("factor_orthogonal", JSONB, nullable=True),
    )

    # ============================================================
    # 5. signal 扩展 3 列
    # ============================================================
    op.add_column("signal", sa.Column("composite_z", sa.Numeric(8, 4), nullable=True))
    op.add_column(
        "signal", sa.Column("composite_pct_in_market", sa.Numeric(6, 4), nullable=True)
    )
    op.add_column("signal", sa.Column("trigger_reason", sa.Text, nullable=True))


def downgrade() -> None:
    # signal 回退
    op.drop_column("signal", "trigger_reason")
    op.drop_column("signal", "composite_pct_in_market")
    op.drop_column("signal", "composite_z")

    # signal_score_snapshot 回退
    op.drop_column("signal_score_snapshot", "factor_orthogonal")
    op.drop_column("signal_score_snapshot", "factor_neutralized")
    op.drop_column("signal_score_snapshot", "factor_winsorized")

    # candidate_pool 回退
    op.drop_column("candidate_pool", "score_breakdown_residual")
    op.drop_column("candidate_pool", "score_breakdown_raw")
    op.drop_column("candidate_pool", "hysteresis_status")
    op.drop_column("candidate_pool", "weights_source")
    op.drop_column("candidate_pool", "composite_pct_in_market")
    op.drop_column("candidate_pool", "composite_z")

    # 新表回退
    op.drop_index(
        "idx_strategy_weights_history_date",
        table_name="strategy_weights_history",
    )
    op.drop_table("strategy_weights_history")

    op.drop_index(
        "idx_factor_ic_window_state_date_strategy",
        table_name="factor_ic_window_state",
    )
    op.drop_table("factor_ic_window_state")
