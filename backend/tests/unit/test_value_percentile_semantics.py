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


class TestPrecomputedPercentileOverride:
    """ValueStrategy 优先消费 Service 预计算的分位（SQL 下推产物）。

    ## 为什么保留回退而不是直接替换

    `pe_pb_history` 路径**回测引擎还在用**（`backtest/engine.py` 自建 MarketSnapshot
    并做 PIT 切片）。直接换掉契约会连带改回测，blast radius 远超这次性能修复的范围。
    故加可选键：Service 传预计算值走下推、回测不传则走原路，两条路**必须等价**——
    最后一条用例就是钉这个等价性的，它一红就说明两条路开始漂移了。
    """

    @staticmethod
    def _snapshot(hist_vals, curr, *, precomputed=None):
        universe = pd.Index(["A.SZ"], name="ts_code")
        snap = {
            "daily_quotes": pd.DataFrame({"pe_ttm": [curr], "pb": [curr]}, index=universe),
            "financials": pd.DataFrame({"roe": [10.0]}, index=universe),
            "pe_pb_history": _hist(hist_vals).assign(pb=lambda d: d["pe_ttm"]),
        }
        if precomputed is not None:
            snap["pe_percentile"] = pd.Series(precomputed, index=universe)
            snap["pb_percentile"] = pd.Series(precomputed, index=universe)
        return universe, snap

    def test_precomputed_is_used_verbatim(self) -> None:
        """给一个与历史算出来**明显不同**的值，输出必须等于它。

        若实现忽略了这个键（"接了但没生效"最常见的形态），输出会是 2/3 而非 0.123。
        """
        from quantpilot.engine.strategies.value import ValueStrategy

        universe, snap = self._snapshot([5.0, 10.0, 15.0], 10.0, precomputed=0.123)
        df = ValueStrategy().compute_raw_factors(universe, snap)  # type: ignore[arg-type]
        assert df.loc["A.SZ", "pe_percentile"] == pytest.approx(0.123)
        assert df.loc["A.SZ", "pb_percentile"] == pytest.approx(0.123)

    def test_falls_back_to_history_when_absent(self) -> None:
        """不传预计算值 → 走原 pe_pb_history 路径（回测就是这条）。"""
        from quantpilot.engine.strategies.value import ValueStrategy

        universe, snap = self._snapshot([5.0, 10.0, 15.0], 10.0)
        df = ValueStrategy().compute_raw_factors(universe, snap)  # type: ignore[arg-type]
        assert df.loc["A.SZ", "pe_percentile"] == pytest.approx(2.0 / 3.0)

    def test_two_paths_agree_on_same_data(self) -> None:
        """同一批数据下两条路必须给出相同结果——漂移守卫。"""
        from quantpilot.engine.strategies.value import ValueStrategy

        hist, curr = [5.0, 10.0, 15.0, 20.0], 12.0
        universe, snap_fallback = self._snapshot(hist, curr)
        via_history = ValueStrategy().compute_raw_factors(
            universe, snap_fallback  # type: ignore[arg-type]
        ).loc["A.SZ", "pe_percentile"]

        _, snap_pre = self._snapshot(hist, curr, precomputed=via_history)
        via_precomputed = ValueStrategy().compute_raw_factors(
            universe, snap_pre  # type: ignore[arg-type]
        ).loc["A.SZ", "pe_percentile"]
        assert via_precomputed == pytest.approx(via_history)


class TestServiceActuallyPushesDown:
    """在**调用点**上验证下推真的接上了（§4.11「调用点是否真传参」）。

    ⚠️ 判据必须查源码，不能"构造 spy 再调用它"——那是自证式的，缺陷仍在时照样绿。
    """

    @staticmethod
    def _calls(fn) -> set[str]:
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(fn).lstrip())
        return {
            n.func.attr for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }

    def test_snapshot_builder_uses_pushdown(self) -> None:
        from quantpilot.services.strategy_service import ScoringService

        assert "get_pe_pb_percentile_bulk" in self._calls(
            ScoringService._build_market_snapshot
        )

    def test_snapshot_builder_no_longer_loads_five_years_of_history(self) -> None:
        """这条才是省内存的**全部意义**。

        若有人"保险起见"把 `get_pe_pb_history_bulk` 加回去，分位仍然正确、
        测试仍然全绿，但那 380 万行又回到内存里——省内存的效果**静默蒸发**。
        没有任何功能测试会发现，只有这条会。
        """
        from quantpilot.services.strategy_service import ScoringService

        assert "get_pe_pb_history_bulk" not in self._calls(
            ScoringService._build_market_snapshot
        ), "每日管线不应再整批拉取 5 年 pe/pb 历史"
