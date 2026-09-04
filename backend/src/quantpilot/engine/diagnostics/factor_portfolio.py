"""组合口径的因子验证指标。V1.5-K K-2。Engine 层纯函数，严格无 IO。

## 为什么 IC 不够

IC 是**横截面相关系数**，回答「因子排序与收益排序有多一致」。但实盘不按相关系数
交易——**只买头部那一小撮**。一个 IC 显著为正的因子，其单调性完全可能全部来自尾部
（做空端），而 A 股不能做空，头部反而毫无超额。K-2 把验证口径对齐到交易口径：

- `decile_fwd_return` —— 十档前向收益阶梯，看单调性**在哪一段**成立
- `top5_excess` —— 头部超额，看真正能交易的那一端有没有钱

## ⚠️ 两条来自既有事故的口径约定

1. **「取前 X%」按名次取，不用 `quantile` 阈值**（CLAUDE.md §4.4）：
   `quantile(1-X)` 在小样本上被线性插值支配，且切点并列时 `>=` 会把整批圈进来，
   头部悄悄膨胀。此处一律 `int(n * pct)`（floor），不足 1 只则**不产出行**——
   退化成「至少取 1 只」等于把 5% 悄悄变成 5.3%~100% 且无任何提示。

2. **无离散度即跳过**：横截面全同值时分档与头部都没有语义。

## 退化约定

按设计文档 §3 门槛 3：退化则**跳过不写占位行**，与 `compute_daily_ic` 一致。
（`valid_ratio` 那条「必须写 0」的例外只适用于 K-6 有效率，不适用于本模块——
本模块的指标为零和「算不出来」是两件事，写占位行反而会被读成「头部无超额」。）
"""
from __future__ import annotations

import pandas as pd

from quantpilot.engine.diagnostics.factor_ic import PanelStatPoint
from quantpilot.engine.diagnostics.ic_aggregator import _DAILY_IC_MIN_XS

__all__ = [
    "compute_decile_forward_return",
    "compute_top_pct_excess",
    "select_top_pct",
]

_N_DECILES = 10
_TOP_PCT = 0.05


def _aligned(factor: pd.Series, forward_returns: pd.Series) -> pd.DataFrame:
    """对齐并剔除 NaN，按 (因子值, ts_code) 升序排列。

    ⚠️ 二级键 `ts_code` 不是装饰：并列值若只按因子值排序，行序取决于输入顺序，
    分档结果就不可重现，同一批数据两次面板重跑会得到两个答案。
    """
    df = pd.DataFrame({"f": factor, "r": forward_returns}).dropna()
    if df.empty:
        return df
    df = df.assign(_code=df.index.astype(str)).sort_values(
        ["f", "_code"], kind="mergesort"
    )
    return df.drop(columns="_code")


def select_top_pct(
    factor: pd.Series, *, pct: float = _TOP_PCT, min_xs: int | None = None
) -> list[str] | None:
    """按名次取因子值最高的 `int(n * pct)`（floor）只，返回 ts_code 列表。

    **K-2 头部超额与 K-4 换手 Jaccard 共用本函数**——各写一份就可能选出不同的股票，
    于是「测了超额的那个头部」和「测了换手的那个头部」变成两回事，
    而这种不一致在数字上完全看不出来。

    ⚠️ 不用 `quantile(1-pct)` 阈值（CLAUDE.md §4.4）：小样本被线性插值支配，
    且切点并列时 `>=` 会把并列的整批圈进来让头部膨胀。

    Returns:
        ts_code 列表（按因子值升序，末尾为最高）；下列情形返回 **None**：
        NaN 剔除后为空 / 不足 `min_xs` / 横截面无离散度 / `int(n*pct) < 1`。
        None 与「空列表」不同——前者是「取不出头部」，后者会被误读为「头部为空」。
    """
    s = pd.Series(factor).dropna()
    n = len(s)
    if n == 0:
        return None
    if min_xs is not None and n < min_xs:
        return None
    if s.nunique() <= 1:
        return None
    n_head = int(n * pct)          # floor
    if n_head < 1:
        return None
    ordered = s.to_frame("f").assign(_code=lambda d: d.index.astype(str)).sort_values(
        ["f", "_code"], kind="mergesort"
    )
    return [str(c) for c in ordered.index[-n_head:]]


def compute_decile_forward_return(
    factor_values: dict[str, dict[str, pd.Series]],
    forward_returns: pd.Series,
    *,
    stage: str,
    horizon: int,
    state: str = "ALL",
    min_xs: int = _DAILY_IC_MIN_XS,
) -> list[PanelStatPoint]:
    """逐 (strategy, factor) 算十档前向收益阶梯。

    **口径固定：`bucket=1` 是因子值最低档，`bucket=10` 最高。**
    刻意不假设「因子值越高越好」——raw 因子方向各不相同，方向判断留给分析侧。
    反过来定义同样自洽，但必须二选一并写死，否则跨因子/跨批次结果不可比。

    档位大小按名次均分，n 不被 10 整除时各档最多相差 1 只（余数分给靠前的档）。

    Returns:
        `metric='decile_fwd_return'` 的 10 个点；退化则**不产出行**。
        每点的 `sample_size` 是该档只数。
    """
    out: list[PanelStatPoint] = []
    for strategy, factors in factor_values.items():
        for factor, series in factors.items():
            df = _aligned(series, forward_returns)
            n = len(df)
            if n < min_xs or n < _N_DECILES:
                continue
            if df["f"].nunique() <= 1:
                continue                      # 无离散度：分档无语义

            # 按名次切成 10 段（余数分给靠前的档），不依赖 qcut——qcut 在大量并列时
            # 会产出不等长甚至空档，且边界行为随 pandas 版本变化。
            base, rem = divmod(n, _N_DECILES)
            start = 0
            for k in range(1, _N_DECILES + 1):
                size = base + (1 if k <= rem else 0)
                chunk = df.iloc[start : start + size]
                start += size
                out.append(PanelStatPoint(
                    strategy=str(strategy), factor=str(factor), stage=stage,
                    state=state, horizon=int(horizon), metric="decile_fwd_return",
                    value=float(chunk["r"].mean()), sample_size=int(len(chunk)),
                    bucket=k,
                ))
    return out


def compute_top_pct_excess(
    factor_values: dict[str, dict[str, pd.Series]],
    forward_returns: pd.Series,
    *,
    stage: str,
    horizon: int,
    state: str = "ALL",
    pct: float = _TOP_PCT,
    min_xs: int = _DAILY_IC_MIN_XS,
) -> list[PanelStatPoint]:
    """逐 (strategy, factor) 算头部超额 = 头部平均前向收益 − 全体平均。

    ⚠️ 头部只数 = `int(n * pct)`（**floor**），按名次取尾部 n_head 行。
    不用 `quantile(1-pct)` 阈值（CLAUDE.md §4.4）：小样本被线性插值支配，
    且切点并列时 `>=` 会把并列的整批都圈进来——本函数有专门用例钉死
    「20 只并列最高时头部仍恰为 5 只」。

    `n_head == 0`（n < 1/pct）时**不产出行**：退化成「至少取 1 只」会把 5%
    悄悄变成更大的比例且无提示。

    Returns:
        `metric='top5_excess'`、`bucket=-1` 的点；`sample_size` 是头部只数。
    """
    out: list[PanelStatPoint] = []
    for strategy, factors in factor_values.items():
        for factor, series in factors.items():
            df = _aligned(series, forward_returns)
            # 头部选取走共用的 select_top_pct——K-4 换手用的是同一份，
            # 各写一份就会选出不同的股票，而那种不一致在数字上看不出来。
            head = select_top_pct(df["f"], pct=pct, min_xs=min_xs)
            if head is None:
                continue
            head_mean = float(df["r"].reindex(head).mean())
            all_mean = float(df["r"].mean())
            out.append(PanelStatPoint(
                strategy=str(strategy), factor=str(factor), stage=stage,
                state=state, horizon=int(horizon), metric="top5_excess",
                value=head_mean - all_mean, sample_size=len(head),
            ))
    return out
