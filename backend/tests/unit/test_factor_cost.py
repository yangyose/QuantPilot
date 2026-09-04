"""K-5：换手成本拖累（K-COST）。V1.5-K §1.2 / §2.4。

## 本文件最重要的一条：`1 − J` **不是**换手率

K-4 存的是 Jaccard 相似度 `J = |A∩B| / |A∪B|`。直觉上「换手率 = 1 − J」，**但这是错的**：

设两天头部各 n 只、交集 k 只，
- 真实换手率（被换掉的持仓占比）= `(n − k) / n`
- `1 − J` = `2(n − k) / (2n − k)`

n=5、k=3 时：真实换手 `0.4`，而 `1 − J ≈ 0.571`——**高估近一倍**。
（两者关系是 `turnover = (1 − J) / (1 + J)`，仅在两天头部等长时成立。）

成本拖累若按 `1 − J` 算，会系统性夸大交易成本，进而**把本来能覆盖成本的因子误判为
不能覆盖**——这正是 K 主题要避免的那类错误结论。故 K-5 直接从头部集合算，
不吃 J 这个有损摘要。`TestNotOneMinusJaccard` 专门钉死这一点。

## 成本口径复用回测参数（§2.4）

买入 `price × (1 + c + sl)`、卖出 `price × (1 − c − st − sl)`（`backtest/engine.py`），
故换掉一个持仓的往返成本率 = `2c + st + 2sl`。
默认 c=0.025% / st=0.05% / sl=0.1% → **0.3%**。

K 另定一套的话，同一个「成本」会在回测与因子验证里得出不同结论——
两处口径分叉正是本仓反复付过代价的形态。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantpilot.engine.diagnostics.factor_cost import (
    ROUND_TRIP_RATE,
    CostParams,
    compute_cost_drag,
)
from quantpilot.engine.diagnostics.factor_turnover import compute_turnover_jaccard


def _factor(vals, codes=None) -> pd.Series:
    vals = list(vals)
    idx = codes or [f"{i:06d}.SZ" for i in range(len(vals))]
    return pd.Series(vals, index=idx, dtype=float)


def _fv(s: pd.Series) -> dict[str, dict[str, pd.Series]]:
    return {"momentum": {"rs_6m": s}}


def _half_overlap() -> tuple[pd.Series, pd.Series]:
    """100 只、两天头部各 5 只、交集 3 只（与 K-4 用例同一构造）。"""
    prev = _factor(np.arange(100, dtype=float))
    vals = np.arange(100, dtype=float)
    vals[95] = -2.0
    vals[96] = -1.0
    vals[0] = 200.0
    vals[1] = 201.0
    return prev, _factor(vals)


class TestCostRate:
    def test_round_trip_rate_matches_backtest_formula(self) -> None:
        """往返率 = 2c + st + 2sl，与 `backtest/engine.py` 的买卖价公式一致。

        默认 0.00025×2 + 0.0005 + 0.001×2 = 0.003。
        """
        p = CostParams()
        assert p.round_trip_rate == pytest.approx(
            2 * p.commission_rate + p.stamp_tax_rate + 2 * p.slippage_rate
        )
        assert p.round_trip_rate == pytest.approx(0.003)
        assert ROUND_TRIP_RATE == pytest.approx(0.003)

    def test_defaults_match_config_defaults(self) -> None:
        """默认值必须与 `config_defaults` 一致——两处分叉会让回测与因子验证结论不同。"""
        from quantpilot.core.config_defaults import DEFAULT_BACKTEST_DEFAULTS

        p = CostParams()
        assert p.commission_rate == pytest.approx(DEFAULT_BACKTEST_DEFAULTS.commission_rate)
        assert p.stamp_tax_rate == pytest.approx(DEFAULT_BACKTEST_DEFAULTS.stamp_tax_rate)
        assert p.slippage_rate == pytest.approx(DEFAULT_BACKTEST_DEFAULTS.slippage_rate)


class TestCostDrag:
    def test_analytic_half_overlap(self) -> None:
        """头部 5 只换掉 2 只 → 换手 2/5 = 0.4 → 拖累 0.4 × 0.003 = 0.0012。"""
        prev, curr = _half_overlap()
        pts = compute_cost_drag(_fv(prev), _fv(curr), stage="raw")
        assert len(pts) == 1
        p = pts[0]
        assert p.value == pytest.approx(0.4 * 0.003)
        assert p.metric == "cost_drag"
        assert p.horizon == 0
        assert p.bucket == -1
        assert p.sample_size == 5, "sample_size 是头部只数（换手率的分母）"

    def test_no_turnover_is_zero_cost(self) -> None:
        f = _factor(np.arange(100, dtype=float))
        assert compute_cost_drag(_fv(f), _fv(f), stage="raw")[0].value == pytest.approx(0.0)

    def test_full_turnover_is_full_round_trip(self) -> None:
        """头部全换 → 换手 1.0 → 拖累恰为往返率。"""
        prev = _factor(np.arange(100, dtype=float))
        curr = _factor(np.arange(100, dtype=float)[::-1])
        assert compute_cost_drag(_fv(prev), _fv(curr), stage="raw")[0].value == pytest.approx(
            0.003
        )

    def test_value_is_positive_magnitude(self) -> None:
        """存正数量级，语义是「从收益中**扣减**」。

        与 K-4 的方向问题同族：存成负数还是正数不写死，下游迟早会多减或少减一次，
        而那不会报错。
        """
        prev, curr = _half_overlap()
        assert compute_cost_drag(_fv(prev), _fv(curr), stage="raw")[0].value > 0


class TestNotOneMinusJaccard:
    """⚠️ 本主题最易犯的错：拿 `1 − J` 当换手率。"""

    def test_cost_differs_from_naive_one_minus_jaccard(self) -> None:
        """同一构造下，正确值 0.0012，而 `(1−J)×rate` 会得 ≈0.001714——高估 43%。

        若实现改用 `1 − J`，本条立刻红。
        """
        prev, curr = _half_overlap()
        j = compute_turnover_jaccard(_fv(prev), _fv(curr), stage="raw")[0].value
        naive = (1.0 - j) * 0.003
        got = compute_cost_drag(_fv(prev), _fv(curr), stage="raw")[0].value

        assert j == pytest.approx(3.0 / 7.0)
        assert naive == pytest.approx((4.0 / 7.0) * 0.003)
        assert got == pytest.approx(0.4 * 0.003)
        assert got != pytest.approx(naive), "拿 1−J 当换手率会高估成本"

    def test_relation_holds_when_heads_equal_length(self) -> None:
        """两天头部等长时 `turnover == (1−J)/(1+J)`——把两者的关系钉成文档。"""
        prev, curr = _half_overlap()
        j = compute_turnover_jaccard(_fv(prev), _fv(curr), stage="raw")[0].value
        got = compute_cost_drag(_fv(prev), _fv(curr), stage="raw")[0].value
        assert got / 0.003 == pytest.approx((1 - j) / (1 + j))


class TestParamsConsumed:
    def test_changing_cost_params_changes_result(self) -> None:
        """§4.11「参数是否真被消费」：改成本参数 → 结果必须变。"""
        prev, curr = _half_overlap()
        base = compute_cost_drag(_fv(prev), _fv(curr), stage="raw")[0].value
        doubled = compute_cost_drag(
            _fv(prev), _fv(curr), stage="raw",
            cost=CostParams(commission_rate=0.0005, stamp_tax_rate=0.001,
                            slippage_rate=0.002),
        )[0].value
        assert doubled == pytest.approx(base * 2)

    def test_pct_flows_through(self) -> None:
        f = _factor(np.arange(200, dtype=float))
        assert compute_cost_drag(_fv(f), _fv(f), stage="raw", pct=0.10)[0].sample_size == 20


class TestDegenerate:
    def test_head_below_one_name_skipped(self) -> None:
        f = _factor(np.arange(19, dtype=float))
        assert compute_cost_drag(_fv(f), _fv(f), stage="raw", min_xs=5) == []

    def test_factor_absent_on_one_day_skipped(self) -> None:
        f = _factor(np.arange(100, dtype=float))
        assert compute_cost_drag(
            {"momentum": {"rs_6m": f}}, {"momentum": {"other": f}}, stage="raw"
        ) == []

    def test_no_dispersion_skipped(self) -> None:
        flat = _factor([1.0] * 100)
        assert compute_cost_drag(_fv(flat), _fv(flat), stage="raw") == []
