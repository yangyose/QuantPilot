"""HAC（Newey-West）方差估计 + 非重叠子采样 + 配对检验。V1.5-K K-1。

Engine 层纯函数，严格无 IO。

## 为什么需要它

日级 Rank IC 用 20 交易日前向窗口、逐日计算 → 相邻日的收益区间共享 19/20，
**观测高度重叠**。朴素 t 检验假设独立同分布，在这种序列上会**系统性高估显著性**。

C1 面板实测（`v1_5_c §3.3`，全程 25/25 收敛值）：

| 状态 | t_朴素 | t_HAC20 |
|---|---|---|
| OSCILLATION（占 65% 天数）| 0.65 | 0.19 |
| UPTREND | 4.91 | 1.72 |
| DOWNTREND | 6.83 | 2.32 |

只看朴素 t 会把 UPTREND / DOWNTREND 双双误判为「高度显著」。有效样本仅名义的 8~12%。

## ⚠️ 这个模块自己错了，没有更上层的尺子能发现

它产出的是「用来判断别的东西对不对」的度量——因子有没有效最终就靠这些数字回答，
数字错了会一路传导到实盘选股。故 `tests/unit/test_hac.py` 的门槛是
**对照解析解**（`x_t = c + (-1)^t` 有闭式 se = 1/n），不是「不抛异常」。
改本模块任何一行前先读那个文件的 docstring。

## 退化约定

无法计算时一律返回 **NaN，不返回 0**——0 会被下游当成「算出来就是 0」，
而零方差意味着无穷大的 t，与「算不出来」是两回事。
与 `compute_daily_ic` 的「跳过不写占位行」同一取向。
调用方传参错误（负 lag / 长度不等）则**抛 ValueError**，不静默吞掉。
"""
from __future__ import annotations

import numpy as np

__all__ = [
    "newey_west_var_of_mean",
    "hac_tstat",
    "effective_sample_ratio",
    "nonoverlapping_subsample",
    "paired_hac_tstat",
]


def _clean(x: np.ndarray) -> np.ndarray:
    """转 float64 一维并剔除 NaN。

    **剔除而非填 0**：填 0 会把均值拉向 0，并在缺口处伪造出自相关结构。
    """
    a = np.asarray(x, dtype=float).ravel()
    return a[np.isfinite(a)]


def newey_west_var_of_mean(x: np.ndarray, lag: int) -> float:
    """样本均值的 Newey-West（Bartlett 核）HAC 方差估计。

        S = γ₀ + 2 · Σ_{j=1..L} w_j · γ_j ,   w_j = 1 - j/(L+1)
        Var(x̄) = S / n

    ⚠️ Bartlett 权分母是 **L+1** 不是 L：j=L 处权为 1/(L+1) 而非 0，
    写成 `1 - j/L` 会让最远一阶自协方差整个消失（有专门用例钉死）。

    Args:
        x: 一维序列，NaN 会被剔除。
        lag: 截断阶数 L。`lag=0` 退化为普通方差（解析恒等式）。

    Returns:
        方差估计；样本不足 / 零方差 / lag ≥ n 时返回 NaN。

    Raises:
        ValueError: lag < 0（调用方错误，不静默当 0）。
    """
    if lag < 0:
        raise ValueError(f"lag 必须 ≥ 0，实得 {lag}")

    a = _clean(x)
    n = a.size
    if n < 2 or lag >= n:
        return float("nan")

    d = a - a.mean()
    g0 = float(d @ d) / n
    if g0 <= 0.0:
        return float("nan")          # 零方差 ≠ 方差为 0 的有效估计

    s = g0
    for j in range(1, lag + 1):
        gj = float(d[j:] @ d[:-j]) / n
        s += 2.0 * (1.0 - j / (lag + 1.0)) * gj

    if s <= 0.0:
        # 截断核不保证半正定为正：负值说明该 lag 下估计不可用，如实返回 NaN
        return float("nan")
    return s / n


def hac_tstat(x: np.ndarray, lag: int) -> float:
    """均值的 HAC t 统计量：mean / sqrt(Var_NW(mean))。"""
    a = _clean(x)
    v = newey_west_var_of_mean(a, lag)
    if not np.isfinite(v) or v <= 0.0:
        return float("nan")
    return float(a.mean() / np.sqrt(v))


def effective_sample_ratio(x: np.ndarray, lag: int) -> float:
    """有效样本比 = (t_HAC / t_朴素)²，等价于 Var_朴素 / Var_HAC。

    面板里那列「有效样本比 0.08~0.12」就是它：重叠窗口下 n 个观测里
    真正独立的信息量只相当于 0.08n~0.12n 个。

    Returns:
        比值；任一方无法估计时 NaN。
    """
    a = _clean(x)
    v_hac = newey_west_var_of_mean(a, lag)
    v_naive = newey_west_var_of_mean(a, 0)
    if not (np.isfinite(v_hac) and np.isfinite(v_naive)) or v_hac <= 0.0:
        return float("nan")
    return float(v_naive / v_hac)


def nonoverlapping_subsample(x: np.ndarray, step: int) -> np.ndarray:
    """每 `step` 个取一个，得到互不重叠的子样本。

    用途：与 HAC **互为佐证**——HAC 是「保留全部观测、修正方差」，
    子采样是「丢弃观测、换取真正的独立性」。两者结论一致才可信；
    只用其中一个无法区分「效应真实」与「修正方法不适配」。

    Raises:
        ValueError: step < 1。
    """
    if step < 1:
        raise ValueError(f"step 必须 ≥ 1，实得 {step}")
    return np.asarray(x, dtype=float).ravel()[::step]


def paired_hac_tstat(a: np.ndarray, b: np.ndarray, lag: int) -> float:
    """配对 HAC 检验：对**差值序列** `b - a` 求 HAC t。

    ⚠️ 必须配对，不能两组各自求 t 再比——off/on 两组面板跑在**同一批交易日**上，
    共同的市场因素占方差的绝大部分，配对能把它消掉。两样本检验会因这部分
    共同方差而严重欠功效。

    差值恒为常数（零方差）时返回 NaN——那意味着两组只差一个平移，
    没有可供检验的波动。

    Raises:
        ValueError: 两序列长度不等。
    """
    x = np.asarray(a, dtype=float).ravel()
    y = np.asarray(b, dtype=float).ravel()
    if x.size != y.size:
        raise ValueError(f"配对检验要求等长，实得 {x.size} vs {y.size}")
    return hac_tstat(y - x, lag)
