"""Scorer：三状态权重矩阵，综合评分（Phase 4，Phase 10 接入 UserConfig）。"""
from __future__ import annotations

from dataclasses import dataclass

from quantpilot.core.config_defaults import (
    DEFAULT_STRATEGY_WEIGHTS,
    StrategyWeightsConfig,
)
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.strategies.base import StrategyScore

# SDD §7.5 权重矩阵默认值（保留为模块级常量方便回溯；实际值以 DEFAULT_STRATEGY_WEIGHTS 为准）
WEIGHTS: dict[MarketStateEnum, dict[str, float]] = {
    MarketStateEnum.UPTREND: DEFAULT_STRATEGY_WEIGHTS.uptrend,
    MarketStateEnum.DOWNTREND: DEFAULT_STRATEGY_WEIGHTS.downtrend,
    MarketStateEnum.OSCILLATION: DEFAULT_STRATEGY_WEIGHTS.oscillation,
}

# DB 列名映射（CandidatePool.reversion_score 对应策略 key mean_reversion）
SCORE_COLUMN_MAP: dict[str, str] = {
    "trend":           "trend_score",
    "momentum":        "momentum_score",
    "mean_reversion":  "reversion_score",
    "value":           "value_score",
}


@dataclass(frozen=True)
class CompositeScore:
    ts_code: str
    composite_score: float
    trend_score: float | None
    momentum_score: float | None
    reversion_score: float | None
    value_score: float | None
    market_state: MarketStateEnum
    score_breakdown: dict        # {"trend": {"score": x, "weight": 0.40, "contribution": y}, ...}
    explanation: str             # 合并各策略 reason


class Scorer:
    """纯函数，无 IO。按市场状态权重矩阵聚合四大策略评分。

    Phase 10：`weights` 参数注入自 `config_service.get_strategy_weights()`，
    支持用户在 Settings 调整三态 × 4 策略权重矩阵（SDD §7.5）。
    """

    def __init__(
        self, weights: StrategyWeightsConfig = DEFAULT_STRATEGY_WEIGHTS
    ) -> None:
        self._weights = weights

    def _matrix(self) -> dict[MarketStateEnum, dict[str, float]]:
        return {
            MarketStateEnum.UPTREND: self._weights.uptrend,
            MarketStateEnum.DOWNTREND: self._weights.downtrend,
            MarketStateEnum.OSCILLATION: self._weights.oscillation,
        }

    def aggregate(
        self,
        market_state: MarketStateEnum,
        strategy_scores: dict[str, list[StrategyScore]],
    ) -> list[CompositeScore]:
        """
        按市场状态权重加权求和。
        strategy_scores 中缺失的策略权重按比例重新归一化分配给其余策略。
        """
        base_weights = self._matrix()[market_state]
        # 仅保留有分数的策略键
        active_keys = [k for k in base_weights if k in strategy_scores and strategy_scores[k]]
        if not active_keys:
            return []

        # 归一化权重
        raw_total = sum(base_weights[k] for k in active_keys)
        norm_weights = {k: base_weights[k] / raw_total for k in active_keys}

        # 将每个策略的 list[StrategyScore] 转为 dict[ts_code, score]
        per_strategy: dict[str, dict[str, float]] = {}
        per_strategy_reason: dict[str, dict[str, str]] = {}
        all_codes: set[str] = set()
        for key in active_keys:
            per_strategy[key] = {s.ts_code: s.score for s in strategy_scores[key]}
            per_strategy_reason[key] = {s.ts_code: s.reason for s in strategy_scores[key]}
            all_codes |= per_strategy[key].keys()

        # 只保留在所有 active 策略中都出现的股票（保证 composite 不含 NaN）
        valid_codes = all_codes
        for key in active_keys:
            valid_codes = valid_codes & per_strategy[key].keys()

        if not valid_codes:
            return []

        results: list[CompositeScore] = []
        for ts_code in valid_codes:
            breakdown: dict[str, dict[str, float]] = {}
            composite = 0.0
            for key in active_keys:
                s = per_strategy[key][ts_code]
                w = norm_weights[key]
                contrib = s * w
                breakdown[key] = {"score": s, "weight": w, "contribution": contrib}
                composite += contrib

            # 各维度评分（未参与计算的策略赋 None）
            trend_score = per_strategy.get("trend", {}).get(ts_code)
            momentum_score = per_strategy.get("momentum", {}).get(ts_code)
            reversion_score = per_strategy.get("mean_reversion", {}).get(ts_code)
            value_score = per_strategy.get("value", {}).get(ts_code)

            # 合并各策略 reason
            reasons = [
                per_strategy_reason[key].get(ts_code, "")
                for key in active_keys
                if per_strategy_reason[key].get(ts_code)
            ]
            explanation = " | ".join(reasons)

            results.append(CompositeScore(
                ts_code=ts_code,
                composite_score=composite,
                trend_score=trend_score,
                momentum_score=momentum_score,
                reversion_score=reversion_score,
                value_score=value_score,
                market_state=market_state,
                score_breakdown=breakdown,
                explanation=explanation,
            ))

        return results
