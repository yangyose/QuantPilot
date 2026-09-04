"""换手代理：top 5% 集合的日间 Jaccard。V1.5-K K-4。Engine 层纯函数，严格无 IO。

## 为什么要它

K-2 量的是「头部有没有超额」，但一个头部**每天换一批**的因子，即便超额为正也可能
被交易成本吃光。K-4 用相邻交易日头部集合的 Jaccard 相似度做换手代理，
它同时是 K-5（成本拖累）的输入。

## ⚠️ 方向：存的是**相似度**，不是换手率

`turnover_jaccard` 这个名字两头都读得通，故此处写死：

    J = |A ∩ B| / |A ∪ B|    ——  **两天头部完全相同 → 1.0**（不是 0.0）

换手率 = `1 − J` 是平凡变换，留给分析侧。存原始量是为了避免「已经转换过一次」与
「还没转换」在下游被搞混——那类错误不报错，只会让成本估算差一个符号。
`tests/unit/test_factor_turnover.py::TestJaccardDirection` 钉死这个方向。

## 头部选取与 K-2 同源

复用 `factor_portfolio.select_top_pct`。各写一份就可能选出不同的股票，于是
「测了超额的那个头部」与「测了换手的那个头部」变成两回事，
**而这种不一致在数字上完全看不出来**。

## 退化约定

按设计文档 §3 门槛 3：退化跳过不写占位行。其中一条特别要紧——
**某因子只在一天存在时必须跳过，不能把缺失的一天当空集算 J=0**，
否则「新上线的因子」会伪装成「换手率 100% 的坏因子」。
"""
from __future__ import annotations

import pandas as pd

from quantpilot.engine.diagnostics.factor_ic import PanelStatPoint
from quantpilot.engine.diagnostics.factor_portfolio import _TOP_PCT, select_top_pct
from quantpilot.engine.diagnostics.ic_aggregator import _DAILY_IC_MIN_XS

__all__ = ["compute_turnover_jaccard"]

_TURNOVER_HORIZON = 0          # 换手无前向概念（与 valid_ratio 同约定）


def compute_turnover_jaccard(
    prev_factor_values: dict[str, dict[str, pd.Series]],
    curr_factor_values: dict[str, dict[str, pd.Series]],
    *,
    stage: str,
    state: str = "ALL",
    pct: float = _TOP_PCT,
    min_xs: int = _DAILY_IC_MIN_XS,
) -> list[PanelStatPoint]:
    """逐 (strategy, factor) 算相邻两日头部集合的 Jaccard 相似度。

    Args:
        prev_factor_values: 前一交易日的 ``{strategy: {factor: Series}}``。
        curr_factor_values: 当日的同结构。行归属**当日**。
        stage: ``'raw'`` 或 ``'z'``。
        pct: 头部比例，透传给 `select_top_pct`（floor 语义）。
        min_xs: 单日有效数下限。

    Returns:
        `metric='turnover_jaccard'`、`horizon=0`、`bucket=-1` 的点。
        `value` 是**相似度**（相同为 1.0），`sample_size` 是并集大小（Jaccard 的分母）。

        两天中任一天取不出头部、或某因子只在一天存在 → **不产出行**。
    """
    out: list[PanelStatPoint] = []
    for strategy, curr_factors in curr_factor_values.items():
        prev_factors = prev_factor_values.get(strategy)
        if not prev_factors:
            continue
        for factor, curr_series in curr_factors.items():
            prev_series = prev_factors.get(factor)
            if prev_series is None:
                # 只在一天存在 → 无从比较。绝不可当空集算 J=0：
                # 那会让「新上线的因子」伪装成「换手率 100% 的坏因子」。
                continue

            head_prev = select_top_pct(prev_series, pct=pct, min_xs=min_xs)
            head_curr = select_top_pct(curr_series, pct=pct, min_xs=min_xs)
            if head_prev is None or head_curr is None:
                continue

            a, b = set(head_prev), set(head_curr)
            union = a | b
            if not union:
                continue
            out.append(PanelStatPoint(
                strategy=str(strategy), factor=str(factor), stage=stage,
                state=state, horizon=_TURNOVER_HORIZON, metric="turnover_jaccard",
                value=len(a & b) / len(union), sample_size=len(union),
            ))
    return out
