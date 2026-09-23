"""V1.5-C C5：strategy_plugin + strategy_plugin_audit 两表（设计 §7.4）

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-23

**前向且非破坏**：只建两张新表，不动任何现有表。

三处与 ORM 必须逐字一致（CLAUDE.md §4.8「ORM `__table_args__` 与迁移文件保持一致」）：
`UNIQUE(user_id, name, version)`、两条 CHECK 的取值域、`(plugin_id, created_at DESC)`
降序索引（用 `sa.text`——写成普通列索引会让「最近 N 条审计」走反向扫描）。

审计行的 `plugin_id` FK 取 **RESTRICT** 而非 CASCADE：硬删插件会带走它的审计，而审计的
全部意义就是留痕；产品路径是软删（`status='deleted'`，§7.5 DELETE 端点）。
"""
import sqlalchemy as sa

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

_STATUSES = ("active", "disabled", "deleted")
_ACTIONS = ("upload", "update", "delete", "load", "execute")


def _in_clause(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "strategy_plugin",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "name", "version", name="uq_strategy_plugin_user_name_ver"),
        sa.CheckConstraint(_in_clause("status", _STATUSES), name="ck_strategy_plugin_status"),
    )
    op.create_index("idx_strategy_plugin_user_status", "strategy_plugin", ["user_id", "status"])

    op.create_table(
        "strategy_plugin_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("plugin_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        # 执行类动作才有的观测；其余动作保持 NULL（C-4：不用 0 冒充「没有这个观测」）
        sa.Column("trade_date", sa.Date(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("peak_memory_kb", sa.BigInteger(), nullable=True),
        sa.Column("exit_status", sa.String(32), nullable=True),
        sa.Column("error_excerpt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["plugin_id"], ["strategy_plugin.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.CheckConstraint(_in_clause("action", _ACTIONS), name="ck_strategy_plugin_audit_action"),
    )
    op.create_index(
        "idx_strategy_plugin_audit_plugin_created",
        "strategy_plugin_audit",
        ["plugin_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    # 先审计表：它的 FK 指向 strategy_plugin
    op.drop_index("idx_strategy_plugin_audit_plugin_created", table_name="strategy_plugin_audit")
    op.drop_table("strategy_plugin_audit")
    op.drop_index("idx_strategy_plugin_user_status", table_name="strategy_plugin")
    op.drop_table("strategy_plugin")
