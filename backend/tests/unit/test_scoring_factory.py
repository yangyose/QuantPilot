"""V1.5-C C1-2：`scoring_factory` 的策略覆写入口。

设计文档 §3.2 要求 `risk_adjusted=False` 能「一键回退对照」——C1 DoD 的 5y 面板
对比要在**同一份数据、同一条评分管线**上跑两遍，只差 momentum 这一个开关。

原先 `build_default_scoring_service` 硬绑 `DEFAULT_MOMENTUM_STRATEGY`，唯一的切换
办法是手改 `config_defaults.py` 再跑一遍——改完忘了改回来就会把「对照组」配置带进
生产路径，且事后无从判断某批 IC 行到底是哪个配置产出的。故把覆写做成显式入参 +
CLI 开关：命令行本身即产出的出处记录。
"""
from __future__ import annotations

from quantpilot.core.config_defaults import DEFAULT_MOMENTUM_STRATEGY
from quantpilot.engine.strategies.momentum import MomentumStrategy
from quantpilot.services.scoring_factory import build_default_strategies


def _momentum_of(strategies: list) -> MomentumStrategy:
    found = [s for s in strategies if isinstance(s, MomentumStrategy)]
    assert len(found) == 1, f"应恰有一个 MomentumStrategy，实得 {len(found)}"
    return found[0]


def test_ut_c1_10a_default_strategies_keep_config_defaults() -> None:
    """UT-C1-10a: 不传覆写时，momentum 配置 = DEFAULT_MOMENTUM_STRATEGY（默认开风险调整）。"""
    strategies = build_default_strategies()

    assert len(strategies) == 4
    assert _momentum_of(strategies)._cfg == DEFAULT_MOMENTUM_STRATEGY
    assert DEFAULT_MOMENTUM_STRATEGY.risk_adjusted is True


def test_ut_c1_10b_momentum_risk_adjusted_override_takes_effect() -> None:
    """UT-C1-10b: 覆写 momentum_risk_adjusted=False 必须真正落到策略实例上。

    「改参数 → 结果必须变」（CLAUDE.md §4.4）：接了开关却没接进计算的代码，
    能跑过任何只验证「不抛异常」的测试。
    """
    strategies = build_default_strategies(momentum_risk_adjusted=False)

    cfg = _momentum_of(strategies)._cfg
    assert cfg.risk_adjusted is False
    # 其余字段不得被覆写顺手改掉——对照组只允许差这一个变量
    assert cfg.lookback_short == DEFAULT_MOMENTUM_STRATEGY.lookback_short
    assert cfg.lookback_long == DEFAULT_MOMENTUM_STRATEGY.lookback_long
    assert cfg.volatility_window == DEFAULT_MOMENTUM_STRATEGY.volatility_window
    assert cfg.reversal_exclude_pct == DEFAULT_MOMENTUM_STRATEGY.reversal_exclude_pct


def test_ut_c1_10c_override_does_not_mutate_module_default() -> None:
    """UT-C1-10c: 覆写不得污染 `DEFAULT_MOMENTUM_STRATEGY` 模块级单例。

    dataclass 单例被就地改写的话，同进程内后续 `build_default_strategies()`
    会静默继承对照组配置——正是这个脚本要避免的「不知道产出来自哪个配置」。
    """
    build_default_strategies(momentum_risk_adjusted=False)

    assert DEFAULT_MOMENTUM_STRATEGY.risk_adjusted is True
    assert _momentum_of(build_default_strategies())._cfg.risk_adjusted is True


def test_ut_c1_10d_override_none_is_noop() -> None:
    """UT-C1-10d: 显式传 None = 不覆写（脚本默认值路径）。"""
    cfg = _momentum_of(build_default_strategies(momentum_risk_adjusted=None))._cfg

    assert cfg == DEFAULT_MOMENTUM_STRATEGY
