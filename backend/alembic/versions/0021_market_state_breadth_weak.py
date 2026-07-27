"""V1.5-A A3（SDD-EXT-07）：market_state_history.breadth_weak（市场宽度弱信号）

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-27

V1.5-A A3（设计 docs/design/phases/v1_5_a_backtest_monitoring.md §4）：NH-NL 市场宽度
指标。UPTREND 且 NH-NL 差值 ≤ 0（创 60 日新高 ≤ 新低）时 breadth_weak=True，下游
Scorer 按 OSCILLATION 权重压制趋势策略（market_state 仍报 UPTREND）。

生产既有表前向 ADD COLUMN（非破坏，部署单列 C-1）。server_default='false' 让存量
市场状态历史行取 False（既有行本无宽度信息，保守非弱）；ORM 侧默认 False。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "market_state_history",
        sa.Column(
            "breadth_weak",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("market_state_history", "breadth_weak")
