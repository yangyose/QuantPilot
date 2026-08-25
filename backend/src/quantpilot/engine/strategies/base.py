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


# 默认所需交易日数：覆盖 TrendStrategy 的 MA60 warm-up（其 compute_raw_factors 对
# ``len(close) < 65`` 直接返回 NaN）与 MeanReversionStrategy 的 25 日下限。
# **窗口比这更深的策略必须覆写 required_history_days**——ScoringService 取全体
# 策略的最大值来决定价格窗口深度，漏报即静默全 NaN。
DEFAULT_REQUIRED_HISTORY_DAYS = 65


class BaseStrategy(ABC):
    """所有策略的抽象基类。子类须定义 name / display_name / weights。"""

    name: str
    display_name: str
    weights: dict[str, float]      # 策略内因子权重，须 sum(weights.values()) == 1.0

    @property
    def required_history_days(self) -> int:
        """本策略需要的 ``adj_prices`` 列数下限，单位是**交易日**（不是日历天）。

        V1.5-C C1-3 引入。此前 ScoringService 用 180 日历天 + 「≈ 120 交易日」的
        注释近似，实测只有 119 个交易日 → MomentumStrategy 的 ``rs_6m``（权重
        0.35）自 Initial commit 起在生产每一次评分中都是全 NaN，无任何告警。

        计数口径与 ``_period_return`` 一致：算 n 个交易日的收益要 **n + 1** 列
        （首尾两端各占一列）。覆写时记得 +1。
        """
        return DEFAULT_REQUIRED_HISTORY_DAYS

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

    def apply_constraints(
        self,
        raw: pd.DataFrame,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        """V1.5-C C1-1：策略硬约束的**唯一**落点，默认恒等返回。

        Phase 4 把追高剔除 / 价值陷阱截断等硬约束写在各策略的 ``score()`` 末尾，
        而 Phase 11 五步管线走 ``compute_strategy_factors`` **从不调用 score()**
        → 这些约束在生产全部失效（价值陷阱一条尤其严重：value 策略占 composite
        权重 0.57~0.87）。本钩子被两条路径共同调用，约束只写一处即处处生效，
        也是 C2 F-Score 门控的接入点。

        **约束必须在 raw 因子域表达**，不要用 0-100 分域的老写法——五步管线里
        因子随后要 Winsorize → 中性化 → Z-score，「置分 0 / 截断到 50」已无意义：

        - 「剔除」类 → 命中行该策略**所有因子列置 NaN**。语义 = 该股票不参与本
          策略，``Scorer`` 见 NaN 会把权重分给其余策略。**禁止置 0**——Z-score
          后 0 是横截面均值，置 0 等于发了张中性分而不是把它排除。
        - 「截断」类 → 命中行逐列 ``min(raw, raw.quantile(0.5))``，保持「上限为
          中位水平」的原始语义，且在 rank / Z-score 下不变形。

        实现须为纯函数：不得修改入参 ``raw`` 与 ``market_data``（返回副本）。
        """
        return raw

    def compute_strategy_factors(
        self,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> pd.DataFrame:
        """Phase 11 §3.0.1 P0-4：5 步管线 raw 因子矩阵入口。

        默认实现 = ``compute_raw_factors`` + ``apply_constraints``——子类无需覆写。
        V1.5+ 策略可能在 ``compute_raw_factors`` 之上做降维 / 多周期合成 / PCA 等
        中间产物（如 MA 系列合成主成分、PE/PB 合成 value_composite 等）作为 5 步
        管线入口，此时重写本方法不影响 ``compute_raw_factors``（后者继续用于
        ``_build_reason`` L1 文本生成 / 冷启动 score() 路径）——但**重写时必须自行
        调用 ``apply_constraints``**，否则该策略的硬约束会再次失效。
        """
        return self.apply_constraints(
            self.compute_raw_factors(universe, market_data), universe, market_data,
        )

    def score(
        self,
        universe: pd.Index,
        market_data: MarketSnapshot,
    ) -> list[StrategyScore]:
        """
        完整评分流程（由 ScoringService 通过 asyncio.to_thread 并发调用）：
        1. compute_raw_factors() → apply_constraints() → raw（DataFrame）
        2. 横截面 Rank 百分位归一化：raw.rank(pct=True) * 100，∈[0,100]
        3. 策略内加权：(normalized * pd.Series(self.weights)).sum(axis=1)
        4. 逐行构建 StrategyScore（含 reason 文本）

        V1.5-C C1-1 起硬约束统一由 ``apply_constraints`` 施加，与五步管线
        **同源**；子类不再覆写本方法追加约束。被「剔除」类约束命中的标的因子
        全为 NaN → composite 为 NaN → 不出现在返回列表中（改造前是 score=0.0
        仍留在列表里，属有意的语义收敛，见 apply_constraints docstring）。
        """
        raw = self.apply_constraints(
            self.compute_raw_factors(universe, market_data), universe, market_data,
        )
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
