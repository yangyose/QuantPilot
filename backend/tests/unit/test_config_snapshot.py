"""unit/test_config_snapshot.py：from_snapshot 同步派生 dataclass 单元测试。

验证 Phase 10 §4.3 评审 C-01/C-02/C-03 的快照消费链路：
- snapshot=None / 子值缺失 → 默认 dataclass
- 子值结构正确 → 与默认值深合并
- 子值结构损坏 → 回退默认值
"""
from __future__ import annotations

from dataclasses import asdict

import pytest

from quantpilot.core.config_defaults import (
    DEFAULT_RISK_LIMITS,
    DEFAULT_SIGNAL_CONFIG,
    DEFAULT_STRATEGY_WEIGHTS,
    RiskLimitsConfig,
    SignalConfig,
    StrategyWeightsConfig,
)
from quantpilot.services.config_snapshot import from_snapshot


def test_from_snapshot_none_returns_default() -> None:
    """snapshot=None → 默认 dataclass 副本。"""
    result = from_snapshot(None, "signal_params")
    assert isinstance(result, SignalConfig)
    assert asdict(result) == asdict(DEFAULT_SIGNAL_CONFIG)


def test_from_snapshot_empty_dict_returns_default() -> None:
    """snapshot={} → 默认值。"""
    assert asdict(from_snapshot({}, "signal_params")) == asdict(DEFAULT_SIGNAL_CONFIG)


def test_from_snapshot_missing_key_returns_default() -> None:
    """snapshot 缺指定 key → 默认值。"""
    snap = {"risk_limits": {"max_single_stock_pct": 0.30}}
    result = from_snapshot(snap, "signal_params")
    assert asdict(result) == asdict(DEFAULT_SIGNAL_CONFIG)


def test_from_snapshot_partial_overlay() -> None:
    """子值部分字段 → 其余字段保留默认。"""
    snap = {"signal_params": {"buy_threshold": 92.5}}
    result = from_snapshot(snap, "signal_params")
    assert isinstance(result, SignalConfig)
    assert result.buy_threshold == 92.5
    assert result.sell_threshold == DEFAULT_SIGNAL_CONFIG.sell_threshold


def test_from_snapshot_filters_unknown_fields() -> None:
    """子值含未知字段（旧版兼容） → 过滤掉，不抛异常。"""
    snap = {"risk_limits": {"max_single_stock_pct": 0.18, "deprecated_x": 999}}
    result = from_snapshot(snap, "risk_limits")
    assert isinstance(result, RiskLimitsConfig)
    assert result.max_single_stock_pct == 0.18


def test_from_snapshot_nested_partial_overlay() -> None:
    """StrategyWeightsConfig nested dict 部分覆盖 → 同 ConfigService._deep_merge。"""
    snap = {"strategy_weights": {"uptrend": {"trend": 0.55}}}
    result = from_snapshot(snap, "strategy_weights")
    assert isinstance(result, StrategyWeightsConfig)
    assert result.uptrend["trend"] == 0.55
    # uptrend.momentum/mean_reversion/value 保留默认
    assert result.uptrend["momentum"] == DEFAULT_STRATEGY_WEIGHTS.uptrend["momentum"]
    assert result.downtrend == DEFAULT_STRATEGY_WEIGHTS.downtrend


def test_from_snapshot_invalid_key_raises() -> None:
    """非 12-key 之一 → KeyError（编程错误，应在测试中暴露）。"""
    with pytest.raises(KeyError):
        from_snapshot({}, "unknown_key")


def test_from_snapshot_non_dict_value_falls_back_to_default() -> None:
    """子值非 dict（JSONB 被外部写入 list/str） → 记 ERROR 并回退默认。"""
    snap = {"signal_params": "garbage-string"}
    result = from_snapshot(snap, "signal_params")
    assert isinstance(result, SignalConfig)
    assert asdict(result) == asdict(DEFAULT_SIGNAL_CONFIG)


def test_from_snapshot_list_value_falls_back_to_default() -> None:
    """子值为 list（JSONB 异常结构） → 回退默认。"""
    snap = {"risk_limits": [1, 2, 3]}
    result = from_snapshot(snap, "risk_limits")
    assert isinstance(result, RiskLimitsConfig)
    assert asdict(result) == asdict(DEFAULT_RISK_LIMITS)
