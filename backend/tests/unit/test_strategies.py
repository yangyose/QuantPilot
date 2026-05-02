"""STR-01~05: BaseStrategy 通用行为单元测试（纯函数，无 DB）。"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from quantpilot.engine.strategies.base import BaseStrategy, MarketSnapshot

# ── 测试用具体策略（最小实现，只验证 BaseStrategy 框架行为）──────────────────────────

class _SimpleStrategy(BaseStrategy):
    """单因子策略，权重 = 1.0，用于验证框架行为。"""

    name = "simple"
    display_name = "简单测试策略"
    weights = {"factor_a": 1.0}

    def compute_raw_factors(
        self, universe: pd.Index, market_data: MarketSnapshot
    ) -> pd.DataFrame:
        """从 market_data["daily_quotes"]["close"] 直接取值作为因子。"""
        close = market_data["daily_quotes"]["close"].reindex(universe)
        return pd.DataFrame({"factor_a": close}, index=universe)

    def _build_reason(self, ts_code: str, raw_row: pd.Series, final_score: float) -> str:
        return f"factor_a={raw_row.get('factor_a', float('nan')):.2f}, score={final_score:.1f}"


class _TwoFactorStrategy(BaseStrategy):
    """双因子策略，factor_a 60% + factor_b 40%。"""

    name = "two_factor"
    display_name = "双因子测试策略"
    weights = {"factor_a": 0.6, "factor_b": 0.4}

    def compute_raw_factors(
        self, universe: pd.Index, market_data: MarketSnapshot
    ) -> pd.DataFrame:
        quotes = market_data["daily_quotes"].reindex(universe)
        return pd.DataFrame(
            {"factor_a": quotes["close"], "factor_b": quotes["close"] * 0.9},
            index=universe,
        )

    def _build_reason(self, ts_code: str, raw_row: pd.Series, final_score: float) -> str:
        return f"score={final_score:.1f}"


def _make_snapshot(
    codes: list[str],
    close_values: list[float] | None = None,
) -> MarketSnapshot:
    """构建最小 MarketSnapshot，仅填充测试需要的字段。"""
    if close_values is None:
        close_values = [float(i + 1) * 10 for i in range(len(codes))]
    idx = pd.Index(codes, name="ts_code")
    trade_date = date(2025, 1, 31)
    dates = [trade_date - timedelta(days=i) for i in range(60)]

    snapshot: MarketSnapshot = {
        "trade_date": trade_date,
        "adj_prices": pd.DataFrame(
            {d: close_values for d in dates}, index=idx
        ),
        "daily_quotes": pd.DataFrame(
            {"close": close_values, "pe_ttm": [20.0] * len(codes), "pb": [2.0] * len(codes)},
            index=idx,
        ),
        "financials": pd.DataFrame(
            {"roe": [10.0] * len(codes), "net_profit_yoy": [5.0] * len(codes)},
            index=idx,
        ),
        "pe_pb_history": pd.DataFrame(),
        "index_adj_prices": pd.DataFrame(),
    }
    return snapshot


# ── STR-01：横截面百分位边界 ───────────────────────────────────────────────────

def test_str_01_uniform_factors_score_equal() -> None:
    """全市场相同因子值 → 横截面百分位无差异 → 所有标的得分相同。
    注：rank(pct=True) 对 n 个相同值返回 (n+1)/(2n)，n=5 时约 0.6，得分≈60（非50）。
    关键语义是"无差异"，不是具体值。
    """
    codes = ["A", "B", "C", "D", "E"]
    snapshot = _make_snapshot(codes, close_values=[100.0] * 5)
    strategy = _SimpleStrategy()

    results = strategy.score(pd.Index(codes), snapshot)

    assert len(results) == 5
    scores = [r.score for r in results]
    assert len(set(scores)) == 1, f"期望所有得分相同，实际: {scores}"
    assert 0 <= scores[0] <= 100


# ── STR-02：极端离群值不影响其余标的 ─────────────────────────────────────────────

def test_str_02_outlier_does_not_distort_others() -> None:
    """一只标的因子值极大时，其余标的得分应接近正常分布，不压缩到0。"""
    codes = ["A", "B", "C", "D", "E"]
    # A 极端大，B~E 正常
    snapshot = _make_snapshot(codes, close_values=[1e6, 10.0, 20.0, 30.0, 40.0])
    strategy = _SimpleStrategy()

    results = strategy.score(pd.Index(codes), snapshot)

    assert len(results) == 5
    scores = {r.ts_code: r.score for r in results}
    # A 应得最高分（接近100）
    assert scores["A"] > 80
    # B~E 不应全部为0，应有差异
    other_scores = [scores[c] for c in ["B", "C", "D", "E"]]
    assert max(other_scores) > 0
    assert max(other_scores) - min(other_scores) > 5


# ── STR-03：全 NaN 标的被排除 ─────────────────────────────────────────────────

def test_str_03_all_nan_excluded() -> None:
    """compute_raw_factors 返回 NaN 的标的不出现在 score 结果中。"""
    codes = ["A", "B", "C"]
    snapshot = _make_snapshot(codes, close_values=[10.0, float("nan"), 30.0])
    strategy = _SimpleStrategy()

    results = strategy.score(pd.Index(codes), snapshot)

    ts_codes = {r.ts_code for r in results}
    assert "B" not in ts_codes      # NaN 因子被排除
    assert "A" in ts_codes
    assert "C" in ts_codes


# ── STR-04：weights 权重和为 1.0 ──────────────────────────────────────────────

def test_str_04_weights_sum_to_one() -> None:
    """所有策略的 weights 权重和必须为 1.0（±1e-9 容差）。"""
    for strategy_cls in [_SimpleStrategy, _TwoFactorStrategy]:
        s = strategy_cls()
        total = sum(s.weights.values())
        assert abs(total - 1.0) < 1e-9, (
            f"{strategy_cls.__name__}.weights 总和={total}，期望1.0"
        )


# ── STR-05：market_data 不被修改（只读约束）────────────────────────────────────

def test_str_05_market_data_readonly() -> None:
    """compute_raw_factors 执行后，market_data 内的 DataFrame 内容不变。"""
    codes = ["A", "B", "C"]
    snapshot = _make_snapshot(codes, close_values=[10.0, 20.0, 30.0])
    # 记录执行前的哈希（通过 copy 比较）
    original_close = snapshot["daily_quotes"]["close"].copy()
    original_adj = snapshot["adj_prices"].copy()

    strategy = _SimpleStrategy()
    strategy.score(pd.Index(codes), snapshot)

    pd.testing.assert_series_equal(snapshot["daily_quotes"]["close"], original_close)
    pd.testing.assert_frame_equal(snapshot["adj_prices"], original_adj)
