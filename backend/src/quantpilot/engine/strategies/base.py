"""BaseStrategy ABC、StrategyScore dataclass、MarketSnapshot TypedDict（Phase 4）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, TypedDict

import pandas as pd

if TYPE_CHECKING:
    pass


class MarketSnapshot(TypedDict):
    """由 ScoringService 构建后只读传入各策略。"""

    trade_date: date
    adj_prices: pd.DataFrame       # index=ts_code，columns=trade_date，后复权收盘价（近180日历天）
    daily_quotes: pd.DataFrame     # index=ts_code，最新一日行情（含 pe_ttm/pb/amount/vol/limit_up）
    financials: pd.DataFrame       # index=ts_code，最新一期财务数据（PIT）
    pe_pb_history: pd.DataFrame    # index=(ts_code, trade_date)，universe 过滤后近5年 pe_ttm/pb
    # index=index_code，columns=trade_date，Wide 格式（与 adj_prices 结构一致）
    index_adj_prices: pd.DataFrame


@dataclass(frozen=True)
class StrategyScore:
    ts_code: str
    raw_factors: dict[str, float]  # 原始因子值（数据血缘/归因用）
    score: float                   # 0–100，横截面百分位归一化
    reason: str                    # 可读解释（面向用户）


class BaseStrategy(ABC):
    """所有策略的抽象基类。子类须定义 name / display_name / weights。"""

    name: str
    display_name: str
    weights: dict[str, float]      # 策略内因子权重，须 sum(weights.values()) == 1.0

    @abstractmethod
    def compute_raw_factors(
        self,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        """
        计算原始因子值。
        - index=ts_code，列=各因子名
        - 纯函数，禁止修改 market_data 内任何 DataFrame
        - 无法计算的标的返回 NaN（横截面 rank 时自动排除）
        """

    def score(
        self,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> list[StrategyScore]:
        """
        完整评分流程（由 ScoringService 通过 asyncio.to_thread 并发调用）：
        1. compute_raw_factors() → raw（DataFrame）
        2. 横截面 Rank 百分位归一化：raw.rank(pct=True) * 100，∈[0,100]
        3. 策略内加权：(normalized * pd.Series(self.weights)).sum(axis=1)
        4. 逐行构建 StrategyScore（含 reason 文本）
        子类可在此方法末尾施加额外约束（追高剔除、价值陷阱截断等）。
        """
        raw = self.compute_raw_factors(universe, market_data)
        raw = raw.reindex(universe)                        # 对齐宇宙
        raw = raw.astype(float)                            # Decimal → float

        # 横截面百分位归一化
        normalized = raw.rank(pct=True) * 100              # ∈[0,100]

        # 策略内加权求和（仅对本策略拥有权重的因子列）
        weight_series = pd.Series(self.weights)
        available_cols = [c for c in weight_series.index if c in normalized.columns]
        if not available_cols:
            return []

        # 排除全 NaN 因子列（例如 TD 未修复导致某因子所有股票均为 NaN）
        available_cols = [c for c in available_cols if not normalized[c].isna().all()]
        if not available_cols:
            return []
        # 按比例归一化缺失因子的权重
        active_weights = weight_series[available_cols]
        active_weights = active_weights / active_weights.sum()
        composite = (normalized[available_cols] * active_weights).sum(axis=1, skipna=False)

        result: list[StrategyScore] = []
        for ts_code in composite.index:
            if pd.isna(composite[ts_code]):
                continue
            raw_row = raw.loc[ts_code]
            final_score = float(composite[ts_code])
            raw_factors = {
                k: float(v) for k, v in raw_row.items() if not pd.isna(v)
            }
            result.append(StrategyScore(
                ts_code=str(ts_code),
                raw_factors=raw_factors,
                score=final_score,
                reason=self._build_reason(str(ts_code), raw_row, final_score),
            ))
        return result

    @abstractmethod
    def _build_reason(
        self,
        ts_code: str,
        raw_row: pd.Series,
        final_score: float,
    ) -> str: ...
