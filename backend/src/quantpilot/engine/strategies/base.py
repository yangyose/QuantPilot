"""BaseStrategy ABC、StrategyScore dataclass、MarketSnapshot TypedDict（Phase 4）。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, TypedDict

import pandas as pd

if TYPE_CHECKING:
    pass


class MarketSnapshot(TypedDict, total=False):
    """由 ScoringService 构建后只读传入各策略。

    Phase 11 §3.0 P0-3 扩展：industry / market_cap / beta 三个新字段，给 5 步管线
    Step 2（行业 + 市值中性化）使用。旧路径（aggregate_legacy / 各策略 score()）
    不消费这些字段。``total=False`` 允许冷启动 / 单元测试场景不全量构造。
    """

    trade_date: date
    adj_prices: pd.DataFrame       # index=ts_code，columns=trade_date，后复权收盘价（近180日历天）
    daily_quotes: pd.DataFrame     # index=ts_code，最新一日行情（含 pe_ttm/pb/amount/vol/limit_up）
    financials: pd.DataFrame       # index=ts_code，最新一期财务数据（PIT）
    pe_pb_history: pd.DataFrame    # index=(ts_code, trade_date)，universe 过滤后近5年 pe_ttm/pb
    # index=index_code，columns=trade_date，Wide 格式（与 adj_prices 结构一致）
    index_adj_prices: pd.DataFrame

    # === Phase 11 §3.0 P0-3 新增字段 ===
    industry: dict[str, str]            # ts_code -> 行业代码（来自 StockInfo.sw_industry_l1）
    market_cap: pd.Series | None        # index=ts_code，float_mkt_cap PIT 切片；neutralize 时取 log
    beta: pd.Series | None              # V1.0 永远 None（NEUTRALIZE_BETA=false）；Phase 12+ 实现


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

    def compute_strategy_factors(
        self,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        """Phase 11 §3.0.1 P0-4：5 步管线 raw 因子矩阵入口。

        默认实现透传 ``compute_raw_factors``——子类无需覆写。V1.5+ 策略可能在
        ``compute_raw_factors`` 之上做降维 / 多周期合成 / PCA 等中间产物（如 MA
        系列合成主成分、PE/PB 合成 value_composite 等）作为 5 步管线入口，此时
        重写本方法不影响 ``compute_raw_factors``（后者继续用于 ``_build_reason``
        L1 文本生成 / 冷启动 score() 路径）。
        """
        return self.compute_raw_factors(universe, market_data)

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
