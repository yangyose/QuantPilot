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
    """SignalGenerator 阈值（SDD §8.2）。"""
    buy_threshold: float = 80.0
    sell_threshold: float = 40.0
    strong_threshold: float = 90.0
    stop_loss_pct: float = 0.08
    add_cost_deviation_pct: float = 0.10
    price_low_mult: float = 0.99
    price_high_mult: float = 1.02


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
    """UniverseFilter + CandidatePoolManager（SDD §7.3）。"""
    min_liquidity_amount: float = 5_000_000.0  # 元
    new_stock_days: int = 60
    pool_capacity: int = 20
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
    """因子监控（v1.1 评审 G-3 新增；SDD 附录 B 列默认窗口但无配置入口）。"""
    ic_window: int = 20
    ic_alert_threshold: float = 0.02
    half_life_window: int = 60


DEFAULT_FACTOR_MONITOR = FactorMonitorConfig()


# ---------------- 辅助：risk_free_rate（Phase 6 已用纯标量，保持不变） ----------------
DEFAULT_RISK_FREE_RATE: float = 0.03


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
    "DEFAULT_RISK_FREE_RATE",
]
