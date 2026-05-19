"""FactorPipeline 单元测试（Phase 11 §3.1）。

覆盖：Winsorize 1%/99% / OLS 残差 / Z-score / NaN 透传 / 单股 corner /
回归奇异降级。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantpilot.engine.factor_pipeline import (
    DEFAULT_FACTOR_PIPELINE,
    FactorPipeline,
    FactorPipelineConfig,
)


# ============================================================
# Step 1：Winsorize
# ============================================================
class TestWinsorize:
    def setup_method(self) -> None:
        self.pipeline = FactorPipeline(DEFAULT_FACTOR_PIPELINE)

    def test_clips_top_and_bottom_1pct(self) -> None:
        np.random.seed(42)
        values = pd.Series(np.random.normal(0, 1, 1000), index=[f"S{i:04d}" for i in range(1000)])
        result = self.pipeline.winsorize(values)
        # 1% 和 99% 分位应该被截断
        assert result.min() >= np.nanpercentile(values, 1) - 1e-9
        assert result.max() <= np.nanpercentile(values, 99) + 1e-9
        # 中间 98% 的值不变（保留尾部以外的形态）
        p5 = np.nanpercentile(values, 5)
        p95 = np.nanpercentile(values, 95)
        mid_mask = (values >= p5) & (values <= p95)
        assert (result[mid_mask] == values[mid_mask]).all()

    def test_preserves_nan(self) -> None:
        values = pd.Series([1.0, 2.0, np.nan, 4.0, 5.0], index=["a", "b", "c", "d", "e"])
        result = self.pipeline.winsorize(values)
        assert pd.isna(result["c"])
        assert result.notna().sum() == 4

    def test_all_nan(self) -> None:
        values = pd.Series([np.nan, np.nan, np.nan], index=["a", "b", "c"])
        result = self.pipeline.winsorize(values)
        assert result.isna().all()

    def test_all_identical(self) -> None:
        values = pd.Series([5.0, 5.0, 5.0, 5.0], index=["a", "b", "c", "d"])
        result = self.pipeline.winsorize(values)
        # 全相同值 → 1% / 99% 分位也是 5.0，clip 后仍是 5.0
        assert (result == 5.0).all()

    def test_extreme_outliers_clipped(self) -> None:
        # 1 个极大值 + 1 个极小值 + 98 个正常值 → 应被截到 1%/99% 分位
        normal = np.random.normal(0, 1, 98)
        values = pd.Series(
            list(normal) + [1000.0, -1000.0],
            index=[f"S{i}" for i in range(100)],
        )
        result = self.pipeline.winsorize(values)
        assert result.max() < 100  # 远小于 1000
        assert result.min() > -100


# ============================================================
# Step 2：中性化（OLS 残差）
# ============================================================
class TestNeutralize:
    def setup_method(self) -> None:
        # 默认配置：行业开 / 市值开 / Beta 关
        self.pipeline = FactorPipeline(DEFAULT_FACTOR_PIPELINE)

    def _mk_data(self, n_per_ind: int = 30) -> tuple[pd.Series, dict[str, str], pd.Series]:
        """构造合成数据：3 个行业，每行业 n_per_ind 只股票。

        因子值 = 行业基线 + log(market_cap) × 0.5 + 噪声
        中性化后残差应主要由噪声驱动（行业基线 + 市值效应被剥离）。
        """
        rng = np.random.default_rng(42)
        codes: list[str] = []
        industries: dict[str, str] = {}
        market_cap_values: dict[str, float] = {}
        factor_values: dict[str, float] = {}
        ind_baseline = {"A": 1.0, "B": 0.0, "C": -1.0}

        for ind, baseline in ind_baseline.items():
            for i in range(n_per_ind):
                code = f"{ind}{i:04d}"
                codes.append(code)
                industries[code] = ind
                mv = float(rng.uniform(10.0, 1000.0))  # 亿
                market_cap_values[code] = mv
                noise = float(rng.normal(0, 0.5))
                factor_values[code] = baseline + 0.5 * np.log(mv) + noise

        values = pd.Series(factor_values, dtype=float)
        market_cap = pd.Series(market_cap_values, dtype=float)
        return values, industries, market_cap

    def test_strips_industry_and_mv_effects(self) -> None:
        values, industries, mv = self._mk_data()
        residuals = self.pipeline.neutralize(values, industries, market_cap=mv)

        # 残差应该接近 0 均值（OLS 性质）
        assert abs(residuals.mean(skipna=True)) < 1e-9
        # 残差 std 应小于原 values 的 std（行业 + 市值效应被剥离）
        assert residuals.std(skipna=True) < values.std() * 0.95

    def test_industry_missing_returns_nan(self) -> None:
        values = pd.Series({"A1": 1.0, "A2": 2.0, "B1": 3.0, "Z1": 4.0})
        industries = {"A1": "A", "A2": "A", "B1": "B"}  # Z1 缺
        mv = pd.Series({"A1": 50.0, "A2": 60.0, "B1": 70.0, "Z1": 80.0})
        result = self.pipeline.neutralize(values, industries, market_cap=mv)
        # Z1 industry 缺 → 输出 NaN
        assert pd.isna(result["Z1"])

    def test_singular_falls_back_to_original(self) -> None:
        # 自由度不足：3 行，包含 1 行业 + log_mv + const = 2 列 X，但 n=3
        # n_obs(3) ≤ n_features(2 + 1 const = 3) → 降级
        values = pd.Series({"A1": 1.0, "A2": 2.0, "A3": 3.0})
        industries = {"A1": "A", "A2": "A", "A3": "A"}
        mv = pd.Series({"A1": 10.0, "A2": 20.0, "A3": 30.0})
        result = self.pipeline.neutralize(values, industries, market_cap=mv)
        # 降级：残差=原值
        assert (result == values).all()

    def test_market_cap_disabled(self) -> None:
        cfg = FactorPipelineConfig(neutralize_industry=True, neutralize_market_cap=False)
        pipeline = FactorPipeline(cfg)
        values, industries, _ = self._mk_data(n_per_ind=20)
        residuals = pipeline.neutralize(values, industries, market_cap=None)
        # 仍能跑出来（不要求市值）
        assert residuals.notna().sum() > 0

    def test_negative_market_cap_treated_as_nan(self) -> None:
        # market_cap ≤ 0 不能 log → 该行输出 NaN
        values = pd.Series({"A1": 1.0, "A2": 2.0, "A3": 3.0, "A4": 4.0, "B1": 5.0, "B2": 6.0})
        industries = {c: ("A" if c.startswith("A") else "B") for c in values.index}
        mv = pd.Series({"A1": 10.0, "A2": 20.0, "A3": -5.0, "A4": 40.0, "B1": 50.0, "B2": 60.0})
        result = self.pipeline.neutralize(values, industries, market_cap=mv)
        assert pd.isna(result["A3"])  # log(-5) 不存在 → drop → NaN


# ============================================================
# Step 3：Z-score
# ============================================================
class TestZscore:
    def setup_method(self) -> None:
        self.pipeline = FactorPipeline(DEFAULT_FACTOR_PIPELINE)

    def test_zero_mean_unit_std(self) -> None:
        rng = np.random.default_rng(42)
        values = pd.Series(rng.normal(5, 3, 500), index=[f"S{i}" for i in range(500)])
        result = self.pipeline.zscore(values)
        assert abs(result.mean()) < 1e-9
        assert abs(result.std() - 1.0) < 1e-6

    def test_all_identical_returns_zeros(self) -> None:
        values = pd.Series([3.0, 3.0, 3.0, 3.0])
        result = self.pipeline.zscore(values)
        assert (result == 0.0).all()

    def test_preserves_nan(self) -> None:
        values = pd.Series([1.0, 2.0, np.nan, 4.0, 5.0])
        result = self.pipeline.zscore(values)
        assert pd.isna(result[2])
        assert result.dropna().shape[0] == 4

    def test_empty(self) -> None:
        values = pd.Series([], dtype=float)
        result = self.pipeline.zscore(values)
        assert result.empty


# ============================================================
# 组合：Step 1~3 完整管线
# ============================================================
class TestRunSteps1To3:
    def test_end_to_end(self) -> None:
        pipeline = FactorPipeline(DEFAULT_FACTOR_PIPELINE)
        rng = np.random.default_rng(42)

        codes = [f"A{i:04d}" for i in range(30)] + [f"B{i:04d}" for i in range(30)]
        industries = {c: c[0] for c in codes}
        mv = pd.Series({c: float(rng.uniform(10, 1000)) for c in codes})

        raw = pd.Series(
            {c: float(rng.normal(0, 2)) for c in codes},
            dtype=float,
        )
        # 增 1 个极端值，应被 winsorize
        raw["A0000"] = 1000.0

        result = pipeline.run_steps_1_to_3(raw, industries, market_cap=mv)

        # 输出应大致 Z-score 分布：mean≈0, std≈1（ddof=1 业界惯例）
        assert abs(result.mean(skipna=True)) < 1e-9
        assert abs(result.std(skipna=True) - 1.0) < 0.1
        # 未 winsorize 时 1000 的原始 z（用清洁数据估 std）≈ 1000 / σ_clean ≈ 500
        # winsorize 截断到 p99 + 中性化 + zscore 后，A0000 的 z 应缩 ≥ 95%
        # 注：raw.std() 本身被 1000 污染（~129），不能用作"原始 z"估计的分母
        raw_clean_std = float(raw.drop("A0000").std())
        original_z_estimate = 1000.0 / raw_clean_std
        assert abs(result["A0000"]) < original_z_estimate * 0.05
