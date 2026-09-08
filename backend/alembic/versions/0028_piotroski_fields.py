"""V1.5-C C2：financial_data 增 Piotroski F-Score 所需 7 列（SDD-EXT-04）

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-08

**前向且非破坏**：纯 `ADD COLUMN`（全部可空），不动任何现有列/索引/约束。
既有行这 7 列为 NULL，而 `compute_f_score` 对 NaN 的处理是**记 NaN 不记 0**、
缺 ≥3 项即「不可判」，故回填完成前 F-Score 门控自然不生效（C-4：可见的降级）。

精度按量纲：比率类 Numeric(12,6) / 每股类 Numeric(12,4) / 股本 Numeric(20,4)。

DDL 权威源：`docs/design/phases/v1_5_c_strategy_expansion.md` §4.2。
"""
import sqlalchemy as sa

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_COLS = (
    ("roa", sa.Numeric(12, 6)),                  # 总资产收益率
    ("ocfps", sa.Numeric(12, 4)),                # 每股经营现金流
    ("eps", sa.Numeric(12, 4)),                  # 每股收益
    ("current_ratio", sa.Numeric(12, 6)),        # 流动比率
    ("grossprofit_margin", sa.Numeric(12, 6)),   # 毛利率
    ("assets_turn", sa.Numeric(12, 6)),          # 资产周转率
    ("total_share", sa.Numeric(20, 4)),          # 总股本（来自 daily_basic）
)


def upgrade() -> None:
    for name, type_ in _COLS:
        op.add_column("financial_data", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLS):
        op.drop_column("financial_data", name)
