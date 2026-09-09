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

__all__ = ["SCORE_COLUMN_MAP", "STRATEGY_NAMES", "score_column"]

# 顺序有意义：`default_matrix` 与各处展示按此序。V1.0 四策略在前，V1.5+ 新增在后。
STRATEGY_NAMES: tuple[str, ...] = (
    "trend",
    "momentum",
    "mean_reversion",
    "value",
    # V1.5-C C3（SDD §7.3）——影子模式，权重从 0 起步
    "low_volatility",
)

# 策略 key → `candidate_pool` 的分数列名。
# ⚠️ `mean_reversion` → `reversion_score` 是**不规则**的历史遗留，勿"统一"。
SCORE_COLUMN_MAP: dict[str, str] = {
    "trend": "trend_score",
    "momentum": "momentum_score",
    "mean_reversion": "reversion_score",
    "value": "value_score",
    "low_volatility": "low_volatility_score",
}


def score_column(strategy: str) -> str:
    """策略名 → DB 分数列名；未登记即 KeyError（属编码错误，不静默兜底）。"""
    return SCORE_COLUMN_MAP[strategy]
