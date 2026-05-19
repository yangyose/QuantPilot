"""Scorer 单元测试 SCR-01~05（Phase 4 T-11）。"""
from __future__ import annotations

import pytest

from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.scorer import Scorer
from quantpilot.engine.strategies.base import StrategyScore


def _make_scores(name: str, codes_scores: dict[str, float]) -> list[StrategyScore]:
    """辅助函数：构造指定策略的 StrategyScore 列表。"""
    return [
        StrategyScore(ts_code=code, raw_factors={}, score=score, reason=f"{name}:{score:.0f}")
        for code, score in codes_scores.items()
    ]


@pytest.fixture
def scorer() -> Scorer:
    return Scorer()


@pytest.fixture
def four_strategy_scores() -> dict[str, list[StrategyScore]]:
    """四大策略均有分数：A>B>C>D。"""
    return {
        "trend":         _make_scores("trend",        {"A": 80, "B": 60, "C": 40, "D": 20}),
        "momentum":      _make_scores("momentum",     {"A": 70, "B": 50, "C": 50, "D": 30}),
        "mean_reversion":_make_scores("mean_reversion",{"A": 60, "B": 70, "C": 40, "D": 30}),
        "value":         _make_scores("value",        {"A": 50, "B": 60, "C": 60, "D": 30}),
    }


# ---------------------------------------------------------------------------
# SCR-01：UPTREND 权重矩阵（趋势 40% 主导）
# ---------------------------------------------------------------------------
class TestSCR01:
    def test_uptrend_weights_applied(self, scorer: Scorer, four_strategy_scores):
        results = scorer.aggregate_legacy(MarketStateEnum.UPTREND, four_strategy_scores)
        assert len(results) == 4

        by_code = {r.ts_code: r for r in results}

        # UPTREND：trend=0.40, momentum=0.25, mean_reversion=0.15, value=0.20
        # 手动验算 A：80*0.40 + 70*0.25 + 60*0.15 + 50*0.20 = 32+17.5+9+10 = 68.5
        expected_a = 80 * 0.40 + 70 * 0.25 + 60 * 0.15 + 50 * 0.20
        assert abs(by_code["A"].composite_score - expected_a) < 0.01

    def test_uptrend_market_state_field(self, scorer: Scorer, four_strategy_scores):
        results = scorer.aggregate_legacy(MarketStateEnum.UPTREND, four_strategy_scores)
        for r in results:
            assert r.market_state == MarketStateEnum.UPTREND


# ---------------------------------------------------------------------------
# SCR-02：DOWNTREND 权重矩阵（价值 70% 主导）
# ---------------------------------------------------------------------------
class TestSCR02:
    def test_downtrend_weights_applied(self, scorer: Scorer, four_strategy_scores):
        results = scorer.aggregate_legacy(MarketStateEnum.DOWNTREND, four_strategy_scores)
        by_code = {r.ts_code: r for r in results}

        # DOWNTREND：trend=0.10, momentum=0.05, mean_reversion=0.15, value=0.70
        # 手动验算 D：20*0.10 + 30*0.05 + 30*0.15 + 30*0.70 = 2+1.5+4.5+21 = 29.0
        expected_d = 20 * 0.10 + 30 * 0.05 + 30 * 0.15 + 30 * 0.70
        assert abs(by_code["D"].composite_score - expected_d) < 0.01

    def test_downtrend_value_dominant(self, scorer: Scorer):
        """价值分高的股票在 DOWNTREND 下排名更靠前。"""
        scores = {
            "trend":         _make_scores("trend",        {"HIGH_VALUE": 20, "LOW_VALUE": 80}),
            "momentum":      _make_scores("momentum",     {"HIGH_VALUE": 20, "LOW_VALUE": 80}),
            "mean_reversion":_make_scores("mean_reversion",{"HIGH_VALUE": 20, "LOW_VALUE": 80}),
            "value":         _make_scores("value",        {"HIGH_VALUE": 90, "LOW_VALUE": 10}),
        }
        results = scorer.aggregate_legacy(MarketStateEnum.DOWNTREND, scores)
        by_code = {r.ts_code: r for r in results}
        assert by_code["HIGH_VALUE"].composite_score > by_code["LOW_VALUE"].composite_score


# ---------------------------------------------------------------------------
# SCR-03：OSCILLATION 权重矩阵（均值回归 40% 主导）
# ---------------------------------------------------------------------------
class TestSCR03:
    def test_oscillation_weights_applied(self, scorer: Scorer, four_strategy_scores):
        results = scorer.aggregate_legacy(MarketStateEnum.OSCILLATION, four_strategy_scores)
        by_code = {r.ts_code: r for r in results}

        # OSCILLATION：trend=0.15, momentum=0.15, mean_reversion=0.40, value=0.30
        # 手动验算 B：60*0.15 + 50*0.15 + 70*0.40 + 60*0.30 = 9+7.5+28+18 = 62.5
        expected_b = 60 * 0.15 + 50 * 0.15 + 70 * 0.40 + 60 * 0.30
        assert abs(by_code["B"].composite_score - expected_b) < 0.01


# ---------------------------------------------------------------------------
# SCR-04：权重归一化（缺失策略时其余权重自动归一化，总和仍为 1.0）
# ---------------------------------------------------------------------------
class TestSCR04:
    def test_missing_strategy_weights_renormalized(self, scorer: Scorer):
        """只有 trend + value 两个策略，
        权重应归一化为 trend=2/3, value=1/3（OSCILLATION 0.15:0.30）。"""
        partial_scores = {
            "trend": _make_scores("trend", {"A": 80, "B": 20}),
            "value": _make_scores("value", {"A": 20, "B": 80}),
        }
        results = scorer.aggregate_legacy(MarketStateEnum.OSCILLATION, partial_scores)
        assert len(results) == 2
        by_code = {r.ts_code: r for r in results}

        # OSCILLATION trend=0.15, value=0.30 → 归一化后 trend=0.333, value=0.667
        # A composite = 80 * (0.15/0.45) + 20 * (0.30/0.45)
        w_trend = 0.15 / (0.15 + 0.30)
        w_value = 0.30 / (0.15 + 0.30)
        expected_a = 80 * w_trend + 20 * w_value
        assert abs(by_code["A"].composite_score - expected_a) < 0.01

    def test_missing_strategy_sum_of_weights_is_one(self, scorer: Scorer):
        """score_breakdown 中各策略 weight 之和为 1.0（归一化后）。"""
        partial_scores = {
            "trend":    _make_scores("trend",    {"A": 70}),
            "momentum": _make_scores("momentum", {"A": 50}),
            # mean_reversion 和 value 缺失
        }
        results = scorer.aggregate_legacy(MarketStateEnum.UPTREND, partial_scores)
        assert len(results) == 1
        breakdown = results[0].score_breakdown
        total_weight = sum(v["weight"] for v in breakdown.values())
        assert abs(total_weight - 1.0) < 1e-9

    def test_empty_strategy_scores_returns_empty(self, scorer: Scorer):
        """所有策略都缺失时返回空列表。"""
        results = scorer.aggregate_legacy(MarketStateEnum.UPTREND, {})
        assert results == []


# ---------------------------------------------------------------------------
# SCR-05：score_breakdown 结构（score / weight / contribution 三字段）
# ---------------------------------------------------------------------------
class TestSCR05:
    def test_score_breakdown_has_required_fields(self, scorer: Scorer, four_strategy_scores):
        results = scorer.aggregate_legacy(MarketStateEnum.UPTREND, four_strategy_scores)
        assert results
        for result in results:
            assert isinstance(result.score_breakdown, dict)
            for strategy_name, breakdown in result.score_breakdown.items():
                assert "score" in breakdown, f"breakdown[{strategy_name}] missing 'score'"
                assert "weight" in breakdown, f"breakdown[{strategy_name}] missing 'weight'"
                assert "contribution" in breakdown, (
                    f"breakdown[{strategy_name}] missing 'contribution'"
                )

    def test_score_breakdown_contribution_equals_score_times_weight(
        self, scorer: Scorer, four_strategy_scores
    ):
        results = scorer.aggregate_legacy(MarketStateEnum.UPTREND, four_strategy_scores)
        for result in results:
            for strategy_name, breakdown in result.score_breakdown.items():
                expected_contrib = breakdown["score"] * breakdown["weight"]
                assert abs(breakdown["contribution"] - expected_contrib) < 0.01

    def test_composite_score_equals_sum_of_contributions(
        self, scorer: Scorer, four_strategy_scores
    ):
        """composite_score == sum(contribution)（数学一致性检验）。"""
        results = scorer.aggregate_legacy(MarketStateEnum.UPTREND, four_strategy_scores)
        for result in results:
            total = sum(v["contribution"] for v in result.score_breakdown.values())
            assert abs(result.composite_score - total) < 0.01

    def test_individual_scores_in_breakdown(self, scorer: Scorer, four_strategy_scores):
        """score_breakdown 中的 score 与 per_strategy_scores 一致。"""
        results = scorer.aggregate_legacy(MarketStateEnum.UPTREND, four_strategy_scores)
        by_code = {r.ts_code: r for r in results}

        # A 的趋势分 = 80
        assert abs(by_code["A"].score_breakdown["trend"]["score"] - 80) < 0.01
        assert abs(by_code["A"].score_breakdown["value"]["score"] - 50) < 0.01

    def test_strategy_scores_stored_on_composite(self, scorer: Scorer, four_strategy_scores):
        """CompositeScore 上的四个独立 score 字段与 strategy_scores 输入一致。"""
        results = scorer.aggregate_legacy(MarketStateEnum.UPTREND, four_strategy_scores)
        by_code = {r.ts_code: r for r in results}

        assert abs(by_code["A"].trend_score - 80) < 0.01
        assert abs(by_code["A"].momentum_score - 70) < 0.01
        assert abs(by_code["A"].reversion_score - 60) < 0.01
        assert abs(by_code["A"].value_score - 50) < 0.01

    def test_explanation_contains_reasons(self, scorer: Scorer, four_strategy_scores):
        """explanation 字段非空，来自各策略 StrategyScore.reason 的合并。"""
        results = scorer.aggregate_legacy(MarketStateEnum.UPTREND, four_strategy_scores)
        for result in results:
            assert isinstance(result.explanation, str)
            assert len(result.explanation) > 0
