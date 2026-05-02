"""从 `pipeline_run.config_snapshot` / `backtest_task.config_snapshot` 派生 dataclass。

Phase 10 §4.3 / §4.4 评审 C-01/C-02/C-03：
- snapshot 在 Pipeline 启动 / 回测任务创建时一次性写入。
- 后续 Engine 的 __init__ 必须从该 dict 派生 dataclass，禁止再调 DB/Redis。
- 本模块提供 **同步** API 供 ScoringService / BacktestEngine 等纯 CPU 路径使用，
  避免在 `asyncio.to_thread` 包装的回测主循环里再 await ConfigService。

合并语义与 `ConfigService._get_typed` 保持一致：dict 字段做 1 层深合并，未知字段被过滤。
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from quantpilot.core.config_defaults import (
    DEFAULT_BACKTEST_DEFAULTS,
    DEFAULT_FACTOR_MONITOR,
    DEFAULT_MARKET_STATE,
    DEFAULT_MEAN_REVERSION_STRATEGY,
    DEFAULT_MOMENTUM_STRATEGY,
    DEFAULT_NOTIFICATION,
    DEFAULT_RISK_LIMITS,
    DEFAULT_SIGNAL_CONFIG,
    DEFAULT_STRATEGY_WEIGHTS,
    DEFAULT_TREND_STRATEGY,
    DEFAULT_UNIVERSE,
    DEFAULT_VALUE_STRATEGY,
    BacktestDefaultsConfig,
    FactorMonitorConfig,
    MarketStateConfig,
    MeanReversionStrategyConfig,
    MomentumStrategyConfig,
    NotificationConfig,
    RiskLimitsConfig,
    SignalConfig,
    StrategyWeightsConfig,
    TrendStrategyConfig,
    UniverseConfig,
    ValueStrategyConfig,
)
from quantpilot.services.config_service import _deep_merge

logger = logging.getLogger(__name__)

_SNAPSHOT_REGISTRY: dict[str, tuple[type, Any]] = {
    "signal_params": (SignalConfig, DEFAULT_SIGNAL_CONFIG),
    "risk_limits": (RiskLimitsConfig, DEFAULT_RISK_LIMITS),
    "market_state_params": (MarketStateConfig, DEFAULT_MARKET_STATE),
    "universe_params": (UniverseConfig, DEFAULT_UNIVERSE),
    "strategy_weights": (StrategyWeightsConfig, DEFAULT_STRATEGY_WEIGHTS),
    "strategy_params_trend": (TrendStrategyConfig, DEFAULT_TREND_STRATEGY),
    "strategy_params_momentum": (MomentumStrategyConfig, DEFAULT_MOMENTUM_STRATEGY),
    "strategy_params_mean_reversion": (
        MeanReversionStrategyConfig,
        DEFAULT_MEAN_REVERSION_STRATEGY,
    ),
    "strategy_params_value": (ValueStrategyConfig, DEFAULT_VALUE_STRATEGY),
    "backtest_defaults": (BacktestDefaultsConfig, DEFAULT_BACKTEST_DEFAULTS),
    "notification_prefs": (NotificationConfig, DEFAULT_NOTIFICATION),
    "factor_monitor_params": (FactorMonitorConfig, DEFAULT_FACTOR_MONITOR),
}


def from_snapshot(snapshot: dict[str, Any] | None, key: str) -> Any:
    """从 snapshot 反序列化 `key` 为对应 dataclass 实例。

    - `snapshot` 为 None / 不含 key / 子值为空 → 返回该 key 的默认 dataclass 副本。
    - 子值为 dict 但结构损坏（未知字段已过滤后仍构造失败）→ 记 ERROR，回退默认值。
    - 子值结构正确 → 与默认值深合并后实例化。

    `key` 必须是 12 个合法 config_key 之一；否则 KeyError（属编码错误）。
    """
    cls, default = _SNAPSHOT_REGISTRY[key]
    if not snapshot:
        return cls(**asdict(default))
    sub = snapshot.get(key)
    if not sub:
        return cls(**asdict(default))
    if not isinstance(sub, dict):
        # JSONB 列被外部写入了非 dict（list/str/数字）→ 记 ERROR 并回退默认
        logger.error(
            "snapshot key=%s expected dict, got %s; fallback to default",
            key, type(sub).__name__,
        )
        return cls(**asdict(default))
    default_dict = asdict(default)
    merged = _deep_merge(default_dict, sub)
    merged = {k: v for k, v in merged.items() if k in default_dict}
    try:
        return cls(**merged)
    except TypeError as e:
        logger.error("snapshot key=%s structure invalid, fallback to default: %s", key, e)
        return cls(**asdict(default))


__all__ = ["from_snapshot"]
