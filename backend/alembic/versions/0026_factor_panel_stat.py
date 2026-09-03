"""V1.5-K K-6：factor_panel_stat（因子级面板统计量，研究数据）

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-03

**前向且非破坏**：只新建一张表，不动任何现有表/列/索引。
运行时表 `factor_ic_window_state` 一个字节都不改——它喂 ICIR 决定实盘权重，
三个读取方（get_ic_daily_window / get_monthly_quality_ic_series /
get_existing_daily_ic_dates 断点续传）零风险。

DDL 权威源：`docs/design/system_design.md §4.2`（C-5：先回写上游再实施）。
"""
import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "factor_panel_stat",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # 重跑批次（配置+日期），令多次重跑并存可比
        sa.Column("panel_run", sa.String(length=64), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=False),
        # 因子名；组合级指标填 '__portfolio__'
        sa.Column("factor", sa.String(length=64), nullable=False),
        # 'raw'（固有预测力）/ 'z'（进 composite 那版）；组合级填 'n/a'
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        # 前向交易日 5/10/20/40；无前向概念填 0
        sa.Column("horizon", sa.SmallInteger(), nullable=False),
        # ic / valid_ratio / decile_fwd_return / top5_excess / turnover_jaccard / cost_drag
        sa.Column("metric", sa.String(length=32), nullable=False),
        # 十分位 1..10；不适用填 -1。
        # ⚠️ 必须 NOT NULL：PG 的 UNIQUE 视 NULL 互不相等，可空会让下面的九列
        # 唯一键形同虚设、静默写重复行（同 C0-7 撞车那一族，alembic 0025）。
        sa.Column(
            "bucket", sa.SmallInteger(), server_default=sa.text("-1"), nullable=False
        ),
        sa.Column("value", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "panel_run", "trade_date", "strategy", "factor",
            "stage", "state", "horizon", "metric", "bucket",
            name="uq_factor_panel_stat",
        ),
    )
    op.create_index(
        "idx_factor_panel_stat_run_metric",
        "factor_panel_stat",
        ["panel_run", "metric", "trade_date"],
    )


def downgrade() -> None:
    op.drop_index("idx_factor_panel_stat_run_metric", table_name="factor_panel_stat")
    op.drop_table("factor_panel_stat")
