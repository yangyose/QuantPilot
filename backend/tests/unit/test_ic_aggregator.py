"""Phase 14 §14-4 IC 时序聚合 + panel 纯函数测试 — UT-P14-4-01/02。

覆盖：
- UT-P14-4-01：aggregate_monthly 接受 ic_value 序列 → 输出 monthly aggregate（含
  ic_mean / ic_std / sample_size / avg_xs_sample / t_stat）
- UT-P14-4-02：build_panel 接受 (strategy, state) -> monthly_df dict → heatmap 数据

设计文档：docs/design/phases/phase14_account_integrity.md §6.3 DoD。
"""
from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from quantpilot.engine.diagnostics.ic_aggregator import (
    ICRecord,
    aggregate_monthly,
    build_panel,
    compute_t_stat,
)

# ============================================================
# UT-P14-4-01: aggregate_monthly
# ============================================================


def test_ut_p14_4_01a_aggregate_monthly_single_month_multiple_days() -> None:
    """单月 5 日 IC 观察 → 月均值/标准差/n 正确，t-stat 与样本统计一致。"""
    records = [
        ICRecord(trade_date=date(2024, 6, 3), ic_value=0.02, sample_size=200),
        ICRecord(trade_date=date(2024, 6, 4), ic_value=0.04, sample_size=210),
        ICRecord(trade_date=date(2024, 6, 5), ic_value=0.05, sample_size=205),
        ICRecord(trade_date=date(2024, 6, 6), ic_value=0.03, sample_size=215),
        ICRecord(trade_date=date(2024, 6, 7), ic_value=0.06, sample_size=208),
    ]
    df = aggregate_monthly(records)
    assert list(df.columns) == [
        "year_month", "ic_mean", "ic_std", "sample_size", "avg_xs_sample", "t_stat",
    ]
    assert len(df) == 1
    row = df.iloc[0]
    assert row["year_month"] == "2024-06"
    assert row["ic_mean"] == pytest.approx(0.04)
    expected_std = float(pd.Series([0.02, 0.04, 0.05, 0.03, 0.06]).std(ddof=1))
    assert row["ic_std"] == pytest.approx(expected_std)
    assert row["sample_size"] == 5
    assert row["avg_xs_sample"] == pytest.approx(207.6)
    assert row["t_stat"] is not None
    se = expected_std / math.sqrt(5)
    assert row["t_stat"] == pytest.approx(0.04 / se)


def test_ut_p14_4_01b_aggregate_monthly_groups_across_months() -> None:
    """跨月 6 条数据 → 3 个 year_month 行 + 升序排列。"""
    records = [
        ICRecord(trade_date=date(2024, 1, 15), ic_value=0.01, sample_size=100),
        ICRecord(trade_date=date(2024, 1, 30), ic_value=0.03, sample_size=110),
        ICRecord(trade_date=date(2024, 2, 5), ic_value=0.05, sample_size=120),
        ICRecord(trade_date=date(2024, 2, 20), ic_value=0.02, sample_size=130),
        ICRecord(trade_date=date(2024, 3, 10), ic_value=-0.01, sample_size=140),
        ICRecord(trade_date=date(2024, 3, 25), ic_value=0.04, sample_size=150),
    ]
    df = aggregate_monthly(records)
    assert list(df["year_month"]) == ["2024-01", "2024-02", "2024-03"]
    assert df.iloc[0]["sample_size"] == 2
    assert df.iloc[1]["sample_size"] == 2
    assert df.iloc[2]["sample_size"] == 2


def test_ut_p14_4_01c_aggregate_monthly_single_day_in_month() -> None:
    """月内仅 1 个观察日 → ic_std=0, t_stat=None（不可计算）。"""
    records = [
        ICRecord(trade_date=date(2024, 7, 1), ic_value=0.05, sample_size=180),
    ]
    df = aggregate_monthly(records)
    assert len(df) == 1
    assert df.iloc[0]["ic_std"] == 0.0
    assert df.iloc[0]["t_stat"] is None


def test_ut_p14_4_01d_aggregate_monthly_empty_input() -> None:
    """空输入 → 空 DataFrame + columns 完整。"""
    df = aggregate_monthly([])
    assert df.empty
    assert "year_month" in df.columns
    assert "t_stat" in df.columns


# ============================================================
# UT-P14-4-02: build_panel
# ============================================================


def test_ut_p14_4_02a_build_panel_4x3_combos() -> None:
    """4 strategy × 3 state × 多月输入 → 12 行 panel DataFrame。"""
    _STRATS = ("trend", "momentum", "mean_reversion", "value")
    _STATES = ("UPTREND", "DOWNTREND", "OSCILLATION")
    monthly_panels: dict[tuple[str, str], pd.DataFrame] = {}
    for strategy in _STRATS:
        for state in _STATES:
            # 3 个月每月 ic_mean 不同
            monthly_panels[(strategy, state)] = pd.DataFrame({
                "year_month":   ["2024-01", "2024-02", "2024-03"],
                "ic_mean":      [0.02, 0.04, 0.03],
                "ic_std":       [0.01, 0.02, 0.015],
                "sample_size":  [20, 22, 21],
                "avg_xs_sample":[200, 210, 205],
                "t_stat":       [3.0, 4.0, 3.5],
            })

    panel = build_panel(monthly_panels)
    assert len(panel) == 12  # 4 × 3
    assert set(panel["strategy"]) == set(_STRATS)
    assert set(panel["state"]) == set(_STATES)
    # 每个 (strategy, state) ic_mean = mean(0.02, 0.04, 0.03) = 0.03
    for _, row in panel.iterrows():
        assert row["ic_mean"] == pytest.approx(0.03)
        assert row["n_months"] == 3
        assert row["t_stat"] is not None


def test_ut_p14_4_02b_build_panel_empty_monthly_df() -> None:
    """某 (strategy, state) 月度 DataFrame 空 → n_months=0 + t_stat=None。"""
    monthly_panels = {
        ("trend", "UPTREND"): pd.DataFrame(columns=[
            "year_month", "ic_mean", "ic_std", "sample_size", "avg_xs_sample", "t_stat",
        ]),
    }
    panel = build_panel(monthly_panels)
    assert len(panel) == 1
    assert panel.iloc[0]["n_months"] == 0
    assert panel.iloc[0]["t_stat"] is None


def test_ut_p14_4_02c_build_panel_empty_input() -> None:
    """空 dict → 空 DataFrame + columns 完整。"""
    panel = build_panel({})
    assert panel.empty
    assert "strategy" in panel.columns
    assert "t_stat" in panel.columns


# ============================================================
# compute_t_stat 边界
# ============================================================


def test_ut_p14_4_compute_t_stat_zero_std_returns_none() -> None:
    """std=0 → t_stat=None（不可计算）。"""
    assert compute_t_stat(mean=0.04, std=0.0, n=5) is None


def test_ut_p14_4_compute_t_stat_n_le_1_returns_none() -> None:
    """n ≤ 1 → t_stat=None。"""
    assert compute_t_stat(mean=0.04, std=0.02, n=1) is None
    assert compute_t_stat(mean=0.04, std=0.02, n=0) is None


def test_ut_p14_4_compute_t_stat_normal() -> None:
    """正常输入：mean=0.04, std=0.02, n=10 → t = 0.04 / (0.02/sqrt(10))。"""
    expected = 0.04 / (0.02 / math.sqrt(10))
    assert compute_t_stat(0.04, 0.02, 10) == pytest.approx(expected)
