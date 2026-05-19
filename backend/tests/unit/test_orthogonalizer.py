"""Orthogonalizer 单元测试（Phase 11 §3.2）。

覆盖：Gram-Schmidt 4 维退化 / 完全共线检测 / renormalize Var≈1 /
order 改变结果差异 / NaN 透传 / 单行退化。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantpilot.engine.orthogonalizer import (
    DEFAULT_ORTHOGONALIZER,
    Orthogonalizer,
)


# ============================================================
# Step 4a：Gram-Schmidt
# ============================================================
class TestGramSchmidt:
    def setup_method(self) -> None:
        self.orth = Orthogonalizer(DEFAULT_ORTHOGONALIZER)

    def _mk_correlated_matrix(self, n: int = 500, rho: float = 0.6) -> pd.DataFrame:
        """构造 4 列相关矩阵：trend 与 momentum 高度相关（ρ≈0.6），其它独立。"""
        rng = np.random.default_rng(42)
        trend = rng.normal(0, 1, n)
        # momentum = ρ × trend + sqrt(1-ρ²) × 独立噪声 → 与 trend 相关 ρ
        momentum = rho * trend + np.sqrt(1 - rho**2) * rng.normal(0, 1, n)
        mean_reversion = rng.normal(0, 1, n)
        value = rng.normal(0, 1, n)
        data = {
            "trend": trend,
            "momentum": momentum,
            "mean_reversion": mean_reversion,
            "value": value,
        }
        return pd.DataFrame(data, index=[f"S{i:04d}" for i in range(n)])

    def test_first_column_unchanged(self) -> None:
        matrix = self._mk_correlated_matrix(rho=0.6)
        order = ["trend", "momentum", "mean_reversion", "value"]
        result = self.orth.gram_schmidt(matrix, order)
        # 第一个策略（trend）不剔除任何投影 → trend_orthogonal == trend
        np.testing.assert_allclose(result["trend_orthogonal"].to_numpy(),
                                    matrix["trend"].to_numpy(), rtol=1e-10)

    def test_second_column_orthogonal_to_first(self) -> None:
        matrix = self._mk_correlated_matrix(rho=0.6)
        order = ["trend", "momentum", "mean_reversion", "value"]
        result = self.orth.gram_schmidt(matrix, order)
        # momentum_orthogonal 应与 trend_orthogonal 内积 ≈ 0
        dot = float((result["trend_orthogonal"] * result["momentum_orthogonal"]).sum())
        assert abs(dot) < 1e-6

    def test_all_columns_pairwise_orthogonal(self) -> None:
        matrix = self._mk_correlated_matrix(rho=0.6)
        order = ["trend", "momentum", "mean_reversion", "value"]
        result = self.orth.gram_schmidt(matrix, order)
        cols = list(result.columns)
        for i, c1 in enumerate(cols):
            for c2 in cols[i + 1:]:
                dot = float((result[c1] * result[c2]).sum())
                assert abs(dot) < 1e-6, f"{c1} ⋅ {c2} = {dot}, expected ≈ 0"

    def test_variance_decreases_with_correlation(self) -> None:
        """4b 之前的残差 Var(u_i) = 1 - Σρ²，应严格 < 1 当存在相关时。"""
        matrix = self._mk_correlated_matrix(rho=0.6)
        order = ["trend", "momentum", "mean_reversion", "value"]
        result = self.orth.gram_schmidt(matrix, order)
        # trend 不投影 → 方差 ≈ 1（输入 z-score 后）
        # momentum 投影掉 0.6×trend → 方差应 ≈ 1 - 0.36 = 0.64
        var_momentum = float(result["momentum_orthogonal"].var(ddof=0))
        assert var_momentum < 0.85, f"Var(momentum_orth) = {var_momentum}, expected < 0.85"

    def test_order_changes_result(self) -> None:
        """改变 order 顺序 → 残差列 contents 不同（顺序依赖）。"""
        matrix = self._mk_correlated_matrix(rho=0.6)
        order_a = ["trend", "momentum", "value", "mean_reversion"]
        order_b = ["momentum", "trend", "value", "mean_reversion"]
        result_a = self.orth.gram_schmidt(matrix, order_a)
        result_b = self.orth.gram_schmidt(matrix, order_b)
        # A 顺序下 trend 第一不剔除；B 顺序下 trend 剔除 momentum 投影
        diff_series = (result_a["trend_orthogonal"] - result_b["trend_orthogonal"]).abs()
        assert float(diff_series.mean()) > 0.01

    def test_perfect_collinearity_writes_nan(self) -> None:
        """完全共线列 → 残差全 0 → 写 NaN（下游权重置 0）。"""
        rng = np.random.default_rng(42)
        x1 = rng.normal(0, 1, 200)
        matrix = pd.DataFrame(
            {"a": x1, "b": x1 * 2.0, "c": rng.normal(0, 1, 200)},  # b 与 a 完全共线
            index=[f"S{i}" for i in range(200)],
        )
        result = self.orth.gram_schmidt(matrix, ["a", "b", "c"])
        # b_orthogonal 应全 NaN（残差全 0 / std<1e-12 → 检测为共线）
        assert result["b_orthogonal"].isna().all()

    def test_nan_row_propagates(self) -> None:
        """单行包含 NaN → 该行所有输出列 NaN。"""
        rng = np.random.default_rng(42)
        matrix = pd.DataFrame(
            {
                "a": rng.normal(0, 1, 100),
                "b": rng.normal(0, 1, 100),
            },
            index=[f"S{i:03d}" for i in range(100)],
        )
        matrix.loc["S050", "a"] = np.nan
        result = self.orth.gram_schmidt(matrix, ["a", "b"])
        # S050 行全 NaN
        assert pd.isna(result.loc["S050", "a_orthogonal"])
        assert pd.isna(result.loc["S050", "b_orthogonal"])
        # 其它行应有值
        assert result["a_orthogonal"].notna().sum() == 99

    def test_single_row_degenerate(self) -> None:
        matrix = pd.DataFrame({"a": [1.0], "b": [2.0]}, index=["S001"])
        result = self.orth.gram_schmidt(matrix, ["a", "b"])
        # 单行无法做投影 → 退化（列名重命名）
        assert list(result.columns) == ["a_orthogonal", "b_orthogonal"]
        assert result.loc["S001", "a_orthogonal"] == 1.0
        assert result.loc["S001", "b_orthogonal"] == 2.0

    def test_empty_order_returns_empty_columns(self) -> None:
        matrix = pd.DataFrame({"a": [1.0, 2.0]}, index=["S1", "S2"])
        result = self.orth.gram_schmidt(matrix, [])
        assert result.empty or len(result.columns) == 0

    def test_order_with_missing_column_raises(self) -> None:
        matrix = pd.DataFrame({"a": [1.0]}, index=["S1"])
        try:
            self.orth.gram_schmidt(matrix, ["a", "nonexistent"])
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "nonexistent" in str(exc)


# ============================================================
# Step 4b：Renormalize
# ============================================================
class TestRenormalize:
    def setup_method(self) -> None:
        self.orth = Orthogonalizer(DEFAULT_ORTHOGONALIZER)

    def test_normalized_mean_0_std_1(self) -> None:
        rng = np.random.default_rng(42)
        n = 500
        # 模拟 Gram-Schmidt 输出：trend 不变（Var≈1），momentum 残差化后 Var≈0.64
        residuals = pd.DataFrame(
            {
                "trend_orthogonal": rng.normal(0, 1, n),
                "momentum_orthogonal": rng.normal(0, 0.8, n),
            },
            index=[f"S{i:04d}" for i in range(n)],
        )
        result = self.orth.renormalize(residuals)
        for col in result.columns:
            assert abs(float(result[col].mean())) < 1e-9
            # 业界 Barra 惯例 ddof=1（样本标准差），renormalize 用此口径
            assert abs(float(result[col].std(ddof=1)) - 1.0) < 1e-6

    def test_column_renamed_orthogonal_to_normalized(self) -> None:
        residuals = pd.DataFrame(
            {"trend_orthogonal": [1.0, 2.0, 3.0], "momentum_orthogonal": [4.0, 5.0, 6.0]},
            index=["a", "b", "c"],
        )
        result = self.orth.renormalize(residuals)
        assert list(result.columns) == ["trend_normalized", "momentum_normalized"]

    def test_all_nan_column_stays_nan(self) -> None:
        residuals = pd.DataFrame(
            {"trend_orthogonal": [np.nan, np.nan, np.nan], "momentum_orthogonal": [1.0, 2.0, 3.0]},
            index=["a", "b", "c"],
        )
        result = self.orth.renormalize(residuals)
        assert result["trend_normalized"].isna().all()
        # momentum 正常标准化
        assert abs(float(result["momentum_normalized"].mean())) < 1e-9

    def test_constant_column_outputs_nan(self) -> None:
        """std=0 列（共线 fallback）→ 输出 NaN（不写 0，避免下游误判）。"""
        residuals = pd.DataFrame(
            {"trend_orthogonal": [5.0, 5.0, 5.0]},
            index=["a", "b", "c"],
        )
        result = self.orth.renormalize(residuals)
        assert result["trend_normalized"].isna().all()


# ============================================================
# 组合：Step 4a + 4b
# ============================================================
class TestCompute:
    def setup_method(self) -> None:
        self.orth = Orthogonalizer(DEFAULT_ORTHOGONALIZER)

    def test_end_to_end_var_unit(self) -> None:
        """完整 Step 4a + 4b：4 个相关策略 → 4 个 N(0,1) 独立残差。"""
        rng = np.random.default_rng(42)
        n = 1000
        rho = 0.6
        trend = rng.normal(0, 1, n)
        momentum = rho * trend + np.sqrt(1 - rho**2) * rng.normal(0, 1, n)
        mean_reversion = rng.normal(0, 1, n)
        value = rng.normal(0, 1, n)
        data = {
            "trend": trend,
            "momentum": momentum,
            "mean_reversion": mean_reversion,
            "value": value,
        }
        matrix = pd.DataFrame(data, index=[f"S{i:04d}" for i in range(n)])
        result = self.orth.compute(matrix, ["trend", "momentum", "mean_reversion", "value"])

        # 输出列名应是 _normalized 后缀
        assert list(result.columns) == [
            "trend_normalized",
            "momentum_normalized",
            "mean_reversion_normalized",
            "value_normalized",
        ]

        # 每列 mean≈0, std≈1（Step 4b 保证，业界 Barra 惯例 ddof=1）
        for col in result.columns:
            assert abs(float(result[col].mean())) < 1e-9
            assert abs(float(result[col].std(ddof=1)) - 1.0) < 1e-6

        # 关键：4 列近似独立（pairwise correlation ≈ 0，业界 Barra 流程允许 < 0.01）
        # 注：Step 4a 后严格内积=0，但 Step 4b z-score 引入有限样本扰动（n=1000 → ~0.003）
        corr_matrix = result.corr()
        off_diag = corr_matrix.values - np.eye(len(corr_matrix))
        assert np.abs(off_diag).max() < 0.01

    def test_composite_z_variance_unit(self) -> None:
        """§7.6 综合评分方差归一化前提验证：
        composite_z_raw = Σ wᵢ × strategy_z_normalized，
        若 strategy_z_normalized ~ N(0,1) 独立同分布，
        Var(composite_z_raw) = Σ wᵢ²，
        composite_z = composite_z_raw / sqrt(Σ wᵢ²) ~ N(0, 1)。
        """
        rng = np.random.default_rng(42)
        n = 5000
        rho = 0.5
        trend = rng.normal(0, 1, n)
        momentum = rho * trend + np.sqrt(1 - rho**2) * rng.normal(0, 1, n)
        mean_reversion = rng.normal(0, 1, n)
        value = rng.normal(0, 1, n)
        data = {
            "trend": trend,
            "momentum": momentum,
            "mean_reversion": mean_reversion,
            "value": value,
        }
        matrix = pd.DataFrame(data, index=[f"S{i:05d}" for i in range(n)])
        normalized = self.orth.compute(
            matrix, ["trend", "momentum", "mean_reversion", "value"]
        )

        # 权重：UPTREND 默认矩阵
        weights = {"trend": 0.4, "momentum": 0.25, "mean_reversion": 0.15, "value": 0.20}
        composite_raw = (
            weights["trend"] * normalized["trend_normalized"]
            + weights["momentum"] * normalized["momentum_normalized"]
            + weights["mean_reversion"] * normalized["mean_reversion_normalized"]
            + weights["value"] * normalized["value_normalized"]
        )
        norm_factor = np.sqrt(sum(w**2 for w in weights.values()))
        composite_z = composite_raw / norm_factor

        # composite_z ~ N(0, 1)：mean≈0, var≈1（ddof=1 业界惯例）
        assert abs(float(composite_z.mean())) < 0.05
        assert abs(float(composite_z.var(ddof=1)) - 1.0) < 0.05

    def test_collinear_degeneration_outputs_nan(self) -> None:
        """v1.3 修订：高度共线（rho≈0.99）残差 std/原 std < 0.3 → 该列 NaN。

        生产 5y 真机 2026-05-12 抓到 momentum 残差被 trend/value/mean_reversion
        前序投影吸收 99.99% → 残差 std≈0.004，renormalize 用极小 std 除把 outlier
        放大到 z=23.7，主导 composite_z 顶端值。新 collinear_residual_ratio=0.3
        触发剔除避免该列污染加权。
        """
        rng = np.random.default_rng(42)
        n = 1000
        trend = rng.normal(0, 1, n)
        rho = 0.99
        # momentum 与 trend 极度相关 → 残差 std = sqrt(1 - 0.99²) ≈ 0.141 < 0.3
        momentum = rho * trend + np.sqrt(1 - rho**2) * rng.normal(0, 1, n)
        # value 独立 → 残差 std ≈ 1，不剔除
        value = rng.normal(0, 1, n)
        matrix = pd.DataFrame(
            {"trend": trend, "momentum": momentum, "value": value},
            index=[f"S{i:04d}" for i in range(n)],
        )
        result = self.orth.compute(matrix, ["trend", "momentum", "value"])

        # trend 是 order[0]，无前序投影 → std=1 不剔除
        assert not result["trend_normalized"].isna().all()
        # momentum 残差 std/原 std ≈ 0.14 < 0.3 → 整列 NaN
        assert result["momentum_normalized"].isna().all(), (
            "momentum 与 trend rho=0.99 → 应被共线退化检测剔除"
        )
        # value 独立 → 不剔除
        assert not result["value_normalized"].isna().all()
