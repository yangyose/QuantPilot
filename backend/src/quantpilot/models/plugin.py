"""C5 策略插件存储与审计（SDD §15.2 / 设计 §7.4）。

两张表：

- `strategy_plugin`：用户提交的插件源码 + 状态。`UNIQUE(user_id, name, version)`——
  同一用户同名同版本只允许一份，版本升级走新行（旧行保留，审计可追）。
- `strategy_plugin_audit`：`action ∈ {upload, update, delete, load, execute}` 全程留痕，
  覆盖 SDD §15.2「加载、执行、输出均记录审计日志」。执行类动作另记耗时 / 峰值内存 /
  沙箱退出状态 / 错误摘要。

## 两个刻意的设计选择

1. **DELETE 是软删**（`status='deleted'`），不真删行——审计行的 FK 用 `RESTRICT`：
   硬删插件会带走它的审计，而审计的全部意义就是留痕。想彻底清理得先显式处理审计。
2. **运行时指标列全可空**：`upload` / `delete` 这类动作没有耗时、没有内存峰值、没有退出
   状态。C-4 禁止用占位值（0 / ""）冒充「没有这个观测」——那会让「执行了但零耗时」与
   「压根没执行」在数据里无法区分（`universe_daily_stat` 那次的同族教训）。

⚠️ `error_excerpt` 落库前必须过脱敏（`plugin_runner._redact` 已按 URL **形状**脱敏，
不是按键名——CLAUDE.md §4.11 第 7 例）。
"""
from datetime import date, datetime

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from quantpilot.models.base import Base

# 插件生命周期状态。`deleted` = 软删（§7.5 DELETE 端点），行仍在、审计仍在。
PLUGIN_STATUSES: tuple[str, ...] = ("active", "disabled", "deleted")
# 审计动作（设计 §7.4 逐字）。改这里必须同时改 CHECK 约束 + 迁移，
# `test_plugin_models.py` 会逐个核对二者一致。
PLUGIN_ACTIONS: tuple[str, ...] = ("upload", "update", "delete", "load", "execute")

_STATUS_CHECK = "status IN (" + ", ".join(f"'{s}'" for s in PLUGIN_STATUSES) + ")"
_ACTION_CHECK = "action IN (" + ", ".join(f"'{a}'" for a in PLUGIN_ACTIONS) + ")"


class StrategyPlugin(Base):
    """L3 用户提交的策略插件源码（设计 §7.4）。"""

    __tablename__ = "strategy_plugin"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_code: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", "version", name="uq_strategy_plugin_user_name_ver"),
        CheckConstraint(_STATUS_CHECK, name="ck_strategy_plugin_status"),
        Index("idx_strategy_plugin_user_status", "user_id", "status"),
    )


class StrategyPluginAudit(Base):
    """插件全生命周期审计（设计 §7.4；SDD §15.2「加载/执行/输出均记录」）。"""

    __tablename__ = "strategy_plugin_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # RESTRICT 而非 CASCADE：硬删插件会带走审计，而审计的意义就是留痕（见模块 docstring）
    plugin_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("strategy_plugin.id", ondelete="RESTRICT"), nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id", ondelete="CASCADE"), nullable=False,
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    # 以下为**执行类**动作才有的观测：upload/delete 时保持 NULL（C-4：不用 0 冒充「没有」）
    trade_date: Mapped[date | None] = mapped_column(Date)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    peak_memory_kb: Mapped[int | None] = mapped_column(BigInteger)
    exit_status: Mapped[str | None] = mapped_column(String(32))
    error_excerpt: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint(_ACTION_CHECK, name="ck_strategy_plugin_audit_action"),
        # 详情页取「最近审计」→ 降序索引必须写进定义（CLAUDE.md §4.8）
        Index(
            "idx_strategy_plugin_audit_plugin_created",
            "plugin_id", text("created_at DESC"),
        ),
    )
