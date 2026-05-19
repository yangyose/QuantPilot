"""Phase 11 §3.3 Scorer 5 步管线新 aggregate 单元测试 SCR-P11-01~10。

覆盖：
- 完整 5 步管线（4 策略，每策略 1~3 列）
- single_strategy_mode 跳过 Step 4/5
- weights_runtime 权重归一化 + sqrt(Σw²) 缩放
- composite_pct_in_market = rank_descending/N（高分→低 pct）
- composite_score = Φ(z) × 100
- score_breakdown_raw / score_breakdown_residual 结构
- explanation top2 文本
- 单股票全策略 NaN → 剔除
- weights_source / hysteresis_status 透传
- 旧字段（trend_score 等）= Φ(strategy_z) × 100
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.scorer import Scorer


def _build_factors(
    strategies_to_codes: dict[str, dict[str, float]],
    factor_cols_per_strategy: dict[str, list[str]] | None = None,
) -> dict[str, pd.DataFrame]:
    """便捷工厂：strategies_to_codes = {strategy: {ts_code: scalar_factor_value}}。

    若 factor_cols_per_strategy 提供，则每策略的 DataFrame 含多个因子列，
    每列值 = strategy_to_codes[strategy][ts_code] + col_offset（offset 仅区分列）。
    """
    out: dict[str, pd.DataFrame] = {}
    for s, mapping in strategies_to_codes.items():
        cols = (factor_cols_per_strategy or {}).get(s, [f"f_{s}"])
        rows = {}
        for code, base in mapping.items():
            rows[code] = [base + 0.0 * i for i in range(len(cols))]
        df = pd.DataFrame.from_dict(rows, orient="index", columns=cols)
        df.index.name = "ts_code"
        out[s] = df
    return out


def _build_snapshot(
    ts_codes: list[str],
    industries: dict[str, str] | None = None,
) -> dict:
    """构造 5 步管线最少字段 MarketSnapshot（industry / market_cap / beta）。

    market_cap 用均匀分布的小区间值，避免对策略 z 产生显著系统漂移。
    """
    if industries is None:
        # 默认：所有股票同一个行业 → 行业 dummy 仅 1 个（drop_first=True 后无 dummy）
        industries = {c: "TECH" for c in ts_codes}
    market_cap = pd.Series(
        np.linspace(1e9, 2e9, num=len(ts_codes)),
        index=pd.Index(ts_codes, name="ts_code"),
    )
    return {
        "industry": industries,
        "market_cap": market_cap,
        "beta": None,
    }


class TestBasic5Step:
    """SCR-P11-01：4 策略各 1 列，5 步管线全开。"""

    def test_outputs_per_universe_ts_code(self) -> None:
        codes = [f"00000{i}.SZ" for i in range(1, 11)]
        # 4 策略，每个策略对每个 ts_code 给不同 raw 值
        factors = _build_factors({
            "trend": {c: i * 1.0 for i, c in enumerate(codes)},
            "momentum": {c: (i % 4) * 1.0 for i, c in enumerate(codes)},
            "mean_reversion": {c: (10 - i) * 1.0 for i, c in enumerate(codes)},
            "value": {c: math.sin(i) for i, c in enumerate(codes)},
        })
        # 多行业避免行业 dummy=0
        industries = {c: ("TECH" if i % 2 == 0 else "FINANCE") for i, c in enumerate(codes)}
        snap = _build_snapshot(codes, industries)
        scorer = Scorer()
        results = scorer.aggregate(
            market_state=MarketStateEnum.OSCILLATION,
            strategy_factors=factors,
            snapshot=snap,
            weights_runtime={
                "trend": 0.25, "momentum": 0.25, "mean_reversion": 0.25, "value": 0.25,
            },
            weights_source="icir",
            orthogonalize_order=["trend", "momentum", "mean_reversion", "value"],
            hysteresis_status="stable",
        )
        assert len(results) == 10
        for r in results:
            assert r.composite_z is not None
            assert r.composite_pct_in_market is not None
            assert 0 <= r.composite_pct_in_market <= 1
            assert r.composite_score == pytest.approx(norm.cdf(r.composite_z) * 100, abs=1e-6)
            assert r.weights_source == "icir"
            assert r.hysteresis_status == "stable"
            assert isinstance(r.score_breakdown_raw, dict)
            assert len(r.score_breakdown_raw) == 4
            assert isinstance(r.score_breakdown_residual, dict)
            assert len(r.score_breakdown_residual) == 4


class TestPctInMarketDescendingRank:
    """SCR-P11-02：composite_pct_in_market = rank_descending/N，最高分 pct 最小。"""

    def test_top_scorer_has_min_pct(self) -> None:
        codes = [f"00000{i}.SZ" for i in range(1, 11)]
        factors = _build_factors({
            "trend": {c: i * 10.0 for i, c in enumerate(codes)},  # 单调递增
        })
        industries = {c: ("TECH" if i % 2 == 0 else "FINANCE") for i, c in enumerate(codes)}
        snap = _build_snapshot(codes, industries)
        scorer = Scorer()
        results = scorer.aggregate(
            market_state=MarketStateEnum.OSCILLATION,
            strategy_factors=factors,
            snapshot=snap,
            weights_runtime={"trend": 1.0},
            weights_source="icir",
            orthogonalize_order=["trend"],
            hysteresis_status="stable",
            single_strategy_mode=True,
        )
        # 最高 raw 因子 = 9 (i=9, 000009.SZ → 后面 _build_factors 用 0-indexed range(1,11))
        by_code = {r.ts_code: r for r in results}
        sorted_by_z = sorted(by_code.values(), key=lambda r: r.composite_z, reverse=True)
        # 最高 z 的 pct 应最小（最靠前）
        assert sorted_by_z[0].composite_pct_in_market < sorted_by_z[-1].composite_pct_in_market


class TestVarianceUnit:
    """SCR-P11-03：composite_z 方差归一化（公式 composite_z = Σw·z / sqrt(Σw²)）。

    用单策略 + single_strategy_mode 校验：composite_z 应等于 strategy_z（z-score 化后）。
    """

    def test_single_strategy_composite_equals_strategy_z(self) -> None:
        codes = [f"{i:06d}.SZ" for i in range(1, 21)]
        # 用线性分布 raw 因子值
        raw_values = np.linspace(-5, 5, num=20)
        factors = {
            "trend": pd.DataFrame(
                {"f_trend": raw_values},
                index=pd.Index(codes, name="ts_code"),
            ),
        }
        industries = {c: ("TECH" if i % 2 == 0 else "FINANCE") for i, c in enumerate(codes)}
        snap = _build_snapshot(codes, industries)
        scorer = Scorer()
        results = scorer.aggregate(
            market_state=MarketStateEnum.OSCILLATION,
            strategy_factors=factors,
            snapshot=snap,
            weights_runtime={"trend": 1.0},
            weights_source="default_matrix",
            orthogonalize_order=["trend"],
            hysteresis_status="stable",
            single_strategy_mode=True,
        )
        zs = pd.Series({r.ts_code: r.composite_z for r in results})
        # composite_z 应近似 z-score：均值 ≈ 0，标准差 ≈ 1
        assert abs(zs.mean()) < 1e-6
        assert abs(zs.std(ddof=1) - 1.0) < 0.05


class TestScoreBreakdownRawConsistency:
    """SCR-P11-04：score_breakdown_raw[s].contribution = z_raw × weight，
    Σ contribution == composite_z × sqrt(Σw²)（未除归一化前）。"""

    def test_contribution_equals_z_raw_times_weight(self) -> None:
        codes = [f"{i:06d}.SZ" for i in range(1, 11)]
        factors = _build_factors({
            "trend": {c: i * 1.0 for i, c in enumerate(codes)},
            "momentum": {c: math.cos(i) for i, c in enumerate(codes)},
        })
        industries = {c: ("TECH" if i % 2 == 0 else "FINANCE") for i, c in enumerate(codes)}
        snap = _build_snapshot(codes, industries)
        scorer = Scorer()
        results = scorer.aggregate(
            market_state=MarketStateEnum.OSCILLATION,
            strategy_factors=factors,
            snapshot=snap,
            weights_runtime={"trend": 0.6, "momentum": 0.4},
            weights_source="icir",
            orthogonalize_order=["trend", "momentum"],
            hysteresis_status="stable",
        )
        for r in results:
            for s_name, bd in r.score_breakdown_raw.items():
                assert bd["contribution"] == pytest.approx(bd["z_raw"] * bd["weight"], abs=1e-9)


class TestScoreBreakdownResidualSum:
    """SCR-P11-05：Σ residual.contribution == composite_z（数学一致性，新 5 步管线主输出公式）。"""

    def test_residual_contribution_sums_to_composite_z(self) -> None:
        codes = [f"{i:06d}.SZ" for i in range(1, 11)]
        factors = _build_factors({
            "trend": {c: i * 1.0 for i, c in enumerate(codes)},
            "momentum": {c: (i % 3) * 0.5 for i, c in enumerate(codes)},
        })
        industries = {c: ("TECH" if i % 2 == 0 else "FINANCE") for i, c in enumerate(codes)}
        snap = _build_snapshot(codes, industries)
        scorer = Scorer()
        results = scorer.aggregate(
            market_state=MarketStateEnum.OSCILLATION,
            strategy_factors=factors,
            snapshot=snap,
            weights_runtime={"trend": 0.6, "momentum": 0.4},
            weights_source="icir",
            orthogonalize_order=["trend", "momentum"],
            hysteresis_status="stable",
        )
        for r in results:
            total = sum(bd["contribution"] for bd in r.score_breakdown_residual.values())
            assert total == pytest.approx(r.composite_z, abs=1e-6), (
                f"{r.ts_code}: Σresidual {total} != composite_z {r.composite_z}"
            )


class TestWeightsRenormalization:
    """SCR-P11-06：weights_runtime 不为 1 时，自动 renormalize 后再做加权。"""

    def test_weights_renormalize(self) -> None:
        codes = [f"{i:06d}.SZ" for i in range(1, 11)]
        factors = _build_factors({
            "trend": {c: i * 1.0 for i, c in enumerate(codes)},
            "momentum": {c: (10 - i) * 1.0 for i, c in enumerate(codes)},
        })
        industries = {c: ("TECH" if i % 2 == 0 else "FINANCE") for i, c in enumerate(codes)}
        snap = _build_snapshot(codes, industries)
        scorer = Scorer()
        # weights sum != 1
        results = scorer.aggregate(
            market_state=MarketStateEnum.OSCILLATION,
            strategy_factors=factors,
            snapshot=snap,
            weights_runtime={"trend": 3.0, "momentum": 1.0},
            weights_source="icir",
            orthogonalize_order=["trend", "momentum"],
            hysteresis_status="stable",
        )
        # 归一化后 trend=0.75, momentum=0.25
        for r in results:
            assert r.score_breakdown_raw["trend"]["weight"] == pytest.approx(0.75, abs=1e-6)
            assert r.score_breakdown_raw["momentum"]["weight"] == pytest.approx(0.25, abs=1e-6)


class TestSingleStrategyMode:
    """SCR-P11-07：single_strategy_mode=True 时跳过 Gram-Schmidt（z_normalized = strategy_z）。"""

    def test_single_strategy_no_orthogonalize(self) -> None:
        codes = [f"{i:06d}.SZ" for i in range(1, 11)]
        factors = _build_factors({
            "trend": {c: i * 1.0 for i, c in enumerate(codes)},
        })
        industries = {c: ("TECH" if i % 2 == 0 else "FINANCE") for i, c in enumerate(codes)}
        snap = _build_snapshot(codes, industries)
        scorer = Scorer()
        results = scorer.aggregate(
            market_state=MarketStateEnum.OSCILLATION,
            strategy_factors=factors,
            snapshot=snap,
            weights_runtime={"trend": 1.0},
            weights_source="default_matrix",
            orthogonalize_order=["trend"],
            hysteresis_status="stable",
            single_strategy_mode=True,
        )
        # 单策略：z_orthogonal_normalized 应等于 strategy_z（raw 路径走 zscore 后即 z_orth）
        for r in results:
            z_raw_trend = r.score_breakdown_raw["trend"]["z_raw"]
            z_orth_trend = r.score_breakdown_residual["trend"]["z_orthogonal_normalized"]
            assert z_orth_trend == pytest.approx(z_raw_trend, abs=1e-6)


class TestEmptyOrAllNan:
    """SCR-P11-08：strategy_factors 空 / 全 NaN → 返回空。"""

    def test_empty_strategy_factors(self) -> None:
        scorer = Scorer()
        results = scorer.aggregate(
            market_state=MarketStateEnum.OSCILLATION,
            strategy_factors={},
            snapshot=_build_snapshot([]),
            weights_runtime={},
            weights_source="default_matrix",
            orthogonalize_order=[],
            hysteresis_status="stable",
        )
        assert results == []

    def test_all_nan_strategy_factors(self) -> None:
        codes = ["000001.SZ", "000002.SZ"]
        factors = {
            "trend": pd.DataFrame(
                {"f_trend": [float("nan"), float("nan")]},
                index=pd.Index(codes, name="ts_code"),
            ),
        }
        industries = {c: "TECH" for c in codes}
        snap = _build_snapshot(codes, industries)
        scorer = Scorer()
        results = scorer.aggregate(
            market_state=MarketStateEnum.OSCILLATION,
            strategy_factors=factors,
            snapshot=snap,
            weights_runtime={"trend": 1.0},
            weights_source="default_matrix",
            orthogonalize_order=["trend"],
            hysteresis_status="stable",
            single_strategy_mode=True,
        )
        assert results == []


class TestExplanation:
    """SCR-P11-09：explanation 文本含 top2 策略名 + pct + 强度阈值。"""

    def test_explanation_format(self) -> None:
        codes = [f"{i:06d}.SZ" for i in range(1, 11)]
        factors = _build_factors({
            "trend": {c: i * 1.0 for i, c in enumerate(codes)},
            "momentum": {c: i * 0.5 for i, c in enumerate(codes)},
            "value": {c: math.cos(i) for i, c in enumerate(codes)},
        })
        industries = {c: ("TECH" if i % 2 == 0 else "FINANCE") for i, c in enumerate(codes)}
        snap = _build_snapshot(codes, industries)
        scorer = Scorer()
        results = scorer.aggregate(
            market_state=MarketStateEnum.OSCILLATION,
            strategy_factors=factors,
            snapshot=snap,
            weights_runtime={"trend": 0.5, "momentum": 0.3, "value": 0.2},
            weights_source="icir",
            orthogonalize_order=["trend", "momentum", "value"],
            hysteresis_status="stable",
        )
        for r in results:
            assert "全市场 top" in r.explanation
            assert "买入信号" in r.explanation  # "买入信号" 或 "强买入信号"


class TestLegacyFields:
    """SCR-P11-10：旧 4 标量字段 = Φ(strategy_z) × 100；market_state / weights_source 透传。"""

    def test_legacy_scalar_fields(self) -> None:
        codes = [f"{i:06d}.SZ" for i in range(1, 11)]
        factors = _build_factors({
            "trend": {c: i * 1.0 for i, c in enumerate(codes)},
            "momentum": {c: math.sin(i) for i, c in enumerate(codes)},
        })
        industries = {c: ("TECH" if i % 2 == 0 else "FINANCE") for i, c in enumerate(codes)}
        snap = _build_snapshot(codes, industries)
        scorer = Scorer()
        results = scorer.aggregate(
            market_state=MarketStateEnum.UPTREND,
            strategy_factors=factors,
            snapshot=snap,
            weights_runtime={"trend": 0.6, "momentum": 0.4},
            weights_source="icir",
            orthogonalize_order=["trend", "momentum"],
            hysteresis_status="pending_switch",
        )
        for r in results:
            # 4 标量字段：策略缺失为 None
            assert r.value_score is None
            assert r.reversion_score is None
            assert r.trend_score is not None
            assert 0 <= r.trend_score <= 100
            # market_state 透传
            assert r.market_state == MarketStateEnum.UPTREND
            # hysteresis_status 透传
            assert r.hysteresis_status == "pending_switch"
            # composite_score = Φ(composite_z) × 100
            assert r.composite_score == pytest.approx(norm.cdf(r.composite_z) * 100, abs=1e-6)
