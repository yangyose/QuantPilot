"""K-2：十分位组合收益阶梯 + top 5% 头部超额（V1.5-K §1.2）。

## 为什么要这两个指标

IC 是**横截面相关系数**，它回答「因子排序与收益排序有多一致」。但实盘不是按相关系数
交易的——是**买头部那一小撮**。一个 IC 显著为正的因子，完全可能其单调性全部来自
尾部（做空端），头部反而没有超额。K-2 把验证口径对齐到交易口径。

## ⚠️ 本文件的门槛：对照解析解，不是「不抛异常」

设计文档 §3 开宗明义——本主题产出的是「用来判断别的东西对不对」的度量，
**它自己错了没有更上层的尺子能发现**。故下列用例一律给可手算的解析值。

## 一条必须套用的既有教训

CLAUDE.md §4.4：**「取前 X%」不要用 `quantile(1-X)` 当阈值**——小样本上被线性插值
支配（21 只剔 2 只 = 9.5%、2 只剔 1 只 = 50%）。要用 `nlargest(int(n * pct))` 按名次取，
`floor` 让「不足 1 只」时不取任何标的。top 5% 正是这个场景，`TestTopFivePercentSizing`
专门钉它。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantpilot.engine.diagnostics.factor_portfolio import (
    compute_decile_forward_return,
    compute_top_pct_excess,
)


def _codes(n: int) -> list[str]:
    return [f"{i:06d}.SZ" for i in range(n)]


def _series(vals, codes=None) -> pd.Series:
    vals = list(vals)
    return pd.Series(vals, index=codes or _codes(len(vals)), dtype=float)


class TestDecileLadder:
    def test_monotone_factor_gives_monotone_ladder(self) -> None:
        """因子与前向收益完全同序 → 十档均值严格递增（这就是「阶梯」）。

        100 只、每档 10 只：第 k 档（1-indexed）装的是排名 [10(k-1), 10k) 的股票，
        收益 = 序号/100，故第 k 档均值 = (10(k-1) + 10k - 1)/2 /100 = (20k-11)/200。
        k=1 → 0.045，k=10 → 0.945。全部可手算。
        """
        n = 100
        factor = _series(np.arange(n, dtype=float))
        fwd = _series(np.arange(n, dtype=float) / 100.0)
        pts = compute_decile_forward_return(
            {"momentum": {"rs_6m": factor}}, fwd, stage="raw", horizon=20
        )
        assert len(pts) == 10
        by_bucket = {p.bucket: p for p in pts}
        assert sorted(by_bucket) == list(range(1, 11))
        for k in range(1, 11):
            assert by_bucket[k].value == pytest.approx((20 * k - 11) / 200)
            assert by_bucket[k].sample_size == 10
            assert by_bucket[k].metric == "decile_fwd_return"
            assert by_bucket[k].horizon == 20

    def test_bucket_1_is_lowest_factor_value(self) -> None:
        """口径固定：**bucket 1 = 因子值最低**，10 = 最高。

        ⚠️ 不假设「因子值越高越好」——raw 因子的方向各不相同，方向判断交给分析侧。
        写死这个口径是为了让跨因子、跨批次的结果可比；反过来定义同样自洽但必须二选一。
        """
        factor = _series(np.arange(100, dtype=float))
        fwd = _series(-np.arange(100, dtype=float) / 100.0)   # 反向收益
        pts = compute_decile_forward_return(
            {"s": {"f": factor}}, fwd, stage="raw", horizon=20
        )
        by = {p.bucket: p.value for p in pts}
        assert by[1] > by[10], "因子低档收益更高时，bucket 1 的值必须更大"

    def test_uneven_split_keeps_every_bucket_nonempty(self) -> None:
        """n 不被 10 整除时仍须 10 档、无空档，且总数守恒。"""
        n = 97
        pts = compute_decile_forward_return(
            {"s": {"f": _series(np.arange(n, dtype=float))}},
            _series(np.arange(n, dtype=float)),
            stage="raw", horizon=20,
        )
        sizes = [p.sample_size for p in pts]
        assert len(pts) == 10
        assert min(sizes) >= 1
        assert sum(sizes) == n
        assert max(sizes) - min(sizes) <= 1, "档位大小最多相差 1"

    def test_result_is_order_independent(self) -> None:
        """打乱输入行序不得改变结果——否则同一批数据两次跑出两个答案。

        并列值的分档若依赖输入顺序（如 rank(method='first')），面板重跑就不可重现。
        """
        n = 60
        vals = np.tile(np.arange(6, dtype=float), 10)   # 大量并列
        codes = _codes(n)
        f1 = pd.Series(vals, index=codes)
        r1 = pd.Series(np.arange(n, dtype=float), index=codes)
        perm = np.random.RandomState(0).permutation(n)
        f2 = f1.iloc[perm]
        r2 = r1.iloc[perm]

        a = compute_decile_forward_return({"s": {"f": f1}}, r1, stage="raw", horizon=20)
        b = compute_decile_forward_return({"s": {"f": f2}}, r2, stage="raw", horizon=20)
        assert [(p.bucket, p.value, p.sample_size) for p in a] == [
            (p.bucket, p.value, p.sample_size) for p in b
        ]


class TestDecileDegenerate:
    """按 §3 门槛 3：退化则**跳过不写占位行**（`valid_ratio` 那条例外不适用于本指标）。"""

    def test_below_min_is_skipped(self) -> None:
        pts = compute_decile_forward_return(
            {"s": {"f": _series(np.arange(9, dtype=float))}},
            _series(np.arange(9, dtype=float)),
            stage="raw", horizon=20, min_xs=20,
        )
        assert pts == []

    def test_fewer_than_ten_names_is_skipped(self) -> None:
        """不足 10 只就分不出十档——此时硬分会产出空档或语义混乱的档位。"""
        pts = compute_decile_forward_return(
            {"s": {"f": _series(np.arange(9, dtype=float))}},
            _series(np.arange(9, dtype=float)),
            stage="raw", horizon=20, min_xs=5,
        )
        assert pts == []

    def test_all_nan_factor_is_skipped(self) -> None:
        pts = compute_decile_forward_return(
            {"s": {"f": _series([np.nan] * 60)}},
            _series(np.arange(60, dtype=float)),
            stage="raw", horizon=20,
        )
        assert pts == []

    def test_partial_nan_uses_aligned_subset(self) -> None:
        """NaN 行剔除后再分档，sample_size 之和 = 对齐后的有效数。"""
        f = _series(np.arange(100, dtype=float))
        f.iloc[:30] = np.nan
        pts = compute_decile_forward_return(
            {"s": {"f": f}}, _series(np.arange(100, dtype=float)),
            stage="raw", horizon=20,
        )
        assert sum(p.sample_size for p in pts) == 70


class TestTopPctExcess:
    def test_analytic_excess(self) -> None:
        """100 只、top 5% = 5 只（序号 95..99），收益 = 序号/100。

        头部均值 = (95+96+97+98+99)/5/100 = 0.97
        全体均值 = (0+..+99)/100/100 = 0.495
        超额 = 0.475（可手算）。
        """
        factor = _series(np.arange(100, dtype=float))
        fwd = _series(np.arange(100, dtype=float) / 100.0)
        pts = compute_top_pct_excess(
            {"s": {"f": factor}}, fwd, stage="raw", horizon=20
        )
        assert len(pts) == 1
        p = pts[0]
        assert p.value == pytest.approx(0.475)
        assert p.metric == "top5_excess"
        assert p.bucket == -1, "头部超额无十分位概念 → bucket 填 -1"
        assert p.sample_size == 5, "sample_size 记的是头部只数"

    def test_zero_excess_when_returns_flat(self) -> None:
        pts = compute_top_pct_excess(
            {"s": {"f": _series(np.arange(100, dtype=float))}},
            _series([0.03] * 100), stage="raw", horizon=20,
        )
        assert pts[0].value == pytest.approx(0.0)


class TestTopFivePercentSizing:
    """⚠️ CLAUDE.md §4.4：按**名次**取，不用 `quantile` 阈值；`floor` 让不足 1 只时不取。"""

    def test_head_size_is_floor_not_round(self) -> None:
        """n=39 → floor(1.95) = 1 只，不是四舍五入的 2 只。

        用 round 会让「头部」在 n 的某些取值上悄悄多装一只，跨日比较时
        头部大小忽 1 忽 2，换手率（K-4）与成本（K-5）跟着抖。
        """
        n = 39
        factor = _series(np.arange(n, dtype=float))
        fwd = _series(np.zeros(n))
        fwd.iloc[-1] = 1.0        # 只有最高的那只有收益
        pts = compute_top_pct_excess({"s": {"f": factor}}, fwd, stage="raw", horizon=20)
        assert pts[0].sample_size == 1

    def test_head_smaller_than_one_name_is_skipped(self) -> None:
        """n=19 → floor(0.95) = 0 只 → **不产出行**，而不是退化成「取 1 只」。

        退化成 1 只等于把 5% 悄悄变成 5.3%~100%，且没有任何提示。
        """
        n = 19
        pts = compute_top_pct_excess(
            {"s": {"f": _series(np.arange(n, dtype=float))}},
            _series(np.zeros(n)), stage="raw", horizon=20, min_xs=5,
        )
        assert pts == []

    def test_ties_at_the_cut_do_not_inflate_head(self) -> None:
        """切点并列时头部只数仍恰为 floor(n*pct)，不因并列而膨胀。

        `quantile` 阈值 + `>=` 比较正是在这里出错：并列会把整批都圈进来。
        """
        n = 100
        vals = np.zeros(n)
        vals[-20:] = 1.0          # 20 只并列最高，远多于 top 5% 的 5 只
        pts = compute_top_pct_excess(
            {"s": {"f": _series(vals)}}, _series(np.arange(n, dtype=float)),
            stage="raw", horizon=20,
        )
        assert pts[0].sample_size == 5, "并列不得让头部膨胀成 20 只"

    def test_pct_parameter_is_consumed(self) -> None:
        """改 pct → 头部只数必须跟着变（§4.11「参数是否真被消费」）。"""
        factor = _series(np.arange(200, dtype=float))
        fwd = _series(np.zeros(200))
        sizes = {
            pct: compute_top_pct_excess(
                {"s": {"f": factor}}, fwd, stage="raw", horizon=20, pct=pct
            )[0].sample_size
            for pct in (0.05, 0.10, 0.20)
        }
        assert sizes == {0.05: 10, 0.10: 20, 0.20: 40}


class TestNoDispersionGuard:
    """横截面无离散度 → 分档与头部都无意义，须跳过（§4.4 「无离散度退化」那条）。"""

    def test_constant_factor_skipped_for_both_metrics(self) -> None:
        flat = _series([7.0] * 100)
        fwd = _series(np.arange(100, dtype=float))
        assert compute_decile_forward_return(
            {"s": {"f": flat}}, fwd, stage="raw", horizon=20
        ) == []
        assert compute_top_pct_excess(
            {"s": {"f": flat}}, fwd, stage="raw", horizon=20
        ) == []
