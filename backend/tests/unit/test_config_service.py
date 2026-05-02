"""unit/test_config_service.py: ConfigService partial-overlay + cache 纯逻辑测试。

使用 FakeSession + FakeRedis，无需真实 DB/Redis。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from quantpilot.core.config_defaults import (
    DEFAULT_RISK_LIMITS,
    DEFAULT_SIGNAL_CONFIG,
    DEFAULT_STRATEGY_WEIGHTS,
    RiskLimitsConfig,
    SignalConfig,
    StrategyWeightsConfig,
)
from quantpilot.services.config_service import ConfigService


class FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class FakeSession:
    """最小化 AsyncSession stub：按 config_key 返回预置字典。"""

    def __init__(self, rows: dict[str, dict]) -> None:
        self._rows = rows
        self.calls = 0  # 记录 execute 调用次数，用于缓存测试

    async def execute(self, stmt: Any) -> FakeResult:
        self.calls += 1
        # 从 stmt.whereclause 提取 config_key 文本比较值
        # 简化：按顺序匹配 — 实际测试通过调用次序精细控制
        # 更可靠的做法：检视 stmt.compile().params
        key = None
        try:
            params = stmt.compile().params
            key = params.get("config_key_1")
        except Exception:
            pass
        return FakeResult(self._rows.get(key))


class FakeRedis:
    """最小化 Redis stub：支持 get/setex/delete。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


# ---------------- 1. DB miss → 返回默认值 ----------------


async def test_db_miss_returns_default() -> None:
    """DB 无行 → 返回与默认值等价的 dataclass 实例。"""
    session = FakeSession(rows={})
    svc = ConfigService(session)  # type: ignore[arg-type]

    result = await svc.get_signal_params()

    assert isinstance(result, SignalConfig)
    assert asdict(result) == asdict(DEFAULT_SIGNAL_CONFIG)


# ---------------- 2. 部分覆盖 ----------------


async def test_partial_overlay_single_field() -> None:
    """DB 只存 buy_threshold=85 → 其他字段保持默认。"""
    session = FakeSession(rows={"signal_params": {"buy_threshold": 85.0}})
    svc = ConfigService(session)  # type: ignore[arg-type]

    result = await svc.get_signal_params()

    assert result.buy_threshold == 85.0
    assert result.sell_threshold == DEFAULT_SIGNAL_CONFIG.sell_threshold  # 默认值
    assert result.stop_loss_pct == DEFAULT_SIGNAL_CONFIG.stop_loss_pct


async def test_partial_overlay_ignores_unknown_fields() -> None:
    """DB 存入未知字段（旧版兼容）→ 过滤掉，不抛异常。"""
    session = FakeSession(
        rows={"risk_limits": {"max_single_stock_pct": 0.25, "deprecated_field": 999}}
    )
    svc = ConfigService(session)  # type: ignore[arg-type]

    result = await svc.get_risk_limits()

    assert isinstance(result, RiskLimitsConfig)
    assert result.max_single_stock_pct == 0.25
    assert result.max_industry_pct == DEFAULT_RISK_LIMITS.max_industry_pct


async def test_partial_overlay_strategy_weights_dict() -> None:
    """StrategyWeightsConfig 的 dict 字段支持整段替换。"""
    custom = {
        "uptrend": {"trend": 0.50, "momentum": 0.20, "mean_reversion": 0.10, "value": 0.20},
    }
    session = FakeSession(rows={"strategy_weights": custom})
    svc = ConfigService(session)  # type: ignore[arg-type]

    result = await svc.get_strategy_weights()

    assert isinstance(result, StrategyWeightsConfig)
    assert result.uptrend["trend"] == 0.50
    # downtrend / oscillation 仍为默认
    assert result.downtrend == DEFAULT_STRATEGY_WEIGHTS.downtrend
    assert result.oscillation == DEFAULT_STRATEGY_WEIGHTS.oscillation


async def test_partial_overlay_strategy_weights_nested_partial() -> None:
    """StrategyWeightsConfig 的 nested dict 部分覆盖：仅写 uptrend.trend → 其它策略权重保留默认。

    Phase 10 评审 C-05：避免用户矩阵编辑器单格修改后归一化把其它权重清零。
    """
    custom = {"uptrend": {"trend": 0.55}}
    session = FakeSession(rows={"strategy_weights": custom})
    svc = ConfigService(session)  # type: ignore[arg-type]

    result = await svc.get_strategy_weights()

    # uptrend.trend 被覆盖
    assert result.uptrend["trend"] == 0.55
    # uptrend 内其它策略权重保留默认（不应被清空）
    assert result.uptrend["momentum"] == DEFAULT_STRATEGY_WEIGHTS.uptrend["momentum"]
    assert result.uptrend["mean_reversion"] == DEFAULT_STRATEGY_WEIGHTS.uptrend["mean_reversion"]
    assert result.uptrend["value"] == DEFAULT_STRATEGY_WEIGHTS.uptrend["value"]
    # downtrend / oscillation 整体未触及
    assert result.downtrend == DEFAULT_STRATEGY_WEIGHTS.downtrend
    assert result.oscillation == DEFAULT_STRATEGY_WEIGHTS.oscillation


# ---------------- 3. Redis 缓存命中跳过 DB ----------------


async def test_redis_hit_skips_db() -> None:
    """Redis 命中时 DB 不被查询（execute.calls == 0）。"""
    redis = FakeRedis()
    redis.store["config:signal_params"] = json.dumps({"buy_threshold": 88.0})
    session = FakeSession(rows={})

    svc = ConfigService(session, redis)  # type: ignore[arg-type]
    result = await svc.get_signal_params()

    assert result.buy_threshold == 88.0
    assert session.calls == 0  # DB 未被查询


async def test_redis_miss_populates_cache() -> None:
    """Redis 未命中 → 查 DB → 回填缓存。"""
    redis = FakeRedis()
    session = FakeSession(rows={"signal_params": {"buy_threshold": 82.0}})

    svc = ConfigService(session, redis)  # type: ignore[arg-type]
    await svc.get_signal_params()

    assert "config:signal_params" in redis.store
    cached = json.loads(redis.store["config:signal_params"])
    assert cached == {"buy_threshold": 82.0}


async def test_invalidate_deletes_cache() -> None:
    """invalidate 清除缓存；之后 get 重新查 DB。"""
    redis = FakeRedis()
    redis.store["config:signal_params"] = json.dumps({"buy_threshold": 88.0})
    session = FakeSession(rows={"signal_params": {"buy_threshold": 75.0}})

    svc = ConfigService(session, redis)  # type: ignore[arg-type]
    await svc.invalidate("signal_params")

    assert "config:signal_params" not in redis.store
    result = await svc.get_signal_params()
    assert result.buy_threshold == 75.0  # 来自 DB，不再是旧缓存


# ---------------- 4. get_all_for_snapshot ----------------


async def test_get_all_for_snapshot_structure() -> None:
    """快照包含 12 个 config_key + _snapshot_at；每项是可 JSON 序列化 dict。"""
    session = FakeSession(rows={})
    svc = ConfigService(session)  # type: ignore[arg-type]

    snap = await svc.get_all_for_snapshot()

    expected_keys = {
        "signal_params", "risk_limits", "market_state_params", "universe_params",
        "strategy_weights",
        "strategy_params_trend", "strategy_params_momentum",
        "strategy_params_mean_reversion", "strategy_params_value",
        "backtest_defaults", "notification_prefs", "factor_monitor_params",
        "_snapshot_at",
    }
    assert set(snap.keys()) == expected_keys
    json.dumps(snap)  # 整体可 JSON 序列化（JSONB 持久化前提）


async def test_snapshot_can_reconstruct_dataclasses() -> None:
    """快照 dict 反序列化回 dataclass 等价原值（Pipeline CP2/CP3 复用模式）。"""
    session = FakeSession(rows={})
    svc = ConfigService(session)  # type: ignore[arg-type]

    snap = await svc.get_all_for_snapshot()
    reconstructed = SignalConfig(**snap["signal_params"])

    assert asdict(reconstructed) == asdict(DEFAULT_SIGNAL_CONFIG)


# ---------------- 5. 无 Redis 时的降级行为 ----------------


async def test_no_redis_still_works() -> None:
    """redis=None 时 get 正常工作；invalidate no-op。"""
    session = FakeSession(rows={"signal_params": {"buy_threshold": 70.0}})
    svc = ConfigService(session, redis=None)  # type: ignore[arg-type]

    result = await svc.get_signal_params()
    assert result.buy_threshold == 70.0

    await svc.invalidate("signal_params")  # 不应抛


# ---------------- 6. snapshot 冻结模式（Phase 10 §4.3 评审 C-01） ----------------


async def test_snapshot_mode_skips_db_and_redis() -> None:
    """snapshot 非 None → 完全不查 DB/Redis，直接派生 dataclass。"""
    redis = FakeRedis()
    redis.store["config:signal_params"] = json.dumps({"buy_threshold": 999.0})  # 旧缓存
    session = FakeSession(rows={"signal_params": {"buy_threshold": 888.0}})  # 旧 DB
    snap = {"signal_params": {"buy_threshold": 77.0}}

    svc = ConfigService(session, redis, snapshot=snap)  # type: ignore[arg-type]
    result = await svc.get_signal_params()

    # 仅来自 snapshot；DB/Redis 均未触达
    assert result.buy_threshold == 77.0
    assert session.calls == 0
    # snapshot 模式不应读 redis（_cache_get 短路）
    assert redis.store["config:signal_params"] == json.dumps({"buy_threshold": 999.0})


async def test_snapshot_mode_missing_key_returns_default() -> None:
    """snapshot 缺失 key → 返回默认 dataclass，仍不查 DB。"""
    session = FakeSession(rows={"signal_params": {"buy_threshold": 99.0}})  # DB 有但应被忽略
    snap = {"risk_limits": {"max_single_stock_pct": 0.20}}  # signal_params 缺失

    svc = ConfigService(session, snapshot=snap)  # type: ignore[arg-type]
    result = await svc.get_signal_params()

    assert result.buy_threshold == DEFAULT_SIGNAL_CONFIG.buy_threshold
    assert session.calls == 0  # 严格不查 DB


async def test_snapshot_mode_strategy_weights_nested_partial() -> None:
    """snapshot 模式同样保留 nested partial overlay 语义。"""
    snap = {"strategy_weights": {"uptrend": {"trend": 0.66}}}
    session = FakeSession(rows={})
    svc = ConfigService(session, snapshot=snap)  # type: ignore[arg-type]

    result = await svc.get_strategy_weights()

    assert result.uptrend["trend"] == 0.66
    # nested partial：未覆盖字段保留默认
    assert result.uptrend["momentum"] == DEFAULT_STRATEGY_WEIGHTS.uptrend["momentum"]
    assert result.downtrend == DEFAULT_STRATEGY_WEIGHTS.downtrend
    assert session.calls == 0
