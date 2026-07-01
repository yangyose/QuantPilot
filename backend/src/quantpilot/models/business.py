from datetime import date, datetime

import sqlalchemy as sa
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

    # === Phase 12（v1.0-r7）新增字段：5 步管线 Step 1/2/4 中间产物（alembic 0010）===
    # 与 signal_score_snapshot 同名 3 列对齐；candidate_pool 覆盖全 pool（~50 只）
    # 比 signal_score_snapshot（仅当日 BUY/SELL 信号股，~10-50 只）样本更全，
    # AttributionService 多因子归因优先读 candidate_pool。Phase 11 设计评审 P1-3
    # 数据源指向修正后落地。
    factor_winsorized: Mapped[dict | None] = mapped_column(JSONB)
    factor_neutralized: Mapped[dict | None] = mapped_column(JSONB)
    factor_orthogonal: Mapped[dict | None] = mapped_column(JSONB)

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


# Phase 15 §15-7（2026-06-27）：旧表 factor_ic_history 已归并进 FactorICWindowState
# （row_type='monthly_quality'）并 DROP（alembic 0017）。原 FactorIcHistory ORM 类删除。


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
    # Phase 14 §14-6：row_type 区分 daily / aggregate 行（共表拆分方案 A）。
    # 旧行（alembic 0014 升级前）：icir IS NOT NULL → 'aggregate'，否则 'daily'。
    # 新行：upsert_ic_daily → 'daily'；upsert_ic_aggregate → 'aggregate'。
    # partial unique index uq_factor_ic_window_state_aggregate 在 'aggregate' 行
    # 强制 (strategy, factor, state, trade_date) 唯一；daily 行仍受全表 UNIQUE 约束。
    # Phase 15 §15-7：row_type 取值扩展为 daily / aggregate / monthly_quality。
    # 'monthly_quality'（归并自旧表 factor_ic_history，2026-06-27）：月度
    # strategy-composite 因子质量行，state='ALL' 哨兵、trade_date=calc_month。
    # 复用列双语义：ic_mean_state=3 月滚动均值、ic_std_state=3 月滚动 std、
    # icir=ir_3m（IR=mean/std）、half_life=半衰期日数取整、sample_size=0 占位
    # （月度路径不记样本数）；alert_status 仅此类行非 NULL。仅由 /factor-quality
    # + /factor-quality/history + 月报告警消费，与 daily/aggregate 读路径隔离。
    row_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="daily",
    )
    # Phase 15 §15-7：因子质量告警状态（DECAY/INEFFICIENT/FAST_DECAY），
    # 仅 row_type='monthly_quality' 行非 NULL。
    alert_status: Mapped[str | None] = mapped_column(String(20))
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
        # Phase 14 §14-6：partial unique on aggregate 行，配合 row_type 列
        # 让 WHERE row_type='aggregate' 查询走 index-only scan。
        Index(
            "uq_factor_ic_window_state_aggregate",
            "strategy", "factor", "state", "trade_date",
            unique=True,
            postgresql_where=sa.text("row_type = 'aggregate'"),
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


class AttributionHistory(Base):
    """Phase 12 多因子回归归因历史（月末批写入；SDD §12.3 + phase12 §3.2.3）。

    V1.0 简化：归因因子 = 4 策略 strategy_z（trend / momentum / mean_reversion /
    value），不是 SDD §12.3 原描述的"风险因子归因"（Size/Value/Momentum/Beta）。
    完整 4 风险因子归因留 V1.5+ strategy_factors → 真因子映射后扩展。

    与 FactorICWindowState 区分：
    - factor_ic_window_state = 单因子 IC 时序（Phase 11 ICIR 滚动监控）
    - attribution_history     = 多因子收益拆解（Phase 12 OLS 归因）
    """

    __tablename__ = "attribution_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    calc_date: Mapped[date] = mapped_column(Date, nullable=False)
    factor: Mapped[str] = mapped_column(String(32), nullable=False)
    beta: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    t_stat: Mapped[float | None] = mapped_column(Numeric(8, 4))
    residual_std: Mapped[float | None] = mapped_column(Numeric(10, 6))
    r_squared: Mapped[float | None] = mapped_column(Numeric(6, 4))
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()", nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "calc_date", "factor", name="uq_attribution_date_factor",
        ),
        Index(
            "idx_attribution_date_desc",
            "calc_date",
            postgresql_ops={"calc_date": "DESC"},
        ),
    )


class DataQualityMetric(Base):
    """Phase 13 §3.4 数据质量监控指标（S2-GAP-01：DataValidator 错误持久化）。

    DataService.ingest_daily 调 DataValidator 后按 (metric_date, data_type,
    metric_key) upsert；/health/data 端点近 30 日聚合查询。

    metric_value 用 Numeric(20, 6) 同时兼容整数（*_count）和浮点（*_ratio）；
    详见 design §5.1 metric_key 示例。
    """

    __tablename__ = "data_quality_metric"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    data_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # daily_quote / financial_data / index_history / namechange
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # 整数 metric_key 示例：errors_count / invalid_rows_count /
    #   completeness_violation_count / price_invalid_count /
    #   pit_violation_count / adj_factor_jump_count
    # 浮点 metric_key 示例：data_completeness_ratio (0.0~1.0) /
    #   nan_ratio_* / avg_pct_chg_abs
    metric_value: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="NOW()", nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "metric_date", "data_type", "metric_key",
            name="uq_data_quality_date_type_key",
        ),
        Index(
            "idx_data_quality_date_desc",
            "metric_date",
            postgresql_ops={"metric_date": "DESC"},
        ),
    )


class Report(Base):
    """报告存储（周报/月报/自定义，SDD §12.5）。"""

    __tablename__ = "report"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account.id"), nullable=False
    )  # V1.5-G G-3：报告归属账户（账户层隔离，ownership 经此列强制）
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
    # V1.5-G G-4b：账户隔离（混合方案）。NULL = 系统级/共享通知（信号/市场/因子/健康），
    # 所有登录用户可见；非 NULL = 账户私有（止损/风险），仅归属用户可见。
    account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("account.id"), nullable=True
    )
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
        # V1.5-G G-4b：账户私有通知按账户过滤（部分索引，仅 account_id 非空行）
        Index(
            "idx_notify_account",
            "account_id",
            "created_at",
            postgresql_where=sa.text("account_id IS NOT NULL"),
            postgresql_ops={"created_at": "DESC"},
        ),
    )
