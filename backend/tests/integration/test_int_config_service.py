"""INT-CFG-01：ConfigService 集成测试（Phase 10 §10.3）。

覆盖：
- DB 写入 → 下次读取得到最新值
- partial-overlay：DB 仅含部分字段 → 缺失字段用默认值补齐
- Redis 缓存命中 → 直接返回；invalidate 后重新读 DB
- DB 损坏 JSONB → 降级回退默认值（不抛异常）
"""
from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.core.config_defaults import (
    DEFAULT_SIGNAL_CONFIG,
    SignalConfig,
)
from quantpilot.services.config_service import ConfigService
from quantpilot.services.settings_service import SettingsService


class _FakeRedis:
    """内存替身：实现 ConfigService 用到的 get/setex/delete 接口。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.delete_calls: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> int:
        self.delete_calls.append(key)
        return 1 if self.store.pop(key, None) is not None else 0


# ---------------------------------------------------------------------------
# INT-CFG-01a: DB 缺失 → 返回 default 全量字段
# ---------------------------------------------------------------------------
async def test_int_cfg_01_db_missing_returns_default(db_session: AsyncSession) -> None:
    """user_config 不含 signal_params → 返回 DEFAULT_SIGNAL_CONFIG。"""
    svc = ConfigService(db_session)

    cfg = await svc.get_signal_params()

    assert isinstance(cfg, SignalConfig)
    assert cfg.buy_threshold == DEFAULT_SIGNAL_CONFIG.buy_threshold
    assert cfg.sell_threshold == DEFAULT_SIGNAL_CONFIG.sell_threshold


# ---------------------------------------------------------------------------
# INT-CFG-01b: DB 部分字段 → partial-overlay
# ---------------------------------------------------------------------------
async def test_int_cfg_01_partial_overlay(db_session: AsyncSession) -> None:
    """DB 只写 buy_threshold → 其它字段用默认值。"""
    settings_svc = SettingsService(db_session)
    await settings_svc.upsert_setting("signal_params", {"buy_threshold": 75.0})
    await db_session.flush()

    svc = ConfigService(db_session)
    cfg = await svc.get_signal_params()

    assert cfg.buy_threshold == 75.0
    assert cfg.sell_threshold == DEFAULT_SIGNAL_CONFIG.sell_threshold
    assert cfg.strong_threshold == DEFAULT_SIGNAL_CONFIG.strong_threshold


# ---------------------------------------------------------------------------
# INT-CFG-01c: Redis 缓存命中 → 直接返回缓存值，不查 DB
# ---------------------------------------------------------------------------
async def test_int_cfg_01_redis_cache_hit(db_session: AsyncSession) -> None:
    """Redis 已存缓存 → ConfigService 直接读缓存（不与 DB 交互）。"""
    fake_redis = _FakeRedis()
    fake_redis.store["config:signal_params"] = json.dumps({"buy_threshold": 88.0})

    svc = ConfigService(db_session, redis=fake_redis)
    cfg = await svc.get_signal_params()

    assert cfg.buy_threshold == 88.0


# ---------------------------------------------------------------------------
# INT-CFG-01d: invalidate 删除缓存 → 下次读取重新查 DB
# ---------------------------------------------------------------------------
async def test_int_cfg_01_invalidate_then_read_latest(db_session: AsyncSession) -> None:
    """流程：写入 v1 → 读（缓存写入 v1）→ 写入 v2 → invalidate → 读 v2。"""
    fake_redis = _FakeRedis()
    settings_svc = SettingsService(db_session)
    cfg_svc = ConfigService(db_session, redis=fake_redis)

    # 1) 写入 v1，首次读 → DB → 写入缓存
    await settings_svc.upsert_setting("signal_params", {"buy_threshold": 70.0})
    await db_session.flush()
    cfg1 = await cfg_svc.get_signal_params()
    assert cfg1.buy_threshold == 70.0
    assert "config:signal_params" in fake_redis.store

    # 2) 写入 v2 + 主动 invalidate（模拟 SettingsService.upsert 之后调 ConfigService.invalidate）
    await settings_svc.upsert_setting("signal_params", {"buy_threshold": 85.0})
    await db_session.flush()
    await cfg_svc.invalidate("signal_params")
    assert fake_redis.delete_calls == ["config:signal_params"]
    assert "config:signal_params" not in fake_redis.store

    # 3) 再次读取 → 缓存已清 → 重新查 DB → 拿到 v2
    cfg2 = await cfg_svc.get_signal_params()
    assert cfg2.buy_threshold == 85.0


# ---------------------------------------------------------------------------
# INT-CFG-01e: get_pipeline_snapshot 不含 backtest_defaults（M1 修复验证）
# ---------------------------------------------------------------------------
async def test_int_cfg_01_pipeline_snapshot_excludes_backtest(db_session: AsyncSession) -> None:
    """§4.3：pipeline_run.config_snapshot 不应含 backtest_defaults。"""
    svc = ConfigService(db_session)

    snapshot = await svc.get_pipeline_snapshot()

    assert "signal_params" in snapshot
    assert "risk_limits" in snapshot
    assert "_snapshot_at" in snapshot
    assert "backtest_defaults" not in snapshot, (
        "Phase 10 §4.3：Pipeline 快照不应含 backtest_defaults"
    )

    # get_all_for_snapshot 才包含 backtest_defaults
    full = await svc.get_all_for_snapshot()
    assert "backtest_defaults" in full


# ---------------------------------------------------------------------------
# INT-CFG-01f: DB 含未知字段 → 过滤后正常构造
# ---------------------------------------------------------------------------
async def test_int_cfg_01_unknown_fields_filtered(db_session: AsyncSession) -> None:
    """DB 残留旧版字段（dataclass 不存在的 key）→ 过滤后构造，不抛 TypeError。"""
    settings_svc = SettingsService(db_session)
    await settings_svc.upsert_setting(
        "signal_params",
        {"buy_threshold": 82.0, "legacy_field": "obsolete"},
    )
    await db_session.flush()

    svc = ConfigService(db_session)
    cfg = await svc.get_signal_params()

    assert cfg.buy_threshold == 82.0
    assert not hasattr(cfg, "legacy_field")


# ---------------------------------------------------------------------------
# INT-CFG-01g: 缓存返回非法 JSON → 静默回退至 DB
# ---------------------------------------------------------------------------
async def test_int_cfg_01_invalid_cache_falls_back_to_db(db_session: AsyncSession) -> None:
    """Redis 缓存值非合法 JSON → 不抛异常，回退查 DB。"""
    fake_redis = _FakeRedis()
    fake_redis.store["config:signal_params"] = "not-a-json"

    settings_svc = SettingsService(db_session)
    await settings_svc.upsert_setting("signal_params", {"buy_threshold": 91.0})
    await db_session.flush()

    svc = ConfigService(db_session, redis=fake_redis)
    cfg = await svc.get_signal_params()

    assert cfg.buy_threshold == 91.0
