"""K-6 调用点：面板统计量的组装与扇出。V1.5-K §2.1/§2.2。

## 这一层做什么

把一天的评分产出（`list[CompositeScore]`，带 `factor_raw` / `factor_z`）重建成
`{strategy: {factor: Series}}`，然后按 stage × horizon × metric 扇出到
K-2~K-6 各计算函数，产出可直接落 `factor_panel_stat` 的 `PanelStatPoint` 列表。

## ⚠️ 最容易错的一条：哪些指标该跟 horizon 循环

| metric | 有前向概念？ | 应产出 |
|---|---|---|
| `ic` / `decile_fwd_return` / `top5_excess` | 有 | **每个 horizon 各一组** |
| `valid_ratio` / `turnover_jaccard` / `cost_drag` | **无** | **整天各一条**（horizon=0）|

把无前向概念的指标也塞进 horizon 循环，会产出 4 条**只有 horizon 不同、
值完全相同**的重复行。它们能通过九元组唯一键（horizon 是维度之一），
所以数据库拦不住；行数膨胀 4 倍，而分析侧按 horizon 分组时同一个换手率
会被重复计入四次。`TestHorizonIndependentMetricsEmittedOnce` 钉死这条。
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from quantpilot.engine.diagnostics.factor_panel import (
    build_factor_matrices,
    compute_panel_stats_for_date,
)
from quantpilot.engine.diagnostics.multi_horizon import HorizonForward
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.scorer import CompositeScore

_N = 60
_CODES = [f"{i:06d}.SZ" for i in range(_N)]


def _composite(i: int, *, with_panel: bool = True) -> CompositeScore:
    raw = {"momentum": {"rs_6m": float(i), "rs_1m": float(_N - i)}}
    z = {"momentum": {"rs_6m": float(i) / 10.0, "rs_1m": float(_N - i) / 10.0}}
    return CompositeScore(
        ts_code=_CODES[i], composite_score=50.0,
        trend_score=None, momentum_score=None,
        reversion_score=None, value_score=None,
        market_state=MarketStateEnum.OSCILLATION,
        score_breakdown={}, explanation="",
        composite_z=0.0, composite_pct_in_market=0.5,
        factor_raw=raw if with_panel else None,
        factor_z=z if with_panel else None,
    )


def _composites(with_panel: bool = True) -> list[CompositeScore]:
    return [_composite(i, with_panel=with_panel) for i in range(_N)]


def _forwards(horizons=(5, 10, 20, 40)) -> list[HorizonForward]:
    r = pd.Series(np.arange(_N, dtype=float) / 100.0, index=_CODES)
    return [HorizonForward(horizon=h, end_date=date(2026, 5, 12), returns=r) for h in horizons]


class TestBuildMatrices:
    def test_rebuilds_strategy_factor_series(self) -> None:
        m = build_factor_matrices(_composites(), stage="raw")
        assert set(m) == {"momentum"}
        assert set(m["momentum"]) == {"rs_6m", "rs_1m"}
        s = m["momentum"]["rs_6m"]
        assert len(s) == _N
        assert s[_CODES[7]] == 7.0

    def test_z_stage_reads_factor_z(self) -> None:
        m = build_factor_matrices(_composites(), stage="z")
        assert m["momentum"]["rs_6m"][_CODES[7]] == 0.7

    def test_missing_panel_fields_give_empty(self) -> None:
        """未开 collect_factor_panel 时 factor_raw/z 为 None → 空矩阵，不炸。"""
        assert build_factor_matrices(_composites(with_panel=False), stage="raw") == {}

    def test_unknown_stage_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            build_factor_matrices(_composites(), stage="winsorized")


class TestFanOut:
    def test_both_stages_present(self) -> None:
        pts = compute_panel_stats_for_date(
            _composites(), _forwards(), state="OSCILLATION", universe_size=_N
        )
        assert {p.stage for p in pts} == {"raw", "z"}

    def test_forward_metrics_emitted_per_horizon(self) -> None:
        pts = compute_panel_stats_for_date(
            _composites(), _forwards(), state="OSCILLATION", universe_size=_N
        )
        for metric in ("ic", "top5_excess"):
            hs = {p.horizon for p in pts if p.metric == metric and p.stage == "raw"}
            assert hs == {5, 10, 20, 40}, f"{metric} 应每 horizon 一组，实得 {hs}"

    def test_decile_has_ten_buckets_per_horizon(self) -> None:
        pts = compute_panel_stats_for_date(
            _composites(), _forwards(), state="OSCILLATION", universe_size=_N
        )
        for h in (5, 10, 20, 40):
            b = sorted(
                p.bucket for p in pts
                if p.metric == "decile_fwd_return" and p.horizon == h and p.stage == "raw"
                and p.factor == "rs_6m"
            )
            assert b == list(range(1, 11)), f"h={h} 十档不全：{b}"


class TestHorizonIndependentMetricsEmittedOnce:
    """⚠️ 无前向概念的指标不得跟 horizon 循环——否则产出 4 条只有 horizon 不同的重复行。"""

    def test_valid_ratio_once_per_factor(self) -> None:
        pts = compute_panel_stats_for_date(
            _composites(), _forwards(), state="OSCILLATION", universe_size=_N
        )
        vr = [p for p in pts if p.metric == "valid_ratio" and p.stage == "raw"]
        assert len(vr) == 2, f"两个因子各一条，实得 {len(vr)}"
        assert {p.horizon for p in vr} == {0}

    def test_turnover_and_cost_once_per_factor(self) -> None:
        pts = compute_panel_stats_for_date(
            _composites(), _forwards(), state="OSCILLATION", universe_size=_N,
            prev_composites=_composites(),
        )
        for metric in ("turnover_jaccard", "cost_drag"):
            rows = [p for p in pts if p.metric == metric and p.stage == "raw"]
            assert len(rows) == 2, f"{metric} 应两个因子各一条，实得 {len(rows)}"
            assert {p.horizon for p in rows} == {0}

    def test_no_duplicate_nine_tuples(self) -> None:
        """整批产出内不得有九元组重复——那会在写库时撞唯一键或互相覆盖。"""
        pts = compute_panel_stats_for_date(
            _composites(), _forwards(), state="OSCILLATION", universe_size=_N,
            prev_composites=_composites(),
        )
        keys = [
            (p.strategy, p.factor, p.stage, p.state, p.horizon, p.metric, p.bucket)
            for p in pts
        ]
        assert len(keys) == len(set(keys)), "存在九元组重复行"


class TestPrevDayOptional:
    def test_without_prev_no_turnover_or_cost(self) -> None:
        """首日没有前一日 → 不产换手/成本，其余照常。"""
        pts = compute_panel_stats_for_date(
            _composites(), _forwards(), state="OSCILLATION", universe_size=_N
        )
        assert not [p for p in pts if p.metric in ("turnover_jaccard", "cost_drag")]
        assert [p for p in pts if p.metric == "ic"]


class TestStateAndUniversePropagate:
    def test_state_written_to_every_point(self) -> None:
        pts = compute_panel_stats_for_date(
            _composites(), _forwards(), state="UPTREND", universe_size=_N
        )
        assert {p.state for p in pts} == {"UPTREND"}

    def test_valid_ratio_denominator_is_universe_size(self) -> None:
        """分母是 universe 大小，不是 Series 长度（否则恒为 1.0，指标失效）。"""
        pts = compute_panel_stats_for_date(
            _composites(), _forwards(), state="OSCILLATION", universe_size=120
        )
        vr = next(p for p in pts if p.metric == "valid_ratio")
        assert vr.sample_size == 120
        assert vr.value == 0.5, "60 个有效值 / universe 120"
