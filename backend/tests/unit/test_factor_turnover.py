"""K-4：top 5% 集合的日间 Jaccard 换手代理。V1.5-K §1.2。

## 为什么要它

K-2 量的是「头部有没有超额」，但一个头部**每天换一批**的因子，即便超额为正也可能
被交易成本吃光。K-4 用相邻交易日头部集合的 Jaccard 相似度做换手代理，
它同时是 K-5（成本拖累）的输入。

## ⚠️ 方向：存的是**相似度**，不是换手率

`turnover_jaccard` 这个名字两头都读得通，故此处写死并用例钉死：

    J = |A ∩ B| / |A ∪ B|    ——  **两天头部完全相同 → 1.0**（不是 0.0）

换手率 = `1 − J` 是平凡变换，留给分析侧。存原始量是为了避免「已经转换过一次」
和「还没转换」在下游被搞混——那种错误不会报错，只会让成本估算差一个符号。

## 头部选取必须与 K-2 同源

若 K-4 自己再写一遍「取前 5%」，两者选出的就可能不是同一批股票，
于是「我们测了超额的那个头部」与「我们测了换手的那个头部」变成两回事，
而这种不一致在数字上完全看不出来。故 `select_top_pct` 由两者共用，
本文件有专门用例钉死这一点。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantpilot.engine.diagnostics.factor_portfolio import select_top_pct
from quantpilot.engine.diagnostics.factor_turnover import compute_turnover_jaccard


def _codes(n: int, offset: int = 0) -> list[str]:
    return [f"{i + offset:06d}.SZ" for i in range(n)]


def _factor(vals, codes=None) -> pd.Series:
    vals = list(vals)
    return pd.Series(vals, index=codes or _codes(len(vals)), dtype=float)


def _fv(series: pd.Series) -> dict[str, dict[str, pd.Series]]:
    return {"momentum": {"rs_6m": series}}


class TestJaccardDirection:
    def test_identical_heads_give_one_not_zero(self) -> None:
        """方向钉死：**完全相同 → 1.0**。存的是相似度，不是换手率。"""
        f = _factor(np.arange(100, dtype=float))
        pts = compute_turnover_jaccard(_fv(f), _fv(f), stage="raw")
        assert len(pts) == 1
        assert pts[0].value == pytest.approx(1.0)
        assert pts[0].metric == "turnover_jaccard"

    def test_disjoint_heads_give_zero(self) -> None:
        """头部完全换掉 → 0.0。"""
        prev = _factor(np.arange(100, dtype=float))
        # 把顺序整个倒过来：原来最高的 5 只变成最低的 5 只
        curr = _factor(np.arange(100, dtype=float)[::-1])
        pts = compute_turnover_jaccard(_fv(prev), _fv(curr), stage="raw")
        assert pts[0].value == pytest.approx(0.0)

    def test_half_overlap_analytic(self) -> None:
        """两天头部各 5 只、交集 3 只 → J = 3 / (5+5-3) = 3/7（可手算）。

        构造：100 只，前一天头部是 95..99；今天把 95/96 的因子值压到最低，
        并把 0/1 抬到最高 → 今天头部 = {0, 1, 97, 98, 99}，交集 {97,98,99}。
        """
        prev = _factor(np.arange(100, dtype=float))
        vals = np.arange(100, dtype=float)
        vals[95] = -2.0
        vals[96] = -1.0
        vals[0] = 200.0
        vals[1] = 201.0
        pts = compute_turnover_jaccard(_fv(prev), _fv(_factor(vals)), stage="raw")
        assert pts[0].value == pytest.approx(3.0 / 7.0)
        assert pts[0].sample_size == 7, "sample_size 是并集大小（Jaccard 的分母）"


class TestSharedHeadSelection:
    """头部选取与 K-2 同源——不同源则两个指标测的不是同一批股票。"""

    def test_uses_select_top_pct(self) -> None:
        """直接比对：K-4 的头部必须与 `select_top_pct` 给出的一致。

        用大量并列的构造——正是两份实现最容易分道扬镳的地方。
        """
        vals = np.zeros(100)
        vals[-20:] = 1.0                    # 20 只并列最高
        f = _factor(vals)
        head = select_top_pct(f, pct=0.05)
        assert head is not None and len(head) == 5, "K-2 口径：floor，并列不膨胀"
        # 同一因子两天不变 → J=1；若 K-4 另取一批（如把并列 20 只全收），
        # 并集/交集仍相等、J 仍是 1，故不能只靠 J 判断——直接断言 sample_size
        pts = compute_turnover_jaccard(_fv(f), _fv(f), stage="raw")
        assert pts[0].sample_size == 5, "并集大小须等于 K-2 的头部大小"

    def test_pct_parameter_flows_through(self) -> None:
        f = _factor(np.arange(200, dtype=float))
        pts = compute_turnover_jaccard(_fv(f), _fv(f), stage="raw", pct=0.10)
        assert pts[0].sample_size == 20


class TestDegenerate:
    """按 §3 门槛 3：退化跳过不写占位行。"""

    def test_head_below_one_name_skipped(self) -> None:
        f = _factor(np.arange(19, dtype=float))
        assert compute_turnover_jaccard(_fv(f), _fv(f), stage="raw", min_xs=5) == []

    def test_below_min_xs_skipped(self) -> None:
        f = _factor(np.arange(30, dtype=float))
        assert compute_turnover_jaccard(_fv(f), _fv(f), stage="raw", min_xs=50) == []

    def test_no_dispersion_skipped(self) -> None:
        flat = _factor([3.0] * 100)
        assert compute_turnover_jaccard(_fv(flat), _fv(flat), stage="raw") == []

    def test_factor_absent_on_one_day_skipped(self) -> None:
        """只有一天有该因子 → 无法比较，跳过。

        ⚠️ 不能把缺失的一天当成空集算 J=0——那会让「新上线的因子」
        伪装成「换手率 100% 的坏因子」。
        """
        f = _factor(np.arange(100, dtype=float))
        prev = {"momentum": {"rs_6m": f}}
        curr = {"momentum": {"other": f}}
        assert compute_turnover_jaccard(prev, curr, stage="raw") == []

    def test_all_nan_skipped(self) -> None:
        nan_s = _factor([np.nan] * 100)
        assert compute_turnover_jaccard(_fv(nan_s), _fv(nan_s), stage="raw") == []


class TestRowShape:
    def test_horizon_zero_and_bucket_minus_one(self) -> None:
        """换手无前向概念 → horizon=0；无十分位概念 → bucket=-1。"""
        f = _factor(np.arange(100, dtype=float))
        p = compute_turnover_jaccard(_fv(f), _fv(f), stage="raw")[0]
        assert p.horizon == 0
        assert p.bucket == -1

    def test_universe_change_does_not_break_jaccard(self) -> None:
        """两天 universe 不同（有新股/退市）仍可算——Jaccard 本就是集合运算。

        构造：今天整体后移 50 个代码 → 两天头部无交集 → J=0，且不抛异常。
        """
        prev = _factor(np.arange(100, dtype=float), _codes(100))
        curr = _factor(np.arange(100, dtype=float), _codes(100, offset=50))
        pts = compute_turnover_jaccard(_fv(prev), _fv(curr), stage="raw")
        assert len(pts) == 1
        assert 0.0 <= pts[0].value <= 1.0
