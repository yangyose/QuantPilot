"""σ（波动率）与 β 的公共纯函数。Engine 层，严格无 IO。

## 为什么单独一个模块

C1-2 的风险调整动量与 C3 低波动策略都要算 σ60。**各算一遍必然漂移**——
而「两处 σ 定义不一致」在数字上看不出来（都是"看起来正常的波动率"），
只会让 composite 里两个策略对同一只股票用不同的风险度量。
`momentum._rolling_sigma` 现为本模块的别名，`tests/unit/test_volatility.py`
以**函数对象同一性**钉死（只比数值的话，两份代码此刻恰好一致，测不出副本）。

## 口径

- σ：近 `window` 个交易日**对数收益率**的标准差，**不年化**。横截面 rank 与
  Z-score 对正的常数缩放不变，年化只增计算不增信息；理由文本里才乘 √252 展示。
- β：`cov(r_i, r_m) / var(r_m)`，对基准指数的对齐收益回归。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "BETA_MIN_VALID_SAMPLES",
    "SIGMA_MIN_VALID_RATIO",
    "rolling_beta",
    "rolling_sigma",
]

# 有效收益数 < window × 该比例 → σ 记 NaN。样本不足时 σ 不可靠，
# 而它在分母上，会把噪声放大成一个很大的"高分"。
SIGMA_MIN_VALID_RATIO = 0.7

# β 的最低有效对齐样本数（设计 §5.1）。低于此值 → NaN。
BETA_MIN_VALID_SAMPLES = 80


def rolling_sigma(adj_prices: pd.DataFrame, window: int) -> pd.Series:
    """近 ``window`` 个交易日对数收益率的标准差，不年化。

    有效收益数 < ``window × SIGMA_MIN_VALID_RATIO`` 的标的记 NaN。
    """
    if adj_prices.shape[1] < 2:
        return pd.Series(float("nan"), index=adj_prices.index)

    prices = adj_prices.astype(float)
    # 非正价格取对数会得到 -inf/NaN；先置 NaN，由下面的有效样本数判定兜底
    prices = prices.where(prices > 0)
    log_ret = np.log(prices).diff(axis=1).iloc[:, -window:]

    sigma = log_ret.std(axis=1, skipna=True)
    min_valid = window * SIGMA_MIN_VALID_RATIO
    return sigma.where(log_ret.notna().sum(axis=1) >= min_valid)


def _log_returns(px: pd.DataFrame, window: int) -> pd.DataFrame:
    prices = px.astype(float).where(lambda d: d > 0)
    return np.log(prices).diff(axis=1).iloc[:, -window:]


def rolling_beta(
    adj_prices: pd.DataFrame,
    index_adj_prices: pd.DataFrame,
    window: int,
    benchmark: str | None = None,
) -> pd.Series:
    """对基准指数的 ``window`` 交易日 Beta：``cov(r_i, r_m) / var(r_m)``。

    Args:
        adj_prices: index=ts_code，columns=trade_date（Wide）。
        index_adj_prices: index=index_code，columns=trade_date（同结构）。
            为空 / 找不到基准 → 全 NaN（**不抛**：Engine 层降级要可见但不致命）。
        window: 交易日窗口。
        benchmark: 指数代码；None 取 `index_adj_prices` 第一行。

    ⚠️ **`var(r_m) == 0` 返回 NaN 而非 inf**：inf 会一路穿过 Winsorize / Z-score
    变成极端分，比缺失更危险——缺失至少会让该标的不参与本策略。

    有效对齐样本 < ``BETA_MIN_VALID_SAMPLES`` → NaN。
    """
    idx = adj_prices.index
    nan = pd.Series(float("nan"), index=idx, dtype=float)
    if adj_prices.shape[1] < 2 or index_adj_prices.empty:
        return nan

    if benchmark is not None and benchmark in index_adj_prices.index:
        bench_row = index_adj_prices.loc[[benchmark]]
    else:
        bench_row = index_adj_prices.iloc[[0]]

    stock_ret = _log_returns(adj_prices, window)
    bench_ret = _log_returns(bench_row, window)
    # 按列（交易日）对齐——两者的日期集合可能不同
    common = stock_ret.columns.intersection(bench_ret.columns)
    if len(common) < BETA_MIN_VALID_SAMPLES:
        return nan
    sr = stock_ret[common]
    br = bench_ret[common].iloc[0]

    valid = sr.notna() & br.notna()
    n = valid.sum(axis=1)

    br_b = pd.DataFrame(
        np.tile(br.to_numpy(), (len(idx), 1)), index=idx, columns=common
    ).where(valid)
    sr_v = sr.where(valid)

    br_mean = br_b.mean(axis=1)
    sr_mean = sr_v.mean(axis=1)
    cov = ((sr_v.sub(sr_mean, axis=0)) * (br_b.sub(br_mean, axis=0))).sum(axis=1)
    var = ((br_b.sub(br_mean, axis=0)) ** 2).sum(axis=1)

    beta = cov / var.where(var > 0)          # var==0 → NaN，不是 inf
    beta = beta.replace([np.inf, -np.inf], np.nan)
    return beta.where(n >= BETA_MIN_VALID_SAMPLES)
