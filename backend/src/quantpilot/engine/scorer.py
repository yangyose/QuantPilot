"""Scorer：5 步评分管线 + 三层输出（Phase 11 §3.3 全量重写）。

Phase 11 改造：
- 旧 ``aggregate(market_state, strategy_scores)`` 重命名为 ``aggregate_legacy``
  （冷启动 / 单策略回测 fallback 路径，对应 §3.4 末段；仍由 BacktestEngine / 旧
  ScoringService.run_daily_scoring 直接调用）
- 新 ``aggregate(market_state, strategy_factors, snapshot, weights_runtime,
  weights_source, orthogonalize_order, hysteresis_status, single_strategy_mode=False)``
  实现完整 5 步管线（Winsorize / 中性化 / Z-score / Gram-Schmidt 含再标准化 / 三层输出）
- ``CompositeScore`` 扩展 Phase 11 新字段（composite_z / composite_pct_in_market /
  score_breakdown_raw / score_breakdown_residual / weights_source / hysteresis_status）
  并兼容旧 4 标量字段
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd
from scipy.stats import norm

from quantpilot.core.config_defaults import (
    DEFAULT_STRATEGY_WEIGHTS,
    StrategyWeightsConfig,
)
from quantpilot.engine.factor_pipeline import FactorPipeline
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.orthogonalizer import Orthogonalizer
from quantpilot.engine.strategies.base import MarketSnapshot, StrategyScore

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

_STRATEGY_KEYS = ("trend", "momentum", "mean_reversion", "value")


@dataclass(frozen=True)
class CompositeScore:
    """评分输出（Phase 11 §3.3 三层 + 旧兼容字段）。

    - 新路径（5 步管线 `aggregate`）：composite_z / composite_pct_in_market /
      score_breakdown_raw / score_breakdown_residual / weights_source /
      hysteresis_status 全部填充。
    - 旧路径（`aggregate_legacy` 冷启动 / 回测 fallback）：新字段为 None，
      score_breakdown 保留旧结构。
    """

    ts_code: str
    composite_score: float
    trend_score: float | None
    momentum_score: float | None
    reversion_score: float | None
    value_score: float | None
    market_state: MarketStateEnum
    score_breakdown: dict        # 旧字段：{"trend": {"score", "weight", "contribution"}}
    explanation: str

    # === Phase 11 新增字段 ===
    # 层 1：跨期可比 z 分；层 2：rank_descending/N（越小越靠前）；层 3：Φ(z)×100
    composite_z: float | None = None
    composite_pct_in_market: float | None = None
    # {strategy: {z_raw, weight, contribution}}
    score_breakdown_raw: dict | None = None
    # {strategy: {z_orthogonal_normalized, weight, contribution}}
    score_breakdown_residual: dict | None = None
    # icir / default_matrix / user_override / legacy_phase4 /
    # industry_missing_skipped / collinear_skipped
    weights_source: str = "legacy_phase4"
    hysteresis_status: str = "stable"
    # P11 §6.2 lineage 用，aggregate 透传
    factor_winsorized: dict | None = field(default=None)
    factor_neutralized: dict | None = field(default=None)
    factor_orthogonal: dict | None = field(default=None)


class Scorer:
    """5 步评分管线编排器（Phase 11 §3.3）。

    构造时注入 FactorPipeline + Orthogonalizer + 冷启动 fallback 权重；
    ``aggregate`` 走完整 5 步管线，``aggregate_legacy`` 走旧 Phase 4 权重矩阵
    （供 BacktestEngine / 冷启动 / 单策略回测 fallback）。
    """

    def __init__(
        self,
        weights: StrategyWeightsConfig = DEFAULT_STRATEGY_WEIGHTS,
        pipeline: FactorPipeline | None = None,
        orthogonalizer: Orthogonalizer | None = None,
    ) -> None:
        self._weights = weights
        self._pipeline = pipeline or FactorPipeline()
        self._orthogonalizer = orthogonalizer or Orthogonalizer()

    def _matrix(self) -> dict[MarketStateEnum, dict[str, float]]:
        return {
            MarketStateEnum.UPTREND: self._weights.uptrend,
            MarketStateEnum.DOWNTREND: self._weights.downtrend,
            MarketStateEnum.OSCILLATION: self._weights.oscillation,
        }

    # ================================================================
    # Phase 11 §3.3 新管线
    # ================================================================

    def aggregate(
        self,
        market_state: MarketStateEnum,
        strategy_factors: dict[str, pd.DataFrame],
        snapshot: MarketSnapshot,
        weights_runtime: dict[str, float],
        weights_source: str,
        orthogonalize_order: list[str],
        hysteresis_status: str,
        single_strategy_mode: bool = False,
    ) -> list[CompositeScore]:
        """5 步管线：Winsorize / 中性化 / Z-score / Gram-Schmidt + 再标准化 / 三层输出。

        Args:
            market_state: 当日市场状态（决定信号触发阈值，本方法不消费）。
            strategy_factors: ``{strategy_name: DataFrame[ts_code × factor_name]}``
                由 ScoringService 调每个策略的 ``compute_strategy_factors`` 收集。
            snapshot: 含 industry / market_cap / beta 字段（Phase 11 §3.0 扩展）。
            weights_runtime: ``{strategy: weight}``，Σw=1。
            weights_source: ``"icir"`` / ``"default_matrix"`` / ``"user_override"``。
            orthogonalize_order: ICIR 降序排列的策略名（4a 正交化顺序）。
            hysteresis_status: ``"stable"`` / ``"pending_switch"``。
            single_strategy_mode: True 时跳过 Step 4/5（单策略回测 SDD §7.1 Q11）。

        Returns:
            ``list[CompositeScore]``，仅含有效行（composite_z 非 NaN）。
        """
        if not strategy_factors:
            return []

        industry = snapshot.get("industry") or {}
        market_cap = snapshot.get("market_cap")
        beta = snapshot.get("beta")

        # --- Step 1~3：策略内逐列 Winsorize → Neutralize → Zscore，再列向平均得 strategy_z ---
        strategy_z_cols: dict[str, pd.Series] = {}
        for s_name, df in strategy_factors.items():
            if df is None or df.empty:
                continue
            # 全 NaN raw 输入 → 跳过该策略：zscore 兜底返回全 0，但策略未提供任何信息，
            # 不应该误判为"composite_z=0 = 中性信号"
            if df.isna().all().all():
                continue
            col_zs: list[pd.Series] = []
            for col in df.columns:
                raw = df[col].astype(float)
                z = self._pipeline.run_steps_1_to_3(raw, industry, market_cap, beta)
                col_zs.append(z.rename(col))
            if not col_zs:
                continue
            # 策略内多因子合成：列向均值（V1.0 简化；P11-A2 §3.0.1 透传 V1.5+ 替换为
            # 策略内 ICIR 加权 / PCA 降维）。skipna=True，至少 1 个因子有值即纳入。
            z_df = pd.concat(col_zs, axis=1)
            strategy_z = z_df.mean(axis=1, skipna=True)
            # 全 NaN 行剔除
            strategy_z = strategy_z.dropna()
            if strategy_z.empty:
                continue
            # v1.3 修订（Barra Robust Z-score 标准流程）：策略内合成后再做
            # standardize + clip 到 ±3.5σ。
            # 起因：5y 真机 2026-05-12 momentum 因子 NaN 率 33%，部分股票只有 1
            # 个有效因子参与 mean → strategy_z max=11.24（理论应 ≤ 3.5）。下游
            # Gram-Schmidt + renormalize 会把该 outlier 放大到 z_normalized=23.7，
            # 主导 composite_z=15+ 顶端，让顶分 100 不可区分排序。
            # 修复：(a) 再 standardize 让 strategy_z 严格 std=1（K 因子合成会让
            # std=1/√K，且 NaN 多时进一步缩小）；(b) clip ±3.5σ（顶端 < top
            # 0.05%）兜底防极端 outlier 漏网。
            mean_v = float(strategy_z.mean())
            std_v = float(strategy_z.std())
            if std_v > 1e-12 and not math.isnan(std_v):
                strategy_z = (strategy_z - mean_v) / std_v
            strategy_z = strategy_z.clip(lower=-3.5, upper=3.5)
            strategy_z_cols[s_name] = strategy_z

        if not strategy_z_cols:
            return []

        # 对齐 universe：取所有 strategy 出现过的 ts_code 并集
        all_codes: set = set()
        for s in strategy_z_cols.values():
            all_codes |= set(s.index)
        index = pd.Index(sorted(all_codes), name="ts_code")
        strategy_z_matrix = pd.DataFrame(
            {s_name: s.reindex(index) for s_name, s in strategy_z_cols.items()},
            index=index,
        )

        # --- Step 4a + 4b：Gram-Schmidt 正交化 + 残差再标准化（单策略模式跳过）---
        active_strategies = list(strategy_z_matrix.columns)
        if single_strategy_mode or len(active_strategies) <= 1:
            # 单策略：直接把 strategy_z 当作 z_normalized
            orthogonal_matrix = strategy_z_matrix.copy()
            orthogonal_matrix.columns = [f"{c}_normalized" for c in orthogonal_matrix.columns]
            effective_order = active_strategies
        else:
            effective_order = [s for s in orthogonalize_order if s in active_strategies]
            # 兜底：order 缺失的策略按 weights_runtime 降序补齐
            missing = [s for s in active_strategies if s not in effective_order]
            missing.sort(key=lambda s: weights_runtime.get(s, 0.0), reverse=True)
            effective_order.extend(missing)
            orthogonal_matrix = self._orthogonalizer.compute(strategy_z_matrix, effective_order)

        # --- Step 5：加权 + 方差归一化 + 三层输出 ---
        # 仅保留 weights_runtime 中权重 > 0 且在 active_strategies 中的策略
        valid_weights = {
            s: float(w)
            for s, w in weights_runtime.items()
            if s in active_strategies and float(w) > 0.0
        }
        w_total = sum(valid_weights.values())
        if w_total <= 0:
            return []
        valid_weights = {s: w / w_total for s, w in valid_weights.items()}

        weighted_z = pd.Series(0.0, index=index, dtype=float)
        w_sq_sum = 0.0
        for s_name, w in valid_weights.items():
            norm_col = f"{s_name}_normalized"
            if norm_col not in orthogonal_matrix.columns:
                continue
            z_col = orthogonal_matrix[norm_col]
            # collinear / NaN → 该策略对该 ts_code 贡献 0；不传染 composite
            weighted_z = weighted_z.add(z_col.fillna(0.0) * w, fill_value=0.0)
            w_sq_sum += w * w

        if w_sq_sum <= 0:
            return []
        composite_z = weighted_z / math.sqrt(w_sq_sum)

        # 剔除所有策略都缺值的行（strategy_z_matrix 全列 NaN → composite_z 视为无效）
        any_valid = strategy_z_matrix[list(valid_weights)].notna().any(axis=1)
        composite_z = composite_z[any_valid]
        if composite_z.empty:
            return []

        # 层 2：composite_pct_in_market = rank_descending / N（越小越好；top 0.5% → 0.005）
        pct_in_market = composite_z.rank(pct=True, ascending=False, method="average")
        # 层 3：composite_score = Φ(z) × 100
        composite_score = pd.Series(
            norm.cdf(composite_z.to_numpy()) * 100, index=composite_z.index
        )

        # 策略级 Φ(strategy_z) × 100，对应旧 candidate_pool 4 列写入（§3.3 P1-2 兼容）
        scalar_per_strategy: dict[str, pd.Series] = {}
        for s_name in _STRATEGY_KEYS:
            if s_name in strategy_z_matrix.columns:
                col = strategy_z_matrix[s_name]
                scalar_per_strategy[s_name] = pd.Series(
                    norm.cdf(col.fillna(0.0).to_numpy()) * 100, index=col.index,
                ).where(col.notna(), None)

        results: list[CompositeScore] = []
        for ts_code in composite_z.index:
            z_value = float(composite_z.loc[ts_code])
            pct_value = float(pct_in_market.loc[ts_code])

            # raw breakdown：z_raw × weight
            breakdown_raw: dict[str, dict] = {}
            for s_name, w in valid_weights.items():
                z_raw = strategy_z_matrix.loc[ts_code, s_name]
                if pd.isna(z_raw):
                    continue
                breakdown_raw[s_name] = {
                    "z_raw": float(z_raw),
                    "weight": float(w),
                    "contribution": float(z_raw) * float(w),
                }

            # residual breakdown：z_orthogonal_normalized × weight / sqrt(Σw²)
            breakdown_residual: dict[str, dict] = {}
            for s_name, w in valid_weights.items():
                norm_col = f"{s_name}_normalized"
                if norm_col not in orthogonal_matrix.columns:
                    continue
                z_orth_val = orthogonal_matrix.loc[ts_code, norm_col]
                z_orth = 0.0 if pd.isna(z_orth_val) else float(z_orth_val)
                breakdown_residual[s_name] = {
                    "z_orthogonal_normalized": z_orth,
                    "weight": float(w),
                    "contribution": z_orth * float(w) / math.sqrt(w_sq_sum),
                }

            # 兼容旧四标量字段
            def _scalar(s_name: str) -> float | None:
                series = scalar_per_strategy.get(s_name)
                if series is None or ts_code not in series.index:
                    return None
                v = series.loc[ts_code]
                return None if v is None or (isinstance(v, float) and pd.isna(v)) else float(v)

            # explanation
            top_sorted = sorted(
                breakdown_raw.items(),
                key=lambda kv: kv[1]["contribution"],
                reverse=True,
            )[:2]
            strength = "强买入信号" if pct_value <= 0.01 else "买入信号"
            if top_sorted:
                top_names = " · ".join(name for name, _ in top_sorted)
                explanation = (
                    f"该股票位列全市场 top {pct_value * 100:.1f}%（{strength}），"
                    f"主要驱动：{top_names}。"
                )
            else:
                explanation = f"该股票位列全市场 top {pct_value * 100:.1f}%（{strength}）。"

            results.append(CompositeScore(
                ts_code=str(ts_code),
                composite_score=float(composite_score.loc[ts_code]),
                trend_score=_scalar("trend"),
                momentum_score=_scalar("momentum"),
                reversion_score=_scalar("mean_reversion"),
                value_score=_scalar("value"),
                market_state=market_state,
                score_breakdown=breakdown_raw,  # 兼容旧字段：等于 breakdown_raw
                explanation=explanation,
                composite_z=z_value,
                composite_pct_in_market=pct_value,
                score_breakdown_raw=breakdown_raw,
                score_breakdown_residual=breakdown_residual,
                weights_source=weights_source,
                hysteresis_status=hysteresis_status,
            ))

        return results

    # ================================================================
    # 旧 Phase 4 权重矩阵聚合（冷启动 / 单策略回测 fallback；BacktestEngine 直调）
    # ================================================================

    def aggregate_legacy(
        self,
        market_state: MarketStateEnum,
        strategy_scores: dict[str, list[StrategyScore]],
    ) -> list[CompositeScore]:
        """Phase 4 旧路径：按市场状态权重矩阵 + StrategyScore.score 0-100 加权。

        保留供：
        - BacktestEngine 主循环（P11-D 切换到 ScoringService.score_universe 前的暂存路径）
        - ScoringService.run_daily_scoring 冷启动 fallback
        - 单元测试 SCR-01~05（Phase 4 验收契约）

        Phase 11 新字段（composite_z / score_breakdown_raw 等）在此路径全部填 None，
        weights_source 标记为 ``"legacy_phase4"``。
        """
        base_weights = self._matrix()[market_state]
        active_keys = [
            k for k in base_weights if k in strategy_scores and strategy_scores[k]
        ]
        if not active_keys:
            return []

        raw_total = sum(base_weights[k] for k in active_keys)
        norm_weights = {k: base_weights[k] / raw_total for k in active_keys}

        per_strategy: dict[str, dict[str, float]] = {}
        per_strategy_reason: dict[str, dict[str, str]] = {}
        all_codes: set[str] = set()
        for key in active_keys:
            per_strategy[key] = {s.ts_code: s.score for s in strategy_scores[key]}
            per_strategy_reason[key] = {s.ts_code: s.reason for s in strategy_scores[key]}
            all_codes |= per_strategy[key].keys()

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

            trend_score = per_strategy.get("trend", {}).get(ts_code)
            momentum_score = per_strategy.get("momentum", {}).get(ts_code)
            reversion_score = per_strategy.get("mean_reversion", {}).get(ts_code)
            value_score = per_strategy.get("value", {}).get(ts_code)

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
                # Phase 11 新字段在 legacy 路径全部为 None / 默认
                composite_z=None,
                composite_pct_in_market=None,
                score_breakdown_raw=None,
                score_breakdown_residual=None,
                weights_source="legacy_phase4",
                hysteresis_status="stable",
            ))

        return results
