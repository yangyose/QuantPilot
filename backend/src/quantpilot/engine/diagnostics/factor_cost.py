"""换手成本拖累（K-COST）。V1.5-K K-5。Engine 层纯函数，严格无 IO。

## ⚠️ `1 − J` **不是**换手率

K-4 存的是 Jaccard 相似度 `J = |A∩B| / |A∪B|`。直觉上「换手率 = 1 − J」，**但这是错的**。
设两天头部各 n 只、交集 k 只：

- 真实换手率（被换掉的持仓占比）= `(n − k) / n`
- `1 − J` = `2(n − k) / (2n − k)`

n=5、k=3 时真实换手 `0.4`，而 `1 − J ≈ 0.571`——**高估近一倍**
（两者关系 `turnover = (1 − J) / (1 + J)`，仅在两天头部等长时成立）。

按 `1 − J` 算成本会系统性夸大交易摩擦，进而**把本来能覆盖成本的因子误判为不能覆盖**
——正是 K 主题要避免的那类错误结论。故本模块**直接从头部集合算**，
不吃 J 这个有损摘要。`tests/unit/test_factor_cost.py::TestNotOneMinusJaccard` 钉死它。

## 成本口径复用回测参数（设计文档 §2.4）

`backtest/engine.py` 的买卖价：买 `price × (1 + c + sl)`、卖 `price × (1 − c − st − sl)`，
故换掉一个持仓的往返成本率 = `2c + st + 2sl`。默认 c=0.025% / st=0.05% / sl=0.1% → **0.3%**。

K 另定一套的话，同一个「成本」会在回测与因子验证里得出不同结论——两处口径分叉
正是本仓反复付过代价的形态（红线② / `BACKTEST_ENABLED`）。

## 方向

`value` 存**正数量级**，语义是「从收益中扣减」。与 K-4 的相似度/换手率同族问题：
正负不写死，下游迟早会多减或少减一次，而那不会报错。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quantpilot.core.config_defaults import DEFAULT_BACKTEST_DEFAULTS
from quantpilot.engine.diagnostics.factor_ic import PanelStatPoint
from quantpilot.engine.diagnostics.factor_portfolio import _TOP_PCT, select_top_pct
from quantpilot.engine.diagnostics.ic_aggregator import _DAILY_IC_MIN_XS

__all__ = ["ROUND_TRIP_RATE", "CostParams", "compute_cost_drag"]

_COST_HORIZON = 0          # 成本无前向概念（与 turnover_jaccard / valid_ratio 同约定）


@dataclass(frozen=True)
class CostParams:
    """交易成本参数。默认取自 `config_defaults.DEFAULT_BACKTEST_DEFAULTS`。

    ⚠️ 默认值**不在此处另写字面量**——直接引用 config，否则回测与因子验证会各持一套。
    """

    commission_rate: float = DEFAULT_BACKTEST_DEFAULTS.commission_rate
    stamp_tax_rate: float = DEFAULT_BACKTEST_DEFAULTS.stamp_tax_rate
    slippage_rate: float = DEFAULT_BACKTEST_DEFAULTS.slippage_rate

    @property
    def round_trip_rate(self) -> float:
        """换掉一个持仓的往返成本率 = 卖出(c+st+sl) + 买入(c+sl) = 2c + st + 2sl。"""
        return 2 * self.commission_rate + self.stamp_tax_rate + 2 * self.slippage_rate


ROUND_TRIP_RATE: float = CostParams().round_trip_rate


def compute_cost_drag(
    prev_factor_values: dict[str, dict[str, pd.Series]],
    curr_factor_values: dict[str, dict[str, pd.Series]],
    *,
    stage: str,
    state: str = "ALL",
    pct: float = _TOP_PCT,
    cost: CostParams | None = None,
    min_xs: int = _DAILY_IC_MIN_XS,
) -> list[PanelStatPoint]:
    """逐 (strategy, factor) 算头部换手带来的成本拖累。

        turnover  = |前一日头部 − 当日头部| / |前一日头部|
        cost_drag = turnover × (2c + st + 2sl)

    头部选取走 `select_top_pct`，与 K-2 / K-4 **同一实现**——各写一份就会选出不同的
    股票，而这种不一致在数字上完全看不出来。

    Returns:
        `metric='cost_drag'`、`horizon=0`、`bucket=-1` 的点。
        `value` 为正数量级（应从收益中扣减），`sample_size` 是前一日头部只数
        （= 换手率的分母）。任一天取不出头部、或某因子只在一天存在 → **不产出行**。
    """
    params = cost or CostParams()
    rate = params.round_trip_rate
    out: list[PanelStatPoint] = []

    for strategy, curr_factors in curr_factor_values.items():
        prev_factors = prev_factor_values.get(strategy)
        if not prev_factors:
            continue
        for factor, curr_series in curr_factors.items():
            prev_series = prev_factors.get(factor)
            if prev_series is None:
                continue                      # 只在一天存在 → 无从比较（同 K-4）

            head_prev = select_top_pct(prev_series, pct=pct, min_xs=min_xs)
            head_curr = select_top_pct(curr_series, pct=pct, min_xs=min_xs)
            if head_prev is None or head_curr is None:
                continue

            a, b = set(head_prev), set(head_curr)
            # 换手率的分母是**前一日持有的头部**（被换掉的占比），不是并集。
            turnover = len(a - b) / len(a)
            out.append(PanelStatPoint(
                strategy=str(strategy), factor=str(factor), stage=stage,
                state=state, horizon=_COST_HORIZON, metric="cost_drag",
                value=turnover * rate, sample_size=len(a),
            ))
    return out
