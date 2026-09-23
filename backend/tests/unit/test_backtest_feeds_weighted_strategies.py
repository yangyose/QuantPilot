"""回测必须喂得起每个**有权重**的策略（2026-09-23）。

## 为什么需要这条

`BacktestDataBundle` / 引擎自建的 `MarketSnapshot` **没有 `money_flow`** 这一项，于是
`MoneyFlowStrategy.compute_raw_factors` 在回测里逐日拿到 `None` → 两个因子全 NaN →
`Scorer.aggregate` 打出 `scorer_strategy_skipped_all_nan` 并跳过该策略（`backtest_service.py`
里有注释说明这是影子期的有意状态）。当前 `money_flow` 三态权重都是 0.0，所以**结果不受影响**。

问题在于「有意」只存在于注释里：C4 转正（月末 rebalance 自动激活，或有人手动改权重）那一刻，
回测会**静默**地按「少一个策略」的口径算，而日志里只有一行 INFO——这正是 §4.11 那一族
（机制接了、数据没接、不报错、测试全绿）。本文件把这层依赖钉成可执行判据：

**任一策略的默认权重 > 0 → 回测侧必须能给它喂数据。** 现在 `money_flow` 是唯一喂不起的，
故等价于「money_flow 权重必须仍是 0，否则先补 bundle」。补完后把它从 `_UNFED` 移除即可。

⚠️ 判据只看**默认权重矩阵**（`config_defaults.StrategyWeightsConfig`）——ICIR/用户覆盖是
运行时状态，测试拿不到；而转正的第一步必然是改这个矩阵或由 rebalance 写库，前者被本条拦住，
后者在 roadmap V1.5-L 登记为「C4 转正前置」。
"""
from __future__ import annotations

import inspect

from quantpilot.core.config_defaults import DEFAULT_STRATEGY_WEIGHTS
from quantpilot.core.strategy_registry import STRATEGY_NAMES

# 回测快照喂不起的策略（bundle 里没有对应数据源）。补上数据后从这里删掉。
_UNFED: dict[str, str] = {
    "money_flow": "BacktestDataBundle 无 money_flow；需加载 money_flow 表并按日切片进快照",
}


def _states() -> dict[str, dict[str, float]]:
    return {
        "uptrend": DEFAULT_STRATEGY_WEIGHTS.uptrend,
        "downtrend": DEFAULT_STRATEGY_WEIGHTS.downtrend,
        "oscillation": DEFAULT_STRATEGY_WEIGHTS.oscillation,
    }


def test_unfed_strategies_must_have_zero_default_weight() -> None:
    for state, weights in _states().items():
        for name, why in _UNFED.items():
            w = float(weights.get(name, 0.0))
            assert w == 0.0, (
                f"{name} 在 {state} 的默认权重为 {w}，但回测喂不起它 → 回测会静默少算一个策略。"
                f"先补数据：{why}"
            )


def _money_flow_is_fed() -> bool:
    """回测侧现在到底喂不喂 money_flow（只看现实，不看 `_UNFED`）。

    判据取**结构**而非某个函数名：bundle 有该字段 **且** 引擎构造当日快照时真填了这一键。
    「Service 用哪个取数函数加载」不纳入——换成别的 loader 模块照样算喂上了，钉函数名
    会在功能补全后仍判 False（2026-09-23 冷启动评审指出这层脆性）。
    """
    from quantpilot.engine.backtest import engine as bt_engine

    in_bundle = "money_flow" in set(bt_engine.BacktestDataBundle.__dataclass_fields__)
    in_snapshot = '"money_flow"' in inspect.getsource(bt_engine.BacktestEngine.run)
    return in_bundle and in_snapshot


def test_unfed_list_matches_reality() -> None:
    """`_UNFED` 必须与现实一致——两个方向都钉（2026-09-23 冷启动评审补齐第二个方向）。

    - 谁把 bundle 补好却忘了删 `_UNFED` → 第一条会永久挡住 C4 转正（**假阳性**）；
    - 谁把 `_UNFED` 清空却没真补数据 → 第一条无项可循环、恒过（**假阴性**）。
      首版这条只独立断言「现实还没接」，不引用 `_UNFED`，因此拦不住后者；现在用
      `money_flow ∈ _UNFED ⟺ 回测喂不起它` 把两者绑定。
    """
    fed = _money_flow_is_fed()
    listed = "money_flow" in _UNFED
    assert listed == (not fed), (
        f"_UNFED 与现实不一致：money_flow 在 bundle 里喂得起={fed}，而 _UNFED 里登记={listed}。"
        "补完数据请从 _UNFED 删除该项；反之不要在没补数据时删它。"
    )


def test_every_registered_strategy_is_either_fed_or_listed() -> None:
    """`_UNFED` 的键必须是真实策略名（打错字 = 上面两条全部失效）。"""
    assert set(_UNFED).issubset(set(STRATEGY_NAMES)), (
        f"_UNFED 含未登记的策略名：{set(_UNFED) - set(STRATEGY_NAMES)}"
    )
