"""K-1：HAC（Newey-West）+ 非重叠子采样 + 配对检验 的单元测试。

⚠️ 本模块产出的是「**用来判断别的东西对不对**」的度量——它自己错了，
没有更上层的尺子能发现（设计文档 v1_5_k §3）。故四条硬门槛：

1. **对照解析解**——不是「不抛异常」。本文件用一个能**手算出闭式**的序列：
   `x_t = c + (-1)^t`（n 为偶数）。均值 c，去均值后是 ±1 交替。
       γ₀ = 1
       γ₁ = -(n-1)/n
       Bartlett 权 w_j = 1 - j/(L+1)
       L=1 → S = γ₀ + 2·(1/2)·γ₁ = 1 - (n-1)/n = 1/n
       Var(x̄) = S/n = 1/n²  → se = 1/n
   n=100, c=0.05 时 **se 恰为 0.01、t 恰为 5.0**，可整数验算。
2. **改输入 → 结果必须变**：同一序列 L=0 得 t=0.5、L=1 得 t=5.0，相差 10 倍。
   `lag` 若传错名被静默忽略（pandas_ta `bbands(std=)` 的同构风险），这条立刻红。
3. **退化输入显式定义并断言**：全 NaN / n<2 / 零方差 / lag≥n。
4. **⛔ 禁止自证**：不用本模块的产出验证本模块——上面的期望值全部来自手算，
   不来自任何一次「先跑一遍看看输出多少」。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from quantpilot.engine.diagnostics.hac import (
    effective_sample_ratio,
    hac_tstat,
    newey_west_var_of_mean,
    nonoverlapping_subsample,
    paired_hac_tstat,
)


def _alternating(n: int = 100, c: float = 0.05) -> np.ndarray:
    """x_t = c + (-1)^t，n 偶数。去均值后为 ±1 交替，自协方差可手算。"""
    assert n % 2 == 0
    return c + np.array([1.0 if i % 2 == 0 else -1.0 for i in range(n)])


# ───────────────────── 门槛 1：对照解析解 ─────────────────────
class TestAnalytic:
    def test_lag0_equals_ordinary_variance(self) -> None:
        """L=0 时 Bartlett 和只剩 γ₀ → 退化为普通（朴素）方差。

        这是**解析恒等式**，不是把实现抄一遍：γ₀ 就是总体方差（除以 n）。
        """
        x = _alternating(100, c=0.05)
        # 手算：去均值后每项 ±1 → γ₀ = 1 → Var(x̄) = 1/100
        assert newey_west_var_of_mean(x, lag=0) == pytest.approx(1.0 / 100, rel=1e-12)
        assert hac_tstat(x, lag=0) == pytest.approx(0.5, rel=1e-12)

    def test_lag1_matches_closed_form(self) -> None:
        """L=1 闭式：S = 1 - (n-1)/n = 1/n → Var(x̄) = 1/n² → se = 1/n。"""
        n = 100
        x = _alternating(n, c=0.05)
        assert newey_west_var_of_mean(x, lag=1) == pytest.approx(1.0 / n**2, rel=1e-12)
        # t = c / se = 0.05 / 0.01 = 5.0（整数可验算）
        assert hac_tstat(x, lag=1) == pytest.approx(5.0, rel=1e-12)

    def test_bartlett_weight_at_truncation_is_not_zero(self) -> None:
        """Bartlett 权在 j=L 处为 1/(L+1)，**不是 0**——写成 1-j/L 会让最远一阶消失。

        n=100 / L=2：w₁=2/3, w₂=1/3，γ₂ = (n-2)/n（+1，同号）
        S = 1 + 2·(2/3)·(-(99/100)) + 2·(1/3)·(98/100)
          = 1 - 1.32 + 0.653333... = 0.333333...
        """
        n = 100
        x = _alternating(n, c=0.05)
        g0, g1, g2 = 1.0, -(n - 1) / n, (n - 2) / n
        expected_s = g0 + 2 * (2 / 3) * g1 + 2 * (1 / 3) * g2
        assert newey_west_var_of_mean(x, lag=2) == pytest.approx(expected_s / n, rel=1e-12)

    def test_iid_like_series_hac_close_to_naive(self) -> None:
        """无自相关时 HAC 与朴素应接近（不等同——有限样本 γ_j 非零）。"""
        rng = np.random.default_rng(20260903)
        x = rng.standard_normal(5000)
        naive = newey_west_var_of_mean(x, lag=0)
        hac = newey_west_var_of_mean(x, lag=20)
        assert hac == pytest.approx(naive, rel=0.25)


# ───────────────────── 门槛 2：改输入 → 结果必须变 ─────────────────────
class TestParameterIsConsumed:
    def test_lag_changes_result(self) -> None:
        """lag 若被静默忽略，这条立刻红（§4.11「参数是否真被消费」）。

        ⚠️ **不能用 `_alternating`**：±1 交替是周期序列，奇数 lag 的 Bartlett
        加权和恰好相等（实测 L=1/3/5 均得 t=5.0）——数学上正确，但会让这条
        「改参数必变」的判据在一半取值上假绿。改用 AR(1)，无此对称性。
        """
        rng = np.random.default_rng(3)
        n, rho = 800, 0.6
        e = rng.standard_normal(n)
        x = np.empty(n)
        x[0] = e[0]
        for i in range(1, n):
            x[i] = rho * x[i - 1] + e[i]
        x = x + 0.5
        vals = [hac_tstat(x, lag=L) for L in (0, 1, 2, 5, 20)]
        assert all(np.isfinite(vals)), f"AR(1) 上不应出现 NaN：{vals}"
        assert len(set(round(v, 10) for v in vals)) == len(vals), f"lag 未被消费：{vals}"
        # AR(1) 正自相关 → lag 越大方差估计越大 → |t| 单调下降
        assert vals[0] > vals[-1], f"正自相关下 HAC 应压低 t，实得 {vals}"

    def test_overlap_degree_changes_effective_sample(self) -> None:
        """重叠度越高 → 有效样本比越低。用 20 期滚动和模拟前向窗口重叠。"""
        rng = np.random.default_rng(7)
        base = rng.standard_normal(2000)
        light = np.convolve(base, np.ones(2), mode="valid")   # 重叠 1/2
        heavy = np.convolve(base, np.ones(20), mode="valid")  # 重叠 19/20
        r_light = effective_sample_ratio(light, lag=20)
        r_heavy = effective_sample_ratio(heavy, lag=20)
        assert r_heavy < r_light, f"重叠更重反而有效样本更多：{r_heavy} vs {r_light}"


# ───────────────────── 门槛 3：退化输入显式定义 ─────────────────────
class TestDegenerate:
    @pytest.mark.parametrize(
        "arr",
        [
            np.array([]),
            np.array([1.0]),
            np.array([np.nan, np.nan, np.nan]),
            np.array([2.0, 2.0, 2.0, 2.0]),          # 零方差
        ],
    )
    def test_degenerate_returns_nan_not_zero(self, arr: np.ndarray) -> None:
        """约定：无法计算时返回 NaN，**不返回 0**。

        0 会被下游当成「算出来就是 0」——而 0 方差意味着无穷大的 t，
        与「算不出来」是两回事。同 `compute_daily_ic` 的「跳过不写占位行」一致。
        """
        assert math.isnan(newey_west_var_of_mean(arr, lag=2))
        assert math.isnan(hac_tstat(arr, lag=2))

    def test_nan_values_are_dropped_not_zero_filled(self) -> None:
        """NaN 必须被剔除而非填 0——填 0 会把均值拉向 0 并伪造自相关。"""
        x = _alternating(100, c=0.05)
        y = x.copy()
        y[[3, 17, 42]] = np.nan
        got = hac_tstat(y, lag=1)
        assert not math.isnan(got)
        # 与「把 NaN 填 0」的结果必须不同
        z = x.copy()
        z[[3, 17, 42]] = 0.0
        assert got != pytest.approx(hac_tstat(z, lag=1), rel=1e-6)

    def test_lag_at_least_n_is_rejected(self) -> None:
        """lag ≥ 有效样本数时无法估计 → NaN + 不崩。"""
        assert math.isnan(newey_west_var_of_mean(np.array([1.0, 2.0, 3.0]), lag=3))

    def test_negative_lag_raises(self) -> None:
        """负 lag 是调用方错误，**必须抛**而不是静默当 0——静默会掩盖传参 bug。"""
        with pytest.raises(ValueError):
            newey_west_var_of_mean(_alternating(10), lag=-1)


# ───────────────────── 非重叠子采样 + 配对检验 ─────────────────────
class TestSubsampleAndPaired:
    def test_nonoverlapping_takes_every_kth(self) -> None:
        x = np.arange(10, dtype=float)
        got = nonoverlapping_subsample(x, step=3)
        assert list(got) == [0.0, 3.0, 6.0, 9.0]

    def test_nonoverlapping_step_one_is_identity(self) -> None:
        x = np.arange(5, dtype=float)
        assert list(nonoverlapping_subsample(x, step=1)) == list(x)

    def test_nonoverlapping_rejects_bad_step(self) -> None:
        with pytest.raises(ValueError):
            nonoverlapping_subsample(np.arange(5, dtype=float), step=0)

    def test_paired_uses_differences_not_two_sample(self) -> None:
        """配对检验必须对**差值**做，不是两组各自求 t 再比。

        构造成两者能被区分：两组共享一个**大方差的共同成分**（模拟 off/on 面板
        跑在同一批交易日上、共同市场因素占方差绝大部分），各自只叠一点小噪声，
        b 比 a 稳定高 0.2。
          · 配对 → 共同成分被差掉，0.2 相对小噪声极显著
          · 两样本 → 被共同的大方差淹没，几乎测不出
        两样本 t 用标准公式当场独立算出（不调用本模块，故非自证）。
        """
        rng = np.random.default_rng(11)
        common = rng.standard_normal(500) * 10.0
        a = common + rng.standard_normal(500) * 0.05
        b = common + rng.standard_normal(500) * 0.05 + 0.2

        t_paired = paired_hac_tstat(a, b, lag=5)
        t_two_sample = (b.mean() - a.mean()) / np.sqrt(
            a.var(ddof=1) / a.size + b.var(ddof=1) / b.size
        )
        assert t_paired > 20 * abs(t_two_sample), (
            f"配对 t={t_paired:.2f} 未显著强于两样本 t={t_two_sample:.2f}——"
            "疑似没对差值做，而是两组各自求 t"
        )

    def test_paired_constant_difference_is_degenerate(self) -> None:
        """差值恒为常数（零方差）→ NaN：没有可供检验的波动。

        ⚠️ 用整数构造以避开浮点残差——`a + 0.3 - a` 因舍入并非精确 0.3，
        方差约 1e-16 而非 0，会得到 t≈1e16 而不是 NaN（首版即栽在这）。
        """
        a = np.arange(200, dtype=float)
        b = a + 5.0
        assert math.isnan(paired_hac_tstat(a, b, lag=5))

    def test_paired_detects_real_difference(self) -> None:
        rng = np.random.default_rng(12)
        a = rng.standard_normal(500)
        b = a + rng.standard_normal(500) * 0.1 + 0.2
        t = paired_hac_tstat(a, b, lag=5)
        assert t > 3.0, f"0.2 的稳定差异应显著，实得 t={t}"

    def test_paired_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError):
            paired_hac_tstat(np.arange(5, dtype=float), np.arange(6, dtype=float), lag=1)
