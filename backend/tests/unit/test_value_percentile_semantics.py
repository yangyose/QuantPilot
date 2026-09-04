"""ValueStrategy 历史分位的**数值语义**特征测试（V1.5-K 性能改造前置）。

## 为什么单开一个文件

`get_pe_pb_history_bulk` 为算约 3212 个分位数要从 `financial_data` 拉约 380 万行，
是每日管线内存峰值的主项（2026-09-03 生产 OOM 的主因，见 `docs/ops/deploy_log.md`）。
把这个计算下推到 SQL 能把行数降三个数量级——但 value 策略占 composite 权重 0.57~0.87，
**算错了会静默改变选股**，且不会有任何报错。

故先按 CLAUDE.md「对不熟悉的遗留代码，先写特征测试再改行为」把现有语义钉死。
`test_engine_degraded_branches.py` 已覆盖**退化分支**（空历史 / 缺列 / curr 为 NaN /
code 不在历史 / 该 code 历史全 NaN），本文件补的是它没覆盖的**数值语义**——
恰恰是重写最容易改错的三条：

| # | 语义 | 写错成什么样也不会报错 |
|---|---|---|
| 1 | 严格 `<`（不是 `<=`）| SQL 里顺手写 `<=`，等值样本的分位整体偏移 |
| 2 | 分母 = `dropna()` **之后**的条数 | SQL `count(*)` 含 NULL → 分母偏大、分位系统性偏低 |
| 3 | `inverse=True` 返回 `1 - pct_rank` | 方向反转 → 低估变高估，选股完全颠倒 |

每条都给可手算的解析值，任何一处偏移都会在数值上露馅。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from quantpilot.engine.strategies.value import _compute_historical_percentile


def _hist(values: list[float], code: str = "A.SZ", col: str = "pe_ttm") -> pd.DataFrame:
    mi = pd.MultiIndex.from_tuples(
        [(code, date(2025, 1, 1) + timedelta(days=i)) for i in range(len(values))],
        names=["ts_code", "trade_date"],
    )
    return pd.DataFrame({col: values}, index=mi)


def _pct(values: list[float], curr: float, *, inverse: bool = True) -> float:
    universe = pd.Index(["A.SZ"], name="ts_code")
    return float(
        _compute_historical_percentile(
            universe, pd.Series([curr], index=universe), _hist(values), "pe_ttm",
            inverse=inverse,
        )["A.SZ"]
    )


class TestStrictLessThan:
    """语义 1：比较是**严格小于**，等值样本不计入分子。"""

    def test_current_equals_a_historical_value(self) -> None:
        """history=[5,10,15], curr=10 → 严格小于的只有 5 → 1/3 → inverse = 2/3。

        若误写 `<=`，分子变 2 → 2/3 → inverse = 1/3。两者数值不同，这条能分辨。
        """
        assert _pct([5.0, 10.0, 15.0], 10.0) == pytest.approx(2.0 / 3.0)

    def test_all_history_equal_to_current(self) -> None:
        """history 全等于 curr → 分子 0 → pct_rank 0 → inverse = 1.0（最高分）。

        ⚠️ 这是现行语义的一个**尖锐边界**：一只 PE 五年纹丝不动的股票会拿到
        「最低估」的满分。重写时若改成 `<=` 它会变成 0.0——**从满分翻到零分**。
        """
        assert _pct([7.0, 7.0, 7.0], 7.0) == 1.0


class TestDenominatorExcludesNaN:
    """语义 2：分母是 `dropna()` 之后的条数，不是原始行数。"""

    def test_nan_rows_leave_denominator(self) -> None:
        """history=[5,NaN,15], curr=10 → dropna→[5,15] → 1/2 → inverse = 0.5。

        若分母用了含 NaN 的原始行数 3 → 1/3 → inverse = 2/3。可分辨。
        """
        assert _pct([5.0, float("nan"), 15.0], 10.0) == 0.5

    def test_many_nans_do_not_dilute(self) -> None:
        """大量 NaN 也不该稀释分位——这正是 SQL `count(*)` vs `count(col)` 的差别。"""
        vals = [5.0, 15.0] + [float("nan")] * 98
        assert _pct(vals, 10.0) == 0.5


class TestInverseDirection:
    """语义 3：`inverse=True` 返回 `1 - pct_rank`，低估 → 高值 → rank 后高分。"""

    def test_cheapest_ever_scores_one(self) -> None:
        """curr 低于全部历史 → pct_rank 0 → inverse = 1.0。"""
        assert _pct([10.0, 20.0, 30.0], 1.0) == 1.0

    def test_most_expensive_ever_scores_zero(self) -> None:
        """curr 高于全部历史 → pct_rank 1 → inverse = 0.0。"""
        assert _pct([10.0, 20.0, 30.0], 99.0) == 0.0

    def test_inverse_false_returns_raw_rank(self) -> None:
        """两个方向必须互补为 1——若实现把 inverse 写死，这条红。"""
        vals = [10.0, 20.0, 30.0, 40.0]
        raw = _pct(vals, 25.0, inverse=False)
        inv = _pct(vals, 25.0, inverse=True)
        assert raw == 0.5
        assert inv == 0.5
        # 换一个不对称的点，避免 0.5 的自反性掩盖方向错误
        raw2 = _pct(vals, 15.0, inverse=False)
        inv2 = _pct(vals, 15.0, inverse=True)
        assert raw2 == 0.25
        assert inv2 == 0.75
        assert raw2 + inv2 == 1.0


class TestPerStockIsolation:
    """分位只跟**该股自己**的历史比，绝不跨股票。

    这条是下推 SQL 时最需要盯的——一个漏掉 `PARTITION BY ts_code` 的窗口函数
    会把全市场历史混在一起算，结果依然是 0~1 的合理数值、不会报错。
    """

    def test_other_stocks_history_does_not_leak(self) -> None:
        universe = pd.Index(["A.SZ", "B.SZ"], name="ts_code")
        tuples = (
            [("A.SZ", date(2025, 1, i + 1)) for i in range(3)]
            + [("B.SZ", date(2025, 1, i + 1)) for i in range(3)]
        )
        mi = pd.MultiIndex.from_tuples(tuples, names=["ts_code", "trade_date"])
        # A 的历史全在低位，B 的历史全在高位
        hist = pd.DataFrame(
            {"pe_ttm": [1.0, 2.0, 3.0, 100.0, 200.0, 300.0]}, index=mi
        )
        curr = pd.Series([2.5, 250.0], index=universe)
        out = _compute_historical_percentile(universe, curr, hist, "pe_ttm")
        # A: 严格小于 2.5 的有 1.0/2.0 → 2/3 → inverse 1/3
        assert out["A.SZ"] == pytest.approx(1.0 / 3.0)
        # B: 严格小于 250 的有 100/200 → 2/3 → inverse 1/3
        assert out["B.SZ"] == pytest.approx(1.0 / 3.0)
        # 若两股历史被混算，A 的 curr=2.5 在 6 个值里只小于 1.0 → 1/6 → inverse 5/6
        assert out["A.SZ"] != pytest.approx(5.0 / 6.0)
