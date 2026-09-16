"""策略名的**单一事实来源**（V1.5-C C3 / 设计 §8.3 陷阱 3）。

## 为什么需要它

策略名清单此前散在**五处**（设计只数到四处，`api/v1/factor_quality.py` 那份漏了）：

| 位置 | 形态 |
|---|---|
| `engine/scorer.py::_STRATEGY_KEYS` | tuple |
| `engine/scorer.py::SCORE_COLUMN_MAP` | 策略 key → DB 列名（**不规则**，见下）|
| `services/factor_monitor_service.py::_STRATEGY_NAMES` | tuple |
| `services/factor_monitor_service.py::_FACTOR_MAP` | DB 列名 → (类名, 列名) |
| `api/v1/factor_quality.py::_STRATEGY_NAMES` | tuple |
| `core/config_defaults.py::StrategyWeightsConfig` | 三个 state dict |

「改了三处漏第四处」是本项目反复踩的坑，而且**同一天内已被同族形态咬三次**
（`get_latest_financial` 共用报告期挡掉稀疏字段、适配器 `fina_cols` merge 白名单、
`_FINANCIAL_UPDATE_COLS` upsert 白名单）——共同点是：漏掉时**不报错、测试全绿**。

## ⚠️ `mean_reversion` 的列名是不规则的

DB 列叫 `reversion_score` 而非 `mean_reversion_score`（历史遗留）。
**不要「统一」它**——那会静默写错列。`SCORE_COLUMN_MAP` 显式记下这个例外，
`test_strategy_registry.py` 锁死。

## 加新策略的落点

改这里**一处**即可；各消费方引用本模块的对象（不是复制内容——
留内容相同的副本会让任何「内容相等」的断言照样绿，而下次加策略仍会漏）。
新策略按影子模式登记权重 0.0（设计 §8.2）。
"""
from __future__ import annotations

__all__ = [
    "SCORE_COLUMN_MAP",
    "STRATEGY_DISPLAY_NAMES",
    "STRATEGY_NAMES",
    "build_top_drivers",
    "score_column",
]

# 顺序有意义：`default_matrix` 与各处展示按此序。V1.0 四策略在前，V1.5+ 新增在后。
STRATEGY_NAMES: tuple[str, ...] = (
    "trend",
    "momentum",
    "mean_reversion",
    "value",
    # V1.5-C C3（SDD §7.3）——影子模式，权重从 0 起步
    "low_volatility",
    # V1.5-C C4（SDD §7.3）——影子模式，权重从 0 起步
    "money_flow",
)

# 策略 key → `candidate_pool` 的分数列名。
# ⚠️ `mean_reversion` → `reversion_score` 是**不规则**的历史遗留，勿"统一"。
SCORE_COLUMN_MAP: dict[str, str] = {
    "trend": "trend_score",
    "momentum": "momentum_score",
    "mean_reversion": "reversion_score",
    "value": "value_score",
    "low_volatility": "low_volatility_score",
    "money_flow": "money_flow_score",
}


def score_column(strategy: str) -> str:
    """策略名 → DB 分数列名；未登记即 KeyError（属编码错误，不静默兜底）。"""
    return SCORE_COLUMN_MAP[strategy]


# 策略名 → **给用户看的中文名**。
# ⚠️ 必须与各策略类的 `display_name` 逐字相等，由 `test_strategy_registry.py` 的
# 契约测试钉死——这里之所以要独立一份而不是去 import 策略类，是为了让
# `core/` 不反向依赖 `engine/strategies/`（会成环）；代价是两份可能漂，
# 所以那条契约测试是必需的，不是锦上添花。
STRATEGY_DISPLAY_NAMES: dict[str, str] = {
    "trend": "趋势跟踪",
    "momentum": "动量",
    "mean_reversion": "均值回归",
    "value": "价值",
    "low_volatility": "低波动",
    "money_flow": "资金动向",
}


def build_top_drivers(
    breakdown_raw: object, *, top_n: int = 2, sep: str = " · "
) -> str | None:
    """按 contribution 降序取前 N 个策略，返回中文展示名串（如 `"价值 · 均值回归"`）。

    输入是 `candidate_pool.score_breakdown_raw` / `CompositeScore.score_breakdown_raw`
    的形状：`{策略内部名: {"z_raw": …, "weight": …, "contribution": …}}`。

    ## 为什么需要它（2026-09-11）

    SDD §9.1 要求买入信号展示 `top_contributors`（前 2 贡献策略）与 `explanation`。
    `Scorer.aggregate` 本来就在算后者，但**零消费者**且它直接
    `" · ".join(内部键名)`——真接到用户面前会显示「value · mean_reversion」，
    而规范例子要的是「价值 · 均值回归」。

    故把「排序 + 译名 + 拼接」收敛到这**一处**：`Scorer` 与 `SignalGenerator`
    同用，避免两处各写一份而措辞分叉。

    ## 口径

    - 不可用（None / 空 / 非 dict / 无有效 contribution）→ 返回 **None**，
      调用方据此**整段省略**，不要拼出一个空的「主要驱动：」（C-4：不编造）
    - **未登记的策略名原样保留**：静默丢弃会让驱动少一项且无人察觉，
      而露出内部名至少能暴露「有个策略没登记进 STRATEGY_DISPLAY_NAMES」
    """
    if not isinstance(breakdown_raw, dict) or not breakdown_raw:
        return None

    scored: list[tuple[str, float]] = []
    for name, payload in breakdown_raw.items():
        if not isinstance(payload, dict):
            continue
        raw = payload.get("contribution")
        try:
            value = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if value != value:      # NaN
            continue
        scored.append((str(name), value))

    if not scored:
        return None
    scored.sort(key=lambda kv: kv[1], reverse=True)
    picked = scored[: max(1, top_n)]
    return sep.join(STRATEGY_DISPLAY_NAMES.get(name, name) for name, _ in picked)
