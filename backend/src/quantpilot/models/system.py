from datetime import date, datetime

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from quantpilot.models.base import Base


class PipelineRun(Base):
    """流水线运行记录（含 CP1/CP2/CP3 检查点，SDD §15.3）。"""

    __tablename__ = "pipeline_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    status: Mapped[str | None] = mapped_column(String(10))  # RUNNING/SUCCESS/FAILED
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    signal_count: Mapped[int | None] = mapped_column(Integer)
    error_msg: Mapped[str | None] = mapped_column(Text)
    cp1_data_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    cp1_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    data_snapshot_version: Mapped[str | None] = mapped_column(String(64))
    cp2_scoring_done: Mapped[bool] = mapped_column(Boolean, default=False)
    cp2_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    cp3_signals_done: Mapped[bool] = mapped_column(Boolean, default=False)
    cp3_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    config_snapshot: Mapped[dict | None] = mapped_column(JSONB)


class SystemConfig(Base):
    """系统运维配置（Token、调度时间等，管理员维护）。"""

    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )


class UserConfig(Base):
    """用户业务配置（分层，SDD §14.1-14.3；支持 L1/L2/L3 权限控制）。"""

    __tablename__ = "user_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    config_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    config_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    user_level: Mapped[str] = mapped_column(String(5), nullable=False, default="L2")
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )


class UserConfigHistory(Base):
    """用户配置变更历史（支持 API 回退，SDD §14.6）。"""

    __tablename__ = "user_config_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    config_key: Mapped[str] = mapped_column(String(100), nullable=False)
    old_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    changed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )
    change_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_config_history_key", "config_key", "changed_at"),
    )


class BacktestTask(Base):
    """回测任务（Phase 8，§2.1）。"""

    __tablename__ = "backtest_task"

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID4 字符串
    # PENDING/RUNNING/SUCCESS/FAILED
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False)  # BacktestConfig 序列化
    # Engine 层 12 配置快照（Phase 10 §4.4）
    config_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    error_msg: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )


class BacktestResult(Base):
    """回测结果持久化（Phase 8，§2.1）。"""

    __tablename__ = "backtest_result"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("backtest_task.task_id", ondelete="CASCADE"),
        nullable=False, unique=True,  # SDD §2.1 FK UNIQUE；与 __table_args__ UniqueConstraint 对应
    )
    performance_json: Mapped[dict] = mapped_column(JSONB, nullable=False)  # SDD 附录 C 全部指标
    # {trade_date_str: nav_value}
    daily_nav_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    disclaimer: Mapped[str] = mapped_column(Text, nullable=False)  # SDD §7.7.4 声明
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )

    __table_args__ = (
        UniqueConstraint("task_id", name="uq_backtest_result_task_id"),
        Index("idx_backtest_result_task", "task_id"),
    )
