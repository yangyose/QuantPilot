from datetime import date, datetime

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from quantpilot.models.base import Base


class MarketStateHistory(Base):
    __tablename__ = "market_state_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    market_state: Mapped[str] = mapped_column(String(20), nullable=False)  # see MarketStateEnum
    trend_strength: Mapped[float | None] = mapped_column(Numeric(5, 2))
    adx_value: Mapped[float | None] = mapped_column(Numeric(6, 3))
    ma20: Mapped[float | None] = mapped_column(Numeric(10, 3))
    ma60: Mapped[float | None] = mapped_column(Numeric(10, 3))
    state_changed: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text)


class CandidatePool(Base):
    """候选股池日快照。ts_code 不设 FK，应用层校验。

    Phase 11（v1.0-r6）扩展：新增三层输出（composite_z / composite_pct_in_market /
    composite_score）+ 审计字段（weights_source / hysteresis_status）+ JSONB
    breakdown（score_breakdown_raw / score_breakdown_residual）。旧 4 个标量
    分值（trend_score 等）保留作兼容字段，按 Φ(strategy_z_raw)×100 写入。
    """

    __tablename__ = "candidate_pool"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    composite_score: Mapped[float | None] = mapped_column(Numeric(5, 2))  # 0-100 (UI 显示层)
    trend_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    reversion_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    momentum_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    value_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    market_state: Mapped[str | None] = mapped_column(String(20))
    in_pool: Mapped[bool] = mapped_column(Boolean, default=True)
    is_holding: Mapped[bool] = mapped_column(Boolean, default=False)  # 持仓标的强制留池

    # === Phase 11（v1.0-r6）新增字段 ===
    composite_z: Mapped[float | None] = mapped_column(Numeric(8, 4))
    composite_pct_in_market: Mapped[float | None] = mapped_column(Numeric(6, 4))
    weights_source: Mapped[str | None] = mapped_column(String(32))
    hysteresis_status: Mapped[str | None] = mapped_column(String(32))
    score_breakdown_raw: Mapped[dict | None] = mapped_column(JSONB)
    score_breakdown_residual: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("ts_code", "trade_date", name="uq_candidate_pool_code_date"),
        Index("idx_pool_date_score", "trade_date", "composite_score"),
        Index("idx_pool_code_date", "ts_code", "trade_date"),
    )


class Signal(Base):
    """交易信号。ts_code 不设 FK，应用层校验。

    Phase 11（v1.0-r6）扩展：新增 composite_z / composite_pct_in_market 反映
    新评分管线层 1 + 层 2 输出；trigger_reason 字段细分（pct_below_buy /
    pct_above_sell / hard_stop_loss / short_term_z_drop / mid_term_icir_flip）。
    旧 score 字段（0-100）保留作 UI 显示层兼容。
    """

    __tablename__ = "signal"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(10), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY / SELL
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    score: Mapped[float | None] = mapped_column(Numeric(5, 2))  # 0-100 (UI 显示层兼容)
    suggested_pct: Mapped[float | None] = mapped_column(Numeric(5, 4))
    suggested_price_low: Mapped[float | None] = mapped_column(Numeric(10, 3))
    suggested_price_high: Mapped[float | None] = mapped_column(Numeric(10, 3))
    stop_loss_price: Mapped[float | None] = mapped_column(Numeric(10, 3))
    signal_strength: Mapped[str | None] = mapped_column(String(10))  # STRONG/MODERATE，仅买入
    liquidity_note: Mapped[str | None] = mapped_column(Text)
    t1_warning: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(15), default="NEW")
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )

    # === Phase 11（v1.0-r6）新增字段 ===
    composite_z: Mapped[float | None] = mapped_column(Numeric(8, 4))
    composite_pct_in_market: Mapped[float | None] = mapped_column(Numeric(6, 4))
    trigger_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("ts_code", "trade_date", "signal_type", name="uq_signal_code_date_type"),
        Index("idx_signal_code_date", "ts_code", "trade_date"),
        Index("idx_signal_date_type", "trade_date", "signal_type"),
    )


class SignalScoreSnapshot(Base):
    """信号-评分快照（数据血缘 V1.0 最小实现）。signal_id 设 FK + CASCADE DELETE。

    Phase 11（v1.0-r6）扩展：新增 5 步管线各阶段因子值快照 JSONB——
    factor_winsorized（Step 1 后）/ factor_neutralized（Step 2 后）/
    factor_orthogonal（Step 4b 后含 _normalized）。原 raw_factors 保留作
    业务可解释 L1 reason 文本生成 + Phase 7~10 baseline 兼容。
    """

    __tablename__ = "signal_score_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("signal.id", ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    ts_code: Mapped[str] = mapped_column(String(10), nullable=False)
    composite_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    trend_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    reversion_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    momentum_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    value_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    market_state: Mapped[str | None] = mapped_column(String(20))
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB)
    raw_factors: Mapped[dict | None] = mapped_column(JSONB)

    # === Phase 11（v1.0-r6）新增字段 ===
    factor_winsorized: Mapped[dict | None] = mapped_column(JSONB)
    factor_neutralized: Mapped[dict | None] = mapped_column(JSONB)
    factor_orthogonal: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("signal_id", name="uq_snapshot_signal_id"),
        Index("idx_snapshot_signal", "signal_id"),
    )


class FactorIcHistory(Base):
    """因子质量监控历史（每月末计算，SDD §7.4，V1.0 必需）。

    【Phase 11 状态】Phase 7~10 baseline 表，保留 readonly 供回归对照。
    Phase 11（v1.0-r6）起 MonthlyScheduler dispatch 切换到 apply_monthly_rebalance，
    写入新表 FactorICWindowState；本表不再被写入但行保留（5y 真机数据）。
    详见 docs/design/phases/phase11_scoring_industrialization.md §2.1 + §4.0。
    """

    __tablename__ = "factor_ic_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    calc_month: Mapped[date] = mapped_column(Date, nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(30), nullable=False)
    factor_name: Mapped[str] = mapped_column(String(50), nullable=False)
    ic_value: Mapped[float | None] = mapped_column(Numeric(8, 6))
    ic_mean_3m: Mapped[float | None] = mapped_column(Numeric(8, 6))
    ic_std_3m: Mapped[float | None] = mapped_column(Numeric(8, 6))
    ir_3m: Mapped[float | None] = mapped_column(Numeric(8, 6))
    half_life_days: Mapped[float | None] = mapped_column(Numeric(6, 1))
    return_window: Mapped[int] = mapped_column(Integer, default=20)
    alert_status: Mapped[str | None] = mapped_column(String(20))  # DECAY/INEFFICIENT/FAST_DECAY

    __table_args__ = (
        UniqueConstraint(
            "calc_month", "strategy_name", "factor_name", "return_window",
            name="uq_ic_history_month_strategy_factor_window",
        ),
        Index("idx_ic_history_strategy", "strategy_name", "calc_month"),
    )


class FactorICWindowState(Base):
    """Phase 11 因子 ICIR 滚动监控（按 strategy × factor × state × trade_date 持久化）。

    SDD v1.4 §7.4 + Phase 11 §2.1 + §4：
    - 窗口固定 `[trade_date - 272d, trade_date - 20d]`（lag 20 跳过未完成 forward returns）
    - state 子集 sample_size < 60 时回退冷启动（不下线，但写告警）
    - IC_daily(s, f, t) 月末批后回算（不加 DailyPipeline 新 CP）
    - 配套 strategy_weights_history 提供月度 rebalance 后生效权重
    """

    __tablename__ = "factor_ic_window_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    factor: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    ic_value: Mapped[float | None] = mapped_column(Numeric(8, 4))
    ic_mean_state: Mapped[float | None] = mapped_column(Numeric(8, 4))
    ic_std_state: Mapped[float | None] = mapped_column(Numeric(8, 4))
    icir: Mapped[float | None] = mapped_column(Numeric(8, 4))
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    ic_ci_low: Mapped[float | None] = mapped_column(Numeric(8, 4))
    ic_ci_high: Mapped[float | None] = mapped_column(Numeric(8, 4))
    t_stat: Mapped[float | None] = mapped_column(Numeric(8, 4))
    half_life: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )

    __table_args__ = (
        UniqueConstraint(
            "strategy", "factor", "state", "trade_date",
            name="uq_factor_ic_window_state_skft",
        ),
        Index(
            "idx_factor_ic_window_state_date_strategy",
            "trade_date", "strategy",
            postgresql_ops={"trade_date": "DESC"},
        ),
    )


class StrategyWeightsHistory(Base):
    """Phase 11 月度生效权重审计（每月 rebalance 后写入，next month 起 effective）。

    SDD v1.4 §7.5 + Phase 11 §2.1 + §4.2 + §6.2：
    - weights_source: "icir" / "default_matrix" / "user_override"
    - hysteresis_status: "stable" / "pending_switch"
    - icir_inputs: 计算时各策略 ICIR 原值（审计回溯用）
    - 由 ScoringService.get_active_weights 实时按 state 查最近一行驱动 state 切换即时换权
    """

    __tablename__ = "strategy_weights_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    weight_used: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    weights_source: Mapped[str] = mapped_column(String(32), nullable=False)
    icir_inputs: Mapped[dict | None] = mapped_column(JSONB)
    hysteresis_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )

    __table_args__ = (
        UniqueConstraint(
            "state", "strategy", "trade_date",
            name="uq_strategy_weights_history_sst",
        ),
        Index(
            "idx_strategy_weights_history_date",
            "trade_date",
            postgresql_ops={"trade_date": "DESC"},
        ),
    )


class Report(Base):
    """报告存储（周报/月报/自定义，SDD §12.5）。"""

    __tablename__ = "report"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_type: Mapped[str] = mapped_column(String(15), nullable=False)  # WEEKLY/MONTHLY/CUSTOM
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )

    __table_args__ = (
        Index("idx_report_type_period", "report_type", "period_end"),
    )


class UserWatchlist(Base):
    """用户黑白名单。ts_code 不设 FK，用户手动管理，应用层校验。"""

    __tablename__ = "user_watchlist"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(10), nullable=False)
    list_type: Mapped[str] = mapped_column(String(10), nullable=False)  # WHITELIST/BLACKLIST
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()"
    )

    __table_args__ = (
        UniqueConstraint("ts_code", "list_type", name="uq_watchlist_code_type"),
    )


class InAppNotification(Base):
    """系统内通知（Phase 10 §2.1 + SDD §13.1 降级兜底渠道）。

    所有通知事件始终写入本表；WxPusher 仅作为附加推送渠道。
    未读通知 = `read_at IS NULL`（前端 Bell Badge 依此计数）。
    """

    __tablename__ = "in_app_notification"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # SIGNAL_BUY / SIGNAL_SELL / MARKET_STATE / STOP_LOSS_WARN
    # / RISK_WARN / FACTOR_ALERT / PIPELINE_FAILURE
    notify_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB)  # 关联实体如 {signal_id, ts_code}
    wx_pushed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    wx_error: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()", nullable=False
    )

    __table_args__ = (
        # 未读查询最多（Bell 下拉）；使用部分索引减少索引体积
        Index(
            "idx_notify_unread",
            "created_at",
            postgresql_where="read_at IS NULL",
            postgresql_ops={"created_at": "DESC"},
        ),
        Index(
            "idx_notify_type_created",
            "notify_type",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
    )
