"""Phase 14 §14-9 日级 IC 生产者 engine 纯函数测试 — UT-P14-9-01/02。

覆盖（设计文档 docs/design/phases/phase14_account_integrity.md §11.3.1/§11.3.3 + §11.4）：
- UT-P14-9-01：compute_daily_ic — 每策略 Spearman Rank IC + sample_size；
  calc_ic 返 None（对齐样本 < 5）→ 该策略不产 DailyICPoint（P3-1）；
  对齐有效 N < _DAILY_IC_MIN_XS(30) → 跳过（S-02）。
- UT-P14-9-02：compute_forward_returns — 后复权 adj_close 透视表 + base/end 日
  → ret = adj_close[end]/adj_close[base] − 1；excluded（涨跌停/停牌）剔除；
  缺 base 或 end 价的 ts_code 剔除。

均为 engine 层纯函数（无 IO）。RED 阶段：实现未落地，import 失败。
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from quantpilot.engine.diagnostics.ic_aggregator import (
    _DAILY_IC_MIN_XS,
    DailyICPoint,
    compute_daily_ic,
    compute_forward_returns,
)

# ============================================================
# UT-P14-9-01: compute_daily_ic
# ============================================================


def _linear_series(n: int, sign: float) -> tuple[pd.Series, pd.Series]:
    """构造 n 只股票单调相关的 (strategy_z, forward_return)。sign=+1 正相关 / −1 反相关。"""
    codes = [f"{i:06d}.SZ" for i in range(n)]
    z = pd.Series(np.linspace(-2.0, 2.0, n), index=codes)
    r = pd.Series(sign * np.linspace(-0.1, 0.1, n), index=codes)
    return z, r


def test_ut_p14_9_01a_positive_monotonic_ic_near_plus_one() -> None:
    """单调正相关 strategy_z↔return → IC ≈ +1，sample_size = 对齐有效数。"""
    z, r = _linear_series(40, sign=1.0)
    points = compute_daily_ic({"trend": z}, r, min_xs=5)
    assert len(points) == 1
    p = points[0]
    assert isinstance(p, DailyICPoint)
    assert p.strategy == "trend"
    assert p.ic_value > 0.99
    assert p.sample_size == 40


def test_ut_p14_9_01b_negative_monotonic_ic_near_minus_one() -> None:
    """单调反相关 → IC ≈ −1。"""
    z, r = _linear_series(40, sign=-1.0)
    points = compute_daily_ic({"value": z}, r, min_xs=5)
    assert len(points) == 1
    assert points[0].ic_value < -0.99


def test_ut_p14_9_01c_nan_aligned_sample_size() -> None:
    """strategy_z / return 含 NaN → sample_size = 对齐后非空交集数。"""
    z, r = _linear_series(40, sign=1.0)
    z.iloc[:5] = np.nan  # 5 只缺因子值
    r.iloc[-3:] = np.nan  # 3 只缺收益
    points = compute_daily_ic({"trend": z}, r, min_xs=5)
    assert len(points) == 1
    assert points[0].sample_size == 40 - 5 - 3  # 32 对齐有效


def test_ut_p14_9_01d_calc_ic_none_below_5_skipped() -> None:
    """对齐有效样本 < 5 → calc_ic 返 None → 该策略不产 DailyICPoint（P3-1）。"""
    codes = [f"{i:06d}.SZ" for i in range(4)]
    z = pd.Series([0.1, 0.2, 0.3, 0.4], index=codes)
    r = pd.Series([0.01, 0.02, 0.03, 0.04], index=codes)
    points = compute_daily_ic({"trend": z}, r, min_xs=1)
    assert points == []


def test_ut_p14_9_01e_below_min_xs_skipped() -> None:
    """对齐有效 N < _DAILY_IC_MIN_XS(默认 30) → 跳过（S-02）。"""
    z, r = _linear_series(20, sign=1.0)  # 20 < 30
    points = compute_daily_ic({"trend": z}, r)  # 用默认 min_xs
    assert points == []


def test_ut_p14_9_01f_default_min_xs_is_30() -> None:
    """常量 _DAILY_IC_MIN_XS = 30。"""
    assert _DAILY_IC_MIN_XS == 30


def test_ut_p14_9_01g_multiple_strategies_one_point_each() -> None:
    """多策略 → 每个通过门槛的策略一个 DailyICPoint；不通过的被跳过。"""
    z_ok, r = _linear_series(40, sign=1.0)
    z_small = z_ok.iloc[:10]  # 仅 10 只对齐 → 不足默认 min_xs
    points = compute_daily_ic({"trend": z_ok, "momentum": z_small}, r)
    by_strategy = {p.strategy: p for p in points}
    assert set(by_strategy) == {"trend"}  # momentum 因 < 30 被跳


# ============================================================
# UT-P14-9-02: compute_forward_returns
# ============================================================


def _adj_pivot() -> pd.DataFrame:
    """构造后复权 adj_close 透视表：index=ts_code，columns=trade_date。"""
    d0, d1 = date(2024, 6, 3), date(2024, 7, 1)
    return pd.DataFrame(
        {
            d0: {"000001.SZ": 10.0, "000002.SZ": 20.0, "000003.SZ": 5.0},
            d1: {"000001.SZ": 11.0, "000002.SZ": 19.0, "000003.SZ": 5.5},
        }
    )


def test_ut_p14_9_02a_forward_return_from_adj_close() -> None:
    """ret = adj_close[end]/adj_close[base] − 1，逐股正确。"""
    pivot = _adj_pivot()
    ret = compute_forward_returns(pivot, date(2024, 6, 3), date(2024, 7, 1))
    assert math.isclose(ret["000001.SZ"], 0.10, rel_tol=1e-9)
    assert math.isclose(ret["000002.SZ"], -0.05, rel_tol=1e-9)
    assert math.isclose(ret["000003.SZ"], 0.10, rel_tol=1e-9)


def test_ut_p14_9_02b_excluded_codes_dropped() -> None:
    """excluded（涨跌停/停牌）ts_code 从结果剔除。"""
    pivot = _adj_pivot()
    ret = compute_forward_returns(
        pivot, date(2024, 6, 3), date(2024, 7, 1), excluded={"000002.SZ"}
    )
    assert "000002.SZ" not in ret.index
    assert "000001.SZ" in ret.index


def test_ut_p14_9_02c_missing_base_or_end_price_dropped() -> None:
    """缺 base 或 end 价的 ts_code 不出现在结果中。"""
    pivot = _adj_pivot()
    pivot.loc["000004.SZ", date(2024, 6, 3)] = 8.0  # 仅 base 有价，end 缺
    ret = compute_forward_returns(pivot, date(2024, 6, 3), date(2024, 7, 1))
    assert "000004.SZ" not in ret.index
    assert len(ret) == 3
