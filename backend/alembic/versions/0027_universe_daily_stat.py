"""可观测性：universe_daily_stat（每日选股面规模 + 逐条规则剔除数）

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-07

**前向且非破坏**：只新建一张表，不动任何现有表/列/索引。

背景（CLAUDE.md §6 登记的可观测性缺口）：生产此前没有任何表持久化每日 universe
规模，容器重启后日志只剩当日一行。2026-09-03 `is_suspended` 修复上线后
「universe 扩大了百分之几」无法回溯实证（事前预估 2276→2658 事后被证伪，
真机实测 3212）；2026-09-07 F-4 净资产过滤修复同样面临这个问题。

⚠️ `excluded` 存**边际**剔除数而非累计：只记总数的话，「F-4 生效但当天没命中」
与「F-4 整条静默失效」在数据里长得一模一样——而后者恰恰真实发生过。
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "universe_daily_stat",
        sa.Column("trade_date", sa.Date(), nullable=False),
        # PIT 活股数（进入 F-1~F-8 之前）
        sa.Column("total_in", sa.Integer(), nullable=False),
        # 通过全部 F-1~F-8 之后
        sa.Column("total_out", sa.Integer(), nullable=False),
        # 黑名单剔除后真正进评分的只数（黑名单在 Service 层，不在 UniverseFilter）
        sa.Column("after_blacklist", sa.Integer(), nullable=True),
        # {"F-1": n, ..., "F-8": n}；边际计数，各条之和 == total_in - total_out
        sa.Column("excluded", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")
        ),
        sa.PrimaryKeyConstraint("trade_date", name="pk_universe_daily_stat"),
    )


def downgrade() -> None:
    op.drop_table("universe_daily_stat")
