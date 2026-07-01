"""Phase 10 §2.3：12 个 config_key 的 dataclass 与默认值（SDD 附录 B 单一事实来源）。

设计原则：
- frozen=True：dataclass 不可变，可安全跨线程/Engine 共享
- 默认值为 SDD 附录 B 与 system_design §8 的代码常量化
- ConfigService 部分覆盖时使用 `{**asdict(default), **db_value}` 合并语义
- Engine 层接收实例后纯函数计算，不再读 DB（CLAUDE.md §6 Engine 层无 IO）
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------- 1. signal_params ----------------
@dataclass(frozen=True)
class SignalConfig:
    """SignalGenerator 阈值（SDD §8.2 + Phase 11 §7.1 分位扩展）。

    旧 V1.0-r5 绝对阈值字段（buy_threshold / sell_threshold / strong_threshold）保留：
    Phase 11 §5 分位主路径 fallback 时仍消费；并支持
    ``enable_absolute_threshold_override=True`` 一键回退。
    """
    # V1.0-r5 绝对阈值（fallback / override 路径仍消费）
    buy_threshold: float = 80.0
    sell_threshold: float = 40.0
    strong_threshold: float = 90.0
    stop_loss_pct: float = 0.08
    add_cost_deviation_pct: float = 0.10
    price_low_mult: float = 0.99
    price_high_mult: float = 1.02
    # Phase 11 §7.1 分位阈值主路径
    buy_pct_threshold: float = 0.05
    sell_pct_threshold: float = 0.70
    strong_pct_threshold: float = 0.01
    short_term_failure_sigma: float = 1.5
    enable_absolute_threshold_override: bool = False


DEFAULT_SIGNAL_CONFIG = SignalConfig()


# ---------------- 2. risk_limits ----------------
@dataclass(frozen=True)
class RiskLimitsConfig:
    """RiskChecker + PositionSizer 上限（SDD §8.3）。"""
    max_single_stock_pct: float = 0.20
    max_industry_pct: float = 0.30
    max_total_position_pct: float = 0.80
    single_trade_pct: float = 0.10
    # V1.0 整改 Batch 2 — B2-1：账户最大回撤 WARN 阈值（SDD §10.2 WARN 级风控）
    max_drawdown_pct: float = 0.20


DEFAULT_RISK_LIMITS = RiskLimitsConfig()


# ---------------- 3. market_state_params ----------------
@dataclass(frozen=True)
class MarketStateConfig:
    """MarketStateEngine（SDD §6.5、§7.1）。"""
    ma_short: int = 20
    ma_long: int = 60
    adx_period: int = 14
    adx_threshold: float = 25.0
    debounce_days: int = 3


DEFAULT_MARKET_STATE = MarketStateConfig()


# ---------------- 4. universe_params ----------------
@dataclass(frozen=True)
class UniverseConfig:
    """UniverseFilter + CandidatePoolManager（SDD §7.3 / Phase 11 §10.4）。"""
    min_liquidity_amount: float = 5_000_000.0  # 元
    new_stock_days: int = 60
    # Phase 11 v1.4：从 V1.0 老默认 20 提到 50，让 candidate_pool 能容纳
    # 设计 §10.4 隐含基线（全 universe top 1% STRONG ≈ 32 只）。否则即使
    # 全 universe STRONG 有 50 只，pool 截断到 20 → "STRONG ≥ 30" 验证假阴性。
    # SignalGenerator 触发用 composite_pct_in_market（相对 universe）独立于
    # pool 行数，所以这里只影响候选池持久化行数 + 前端展示宽度。
    pool_capacity: int = 50
    signal_expiry_days: int = 3


DEFAULT_UNIVERSE = UniverseConfig()


# ---------------- 5. strategy_weights ----------------
@dataclass(frozen=True)
class StrategyWeightsConfig:
    """Scorer 三态 × 4 策略权重矩阵（SDD §7.5）。

    子键必须与 BaseStrategy.name 逐字一致：trend / momentum / mean_reversion / value
    （v1.1 评审 Q-6：误写 reversion 会让 Scorer 取不到权重而回退默认）。
    """
    uptrend: dict[str, float] = field(default_factory=lambda: {
        "trend": 0.40, "momentum": 0.25, "mean_reversion": 0.15, "value": 0.20,
    })
    downtrend: dict[str, float] = field(default_factory=lambda: {
        "trend": 0.10, "momentum": 0.05, "mean_reversion": 0.15, "value": 0.70,
    })
    oscillation: dict[str, float] = field(default_factory=lambda: {
        "trend": 0.15, "momentum": 0.15, "mean_reversion": 0.40, "value": 0.30,
    })


DEFAULT_STRATEGY_WEIGHTS = StrategyWeightsConfig()


# ---------------- 6. strategy_params_trend ----------------
@dataclass(frozen=True)
class TrendStrategyConfig:
    ma_short: int = 20
    ma_long: int = 60
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9


DEFAULT_TREND_STRATEGY = TrendStrategyConfig()


# ---------------- 7. strategy_params_momentum ----------------
@dataclass(frozen=True)
class MomentumStrategyConfig:
    lookback_short: int = 60
    lookback_long: int = 120
    reversal_exclude_pct: float = 0.05


DEFAULT_MOMENTUM_STRATEGY = MomentumStrategyConfig()


# ---------------- 8. strategy_params_mean_reversion ----------------
@dataclass(frozen=True)
class MeanReversionStrategyConfig:
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    bbands_period: int = 20
    bbands_std: float = 2.0


DEFAULT_MEAN_REVERSION_STRATEGY = MeanReversionStrategyConfig()


# ---------------- 9. strategy_params_value ----------------
@dataclass(frozen=True)
class ValueStrategyConfig:
    pe_pb_history_years: int = 5


DEFAULT_VALUE_STRATEGY = ValueStrategyConfig()


# ---------------- 10. backtest_defaults ----------------
@dataclass(frozen=True)
class BacktestDefaultsConfig:
    """POST /backtest/run 端点层 partial-overlay 默认值（Phase 10 §4.4）。

    slippage_rate=0.001：SDD 附录 B 未列，按 A 股散户经验值 0.1%（v1.1 评审 Q-7）。
    """
    commission_rate: float = 0.00025
    stamp_tax_rate: float = 0.0005
    slippage_rate: float = 0.001


DEFAULT_BACKTEST_DEFAULTS = BacktestDefaultsConfig()


# ---------------- 11. notification_prefs ----------------
@dataclass(frozen=True)
class NotificationConfig:
    """通知偏好（SDD §14.4 v1.x 同步至 6 类事件开关）。"""
    wx_enabled: bool = True
    push_start_hour: int = 15
    push_end_hour: int = 22
    notify_signal_buy: bool = True
    notify_signal_sell: bool = True
    notify_market_state: bool = True
    notify_stop_loss_warn: bool = True
    notify_risk_warn: bool = True
    notify_factor_alert: bool = True


DEFAULT_NOTIFICATION = NotificationConfig()


# ---------------- 12. factor_monitor_params ----------------
@dataclass(frozen=True)
class FactorMonitorConfig:
    """因子监控（v1.1 评审 G-3 新增 + Phase 11 §4.1 滚动 ICIR 窗口扩展）。

    旧 Phase 7~10 字段（ic_window / ic_alert_threshold / half_life_window）保留：
    `FactorMonitorService.run_monthly` 旧路径仍消费；Phase 11 新方法
    （rolling_icir_state / apply_monthly_rebalance）使用新字段。
    """
    # Phase 7~10 字段（保留兼容）
    ic_window: int = 20
    ic_alert_threshold: float = 0.02
    half_life_window: int = 60
    # Phase 11 §4.1 新增（滚动 ICIR 窗口）
    ic_window_days: int = 252
    icir_lag_days: int = 20
    icir_warmup_days: int = 272      # = ic_window_days + icir_lag_days
    state_min_samples: int = 60
    ic_bootstrap_iterations: int = 1000
    half_life_window_days: int = 504


DEFAULT_FACTOR_MONITOR = FactorMonitorConfig()


# ---------------- 13. scoring_pipeline_params（Phase 11 §7.1 新增）----------------
@dataclass(frozen=True)
class ScoringPipelineConfig:
    """Phase 11 §7.1 评分管线配置（FactorPipeline 5 步管线 + Hysteresis 主开关）。

    Service 层负责派生 `engine.factor_pipeline.FactorPipelineConfig`（CLAUDE.md §6
    Engine 层无 IO，不直接依赖 ConfigService）。
    """
    winsorize_lower_pct: float = 0.01
    winsorize_upper_pct: float = 0.99
    neutralize_industry: bool = True       # SDD §7.1 Step 2 强制开
    neutralize_market_cap: bool = True     # Q2 锁定默认开
    neutralize_beta: bool = False          # Q2 锁定默认关
    hysteresis_enabled: bool = True


DEFAULT_SCORING_PIPELINE = ScoringPipelineConfig()


# ---------------- 辅助：risk_free_rate（Phase 6 已用纯标量，保持不变） ----------------
DEFAULT_RISK_FREE_RATE: float = 0.03


# ---------------- V1.5-G G-4a：config_key → 所需 level（§6.3 设置分层过滤）----------------
# SDD §14.1 设置表「适用层级」列（All / L2+ / L3）→ user.level 枚举（L1/L2/L3）映射规约
# （设计 §6.3）：All→L1（人人可见）、L2+→L2、L3→L3。这里是 config_key（较粗的分组，
# 一个 key 含 §14.1 多个设置项）→ 该组所需最低 level 的**代码内单一事实来源**。
# 不依赖 user_config.user_level DB 列（生产稀疏、历史 upsert 硬编码 L2、demo 曾写 "USER"
# 等非法字面，不可靠）。过滤 GET /settings + upsert 时回写正确 level 均以本表为准。
CONFIG_KEY_LEVEL: dict[str, str] = {
    # L1：SDD §14.4 提醒设置（All，人人可配）
    "notification_prefs": "L1",
    # L2：SDD §14.1 买/卖阈值、仓位/止损、关注池；§14.2 策略参数（L2+）
    "signal_params": "L2",
    "risk_limits": "L2",
    "universe_params": "L2",
    "backtest_defaults": "L2",
    "strategy_params_trend": "L2",
    "strategy_params_momentum": "L2",
    "strategy_params_mean_reversion": "L2",
    "strategy_params_value": "L2",
    # L3：SDD §14.3 权重配置；市场状态/因子监控内部参数（专业用户，§7.1/§7.4）
    "strategy_weights": "L3",
    "market_state_params": "L3",
    "factor_monitor_params": "L3",
    "scoring_pipeline_params": "L3",
}

_LEVEL_ORDER: dict[str, int] = {"L1": 1, "L2": 2, "L3": 3}


def config_visible_at_level(config_key: str, user_level: str) -> bool:
    """config_key 是否对 user_level 用户可见（所需 level <= 用户 level）。

    未登记 key 保守按 L3（最严）处理，避免把未知项误暴露给低层级用户。
    非法 user_level 回落 L1（最保守可见集）。
    """
    required = CONFIG_KEY_LEVEL.get(config_key, "L3")
    return _LEVEL_ORDER.get(user_level, 1) >= _LEVEL_ORDER[required]


__all__ = [
    "SignalConfig", "DEFAULT_SIGNAL_CONFIG",
    "RiskLimitsConfig", "DEFAULT_RISK_LIMITS",
    "MarketStateConfig", "DEFAULT_MARKET_STATE",
    "UniverseConfig", "DEFAULT_UNIVERSE",
    "StrategyWeightsConfig", "DEFAULT_STRATEGY_WEIGHTS",
    "TrendStrategyConfig", "DEFAULT_TREND_STRATEGY",
    "MomentumStrategyConfig", "DEFAULT_MOMENTUM_STRATEGY",
    "MeanReversionStrategyConfig", "DEFAULT_MEAN_REVERSION_STRATEGY",
    "ValueStrategyConfig", "DEFAULT_VALUE_STRATEGY",
    "BacktestDefaultsConfig", "DEFAULT_BACKTEST_DEFAULTS",
    "NotificationConfig", "DEFAULT_NOTIFICATION",
    "FactorMonitorConfig", "DEFAULT_FACTOR_MONITOR",
    "ScoringPipelineConfig", "DEFAULT_SCORING_PIPELINE",
    "DEFAULT_RISK_FREE_RATE",
    "CONFIG_KEY_LEVEL", "config_visible_at_level",
]
