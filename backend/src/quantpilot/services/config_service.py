"""ConfigService：统一配置访问器（Phase 10 §3）。

职责：
- 对 12 个 config_key 提供类型化 getter，返回 dataclass 实例
- 部分覆盖默认值：DB 中缺失的字段自动用 `core/config_defaults.py` 常量补齐
- Redis 5 分钟缓存（可选，未配置时只读 DB）
- `invalidate(key)` 由 SettingsService upsert 后主动调用
- `get_all_for_snapshot()` 提供 Pipeline 启动一次性快照（§4.3）

Engine 层收到 dataclass 后纯函数计算，严格无 IO（CLAUDE.md §6）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from quantpilot.models.system import UserConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")

_CACHE_TTL_SECONDS = 300
_CACHE_PREFIX = "config:"


class ConfigService:
    """统一配置访问器。

    约束：
    - 纯 Service 层组件，持有 AsyncSession + 可选 Redis
    - Engine 层禁止直接依赖本类（通过 dataclass 参数注入）
    """

    def __init__(
        self,
        session: AsyncSession,
        redis: Any = None,
        *,
        snapshot: dict[str, Any] | None = None,
    ) -> None:
        """构造 ConfigService。

        - `snapshot` 为 None（默认）：常规模式，从 DB + Redis 读取，每次 get_* 触发 IO。
        - `snapshot` 非 None：冻结模式（Phase 10 §4.3 / §4.4 评审 C-01/C-02/C-03）；
          所有 get_* 直接从该 dict 派生 dataclass，**完全不触达 DB/Redis**，
          供 DailyPipeline / BacktestEngine 后台任务消费 `*.config_snapshot`。
        """
        self._session = session
        self._redis = redis
        self._snapshot = snapshot

    # ---------------- 12 个类型化 getter ----------------

    async def get_signal_params(self) -> SignalConfig:
        return await self._get_typed("signal_params", SignalConfig, DEFAULT_SIGNAL_CONFIG)

    async def get_risk_limits(self) -> RiskLimitsConfig:
        return await self._get_typed("risk_limits", RiskLimitsConfig, DEFAULT_RISK_LIMITS)

    async def get_market_state_params(self) -> MarketStateConfig:
        return await self._get_typed(
            "market_state_params", MarketStateConfig, DEFAULT_MARKET_STATE
        )

    async def get_universe_params(self) -> UniverseConfig:
        return await self._get_typed("universe_params", UniverseConfig, DEFAULT_UNIVERSE)

    async def get_strategy_weights(self) -> StrategyWeightsConfig:
        return await self._get_typed(
            "strategy_weights", StrategyWeightsConfig, DEFAULT_STRATEGY_WEIGHTS
        )

    async def get_strategy_params_trend(self) -> TrendStrategyConfig:
        return await self._get_typed(
            "strategy_params_trend", TrendStrategyConfig, DEFAULT_TREND_STRATEGY
        )

    async def get_strategy_params_momentum(self) -> MomentumStrategyConfig:
        return await self._get_typed(
            "strategy_params_momentum", MomentumStrategyConfig, DEFAULT_MOMENTUM_STRATEGY
        )

    async def get_strategy_params_mean_reversion(self) -> MeanReversionStrategyConfig:
        return await self._get_typed(
            "strategy_params_mean_reversion",
            MeanReversionStrategyConfig,
            DEFAULT_MEAN_REVERSION_STRATEGY,
        )

    async def get_strategy_params_value(self) -> ValueStrategyConfig:
        return await self._get_typed(
            "strategy_params_value", ValueStrategyConfig, DEFAULT_VALUE_STRATEGY
        )

    async def get_backtest_defaults(self) -> BacktestDefaultsConfig:
        return await self._get_typed(
            "backtest_defaults", BacktestDefaultsConfig, DEFAULT_BACKTEST_DEFAULTS
        )

    async def get_notification_prefs(self) -> NotificationConfig:
        return await self._get_typed(
            "notification_prefs", NotificationConfig, DEFAULT_NOTIFICATION
        )

    async def get_factor_monitor_params(self) -> FactorMonitorConfig:
        return await self._get_typed(
            "factor_monitor_params", FactorMonitorConfig, DEFAULT_FACTOR_MONITOR
        )

    # ---------------- 快照（§4.3 / §4.4）----------------

    async def get_pipeline_snapshot(self) -> dict[str, Any]:
        """Pipeline 启动入口使用（§4.3）：11 个运行时 config_key 的快照。

        不含 `backtest_defaults`（仅回测端点使用）和 `risk_free_rate`（绩效计算用）。
        特殊键 `_snapshot_at` 附带快照时刻（ISO8601 UTC）。
        """
        return {
            "signal_params": asdict(await self.get_signal_params()),
            "risk_limits": asdict(await self.get_risk_limits()),
            "market_state_params": asdict(await self.get_market_state_params()),
            "universe_params": asdict(await self.get_universe_params()),
            "strategy_weights": asdict(await self.get_strategy_weights()),
            "strategy_params_trend": asdict(await self.get_strategy_params_trend()),
            "strategy_params_momentum": asdict(await self.get_strategy_params_momentum()),
            "strategy_params_mean_reversion": asdict(
                await self.get_strategy_params_mean_reversion()
            ),
            "strategy_params_value": asdict(await self.get_strategy_params_value()),
            "notification_prefs": asdict(await self.get_notification_prefs()),
            "factor_monitor_params": asdict(await self.get_factor_monitor_params()),
            "_snapshot_at": datetime.now(timezone.utc).isoformat(),
        }

    async def get_all_for_snapshot(self) -> dict[str, Any]:
        """回测端点使用（§4.4）：pipeline 全量 + `backtest_defaults`。

        写入 `backtest_task.config_snapshot`，供结果复现。
        """
        snapshot = await self.get_pipeline_snapshot()
        snapshot["backtest_defaults"] = asdict(await self.get_backtest_defaults())
        return snapshot

    # ---------------- 缓存失效 ----------------

    async def invalidate(self, key: str) -> None:
        """PUT /settings 成功后调用。无 Redis 时 no-op。"""
        if self._redis is None:
            return
        try:
            await self._redis.delete(f"{_CACHE_PREFIX}{key}")
        except Exception as e:
            # 降级：缓存失效失败不影响写入，最多下次 get 读到旧值（TTL 300s 自然失效）
            logger.warning("config cache invalidate failed for %s: %s", key, e)

    # ---------------- 内部：读 + 合并 + 缓存 ----------------

    async def _get_typed(self, key: str, cls: type[T], default: T) -> T:
        """读 DB + 部分覆盖默认值 + Redis 缓存。

        流程：
        1. Redis 命中 → 解码 db_value → merge(default, db_value) → 返回 cls 实例
        2. Redis miss → 查 DB → 写缓存 → merge → 返回
        3. DB miss → db_value={} → 直接返回 default 的克隆

        合并语义：dataclass 顶层字段为 dict 时进行 1 层深合并（如
        `StrategyWeightsConfig.uptrend/downtrend/oscillation`），避免用户只
        提交 `uptrend.trend` 时把 `uptrend` 内的其它策略权重清零。

        Phase 10 评审 C-01/C-02/C-03：当 `self._snapshot` 非 None 时进入冻结模式，
        从 snapshot dict 直接派生 dataclass，**不查 DB/Redis**——供 Pipeline/Backtest
        后台路径消费 `*.config_snapshot`。
        """
        if self._snapshot is not None:
            db_value = self._snapshot.get(key) or {}
        else:
            db_value = await self._cache_get(key)
            if db_value is None:
                row = await self._session.execute(
                    select(UserConfig.config_value).where(UserConfig.config_key == key)
                )
                db_value = row.scalar_one_or_none() or {}
                await self._cache_set(key, db_value)

        if not db_value:
            # 用 default 的 asdict 重建一次，保持同一类型路径（避免外部修改默认实例）
            return cls(**asdict(default))  # type: ignore[call-arg]
        default_dict = asdict(default)  # type: ignore[call-arg]
        # 过滤未知字段，避免 dataclass 构造失败（用户若存旧版字段）
        merged = _deep_merge(default_dict, db_value)
        merged = {k: v for k, v in merged.items() if k in default_dict}
        try:
            return cls(**merged)  # type: ignore[call-arg]
        except TypeError as e:
            # 【降级说明】DB 值结构损坏（如用户手工改 SQL）：记 ERROR 并回退默认，
            # 避免整条 Pipeline 因单 key 损坏挂掉。恢复条件：用户通过 PUT /settings 覆盖。
            logger.error("config %s db_value invalid, fallback to default: %s", key, e)
            return cls(**asdict(default))  # type: ignore[call-arg]

    async def _cache_get(self, key: str) -> dict | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(f"{_CACHE_PREFIX}{key}")
        except Exception as e:
            logger.warning("config cache get failed for %s: %s", key, e)
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    async def _cache_set(self, key: str, value: dict) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.setex(
                f"{_CACHE_PREFIX}{key}",
                _CACHE_TTL_SECONDS,
                json.dumps(value),
            )
        except Exception as e:
            logger.warning("config cache set failed for %s: %s", key, e)


def _deep_merge(default: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并：default 为基础，override 中的同名 key 覆盖。

    当 default 与 override 的同名值都是 dict 时，逐键合并；否则 override 直接覆盖。
    用于 `StrategyWeightsConfig` 等 nested dict 字段的 partial overlay：用户写入
    `{"uptrend": {"trend": 0.5}}` 时保留默认 `momentum/mean_reversion/value`，
    避免归一化时其它策略权重被清零。
    """
    out = dict(default)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


__all__ = ["ConfigService"]
