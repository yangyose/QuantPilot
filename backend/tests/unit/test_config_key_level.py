"""V1.5-G G-4a 单元测试：config_key → 所需 level 静态映射 + 可见性判定（§6.3）。

SDD §14.1 设置表「适用层级」列（All / L2+ / L3）→ user.level 枚举（L1/L2/L3）映射：
All→L1（人人可见）、L2+→L2、L3→L3。过滤规则：仅当 config 所需 level <= user.level
时可见（L1 用户看不到 L2/L3 高级参数）。
"""
from __future__ import annotations

from quantpilot.core.config_defaults import CONFIG_KEY_LEVEL, config_visible_at_level


def test_config_key_level_covers_all_whitelist_keys() -> None:
    """12 个白名单 config_key 全部有 level 归属（无遗漏 → 过滤时不会 KeyError）。"""
    whitelist = {
        "signal_params", "risk_limits", "market_state_params", "universe_params",
        "strategy_weights", "strategy_params_trend", "strategy_params_momentum",
        "strategy_params_mean_reversion", "strategy_params_value",
        "backtest_defaults", "notification_prefs", "factor_monitor_params",
    }
    assert whitelist <= set(CONFIG_KEY_LEVEL)


def test_config_key_level_values_are_valid_enum() -> None:
    """所有 level 值规范化为 L1/L2/L3 枚举（无 All/L2+ 字面，否则字符串 <= 比较失效）。"""
    assert set(CONFIG_KEY_LEVEL.values()) <= {"L1", "L2", "L3"}


def test_notification_prefs_visible_to_l1() -> None:
    """notification_prefs = SDD §14.4 提醒设置（All）→ L1 可见。"""
    assert config_visible_at_level("notification_prefs", "L1") is True


def test_signal_params_hidden_from_l1_visible_l2() -> None:
    """signal_params = SDD §14.1 买/卖阈值（L2+）→ L1 隐藏、L2/L3 可见。"""
    assert config_visible_at_level("signal_params", "L1") is False
    assert config_visible_at_level("signal_params", "L2") is True
    assert config_visible_at_level("signal_params", "L3") is True


def test_strategy_weights_only_l3() -> None:
    """strategy_weights = SDD §14.3 权重配置（L3）→ 仅 L3 可见。"""
    assert config_visible_at_level("strategy_weights", "L1") is False
    assert config_visible_at_level("strategy_weights", "L2") is False
    assert config_visible_at_level("strategy_weights", "L3") is True


def test_unknown_key_defaults_hidden_below_l3() -> None:
    """未登记 key 保守按 L3 处理（默认最严，避免误暴露给低层级）。"""
    assert config_visible_at_level("some_unknown_key", "L1") is False
    assert config_visible_at_level("some_unknown_key", "L3") is True
