"""K-6：因子级 IC 与有效率（V1.5-K §2.2）。

现状是 `compute_daily_ic` 只算**策略级** Spearman IC（`factor_ic_window_state.factor`
存的其实是策略名），**因子级指标在生产没有任何落点**——2026-08-31 C1 上线后想验证
C1-3 是否生效，发现 `rs_6m` 的有效率在生产**痕迹根本不存在**。本模块补这个缺口。

## ⚠️ 一处对设计文档 §3 门槛 3 的有意偏离

§3 写「新指标退化行为须与 `compute_daily_ic` 一致——跳过不写占位行」。
`ic` 照办，但 **`valid_ratio` 必须永远写，包括 0.0**：

一个因子全 NaN 时，「跳过」等于让它从数据里彻底消失——**而那正是要检测的东西**。
C1-3 的教训就是「痕迹为零」本身即缺陷本体（`rs_6m` 0/2274 且无任何告警）。
若 valid_ratio 也跟着消失，K-6 就退化成一个只会报告「一切正常」的指标。

§3 那条的原意是防「同一天不同 metric 有的有行有的没行」造成分析困惑；
本偏离把「没行」换成「有行且值为 0」，反而更容易发现问题。已回写设计文档。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from quantpilot.engine.diagnostics.factor_ic import (
    PanelStatPoint,
    compute_factor_ic,
    compute_factor_valid_ratio,
)


def _codes(n: int) -> list[str]:
    return [f"{i:06d}.SZ" for i in range(n)]


def _monotone_factor(n: int = 60) -> pd.Series:
    return pd.Series(np.arange(n, dtype=float), index=_codes(n))


def _forward(n: int = 60, sign: float = 1.0) -> pd.Series:
    return pd.Series(sign * np.arange(n, dtype=float) / 100.0, index=_codes(n))


class TestFactorIC:
    def test_perfect_rank_correlation_is_one(self) -> None:
        """因子与前向收益同序 → Spearman IC = 1（解析值，可手验）。"""
        fv = {"momentum": {"rs_6m": _monotone_factor()}}
        pts = compute_factor_ic(fv, _forward(), stage="raw", horizon=20)
        assert len(pts) == 1
        p = pts[0]
        assert (p.strategy, p.factor, p.stage, p.metric, p.horizon) == (
            "momentum", "rs_6m", "raw", "ic", 20,
        )
        assert p.value == 1.0
        assert p.sample_size == 60
        assert p.bucket == -1

    def test_perfect_inverse_is_minus_one(self) -> None:
        fv = {"momentum": {"rs_6m": _monotone_factor()}}
        pts = compute_factor_ic(fv, _forward(sign=-1.0), stage="raw", horizon=20)
        assert pts[0].value == -1.0

    def test_each_factor_gets_its_own_row(self) -> None:
        """多因子多策略 → 逐 (strategy, factor) 各出一行，不合并。"""
        fv = {
            "momentum": {"rs_6m": _monotone_factor(), "rs_1m": _monotone_factor()},
            "value": {"pe_inv": _monotone_factor()},
        }
        pts = compute_factor_ic(fv, _forward(), stage="z", horizon=5)
        keys = sorted((p.strategy, p.factor) for p in pts)
        assert keys == [("momentum", "rs_1m"), ("momentum", "rs_6m"), ("value", "pe_inv")]
        assert all(p.stage == "z" and p.horizon == 5 for p in pts)

    def test_stage_and_horizon_are_carried_not_hardcoded(self) -> None:
        """stage/horizon 若被写死，这条立刻红（§4.11「参数是否真被消费」）。"""
        fv = {"s": {"f": _monotone_factor()}}
        got = {
            (p.stage, p.horizon)
            for st in ("raw", "z")
            for h in (5, 10, 20, 40)
            for p in compute_factor_ic(fv, _forward(), stage=st, horizon=h)
        }
        assert got == {(st, h) for st in ("raw", "z") for h in (5, 10, 20, 40)}


class TestICDegenerate:
    """`ic` 与 `compute_daily_ic` 同规则：退化则**跳过不写行**。"""

    def test_below_min_xs_is_skipped(self) -> None:
        n = 5
        fv = {"s": {"f": pd.Series(np.arange(n, dtype=float), index=_codes(n))}}
        assert compute_factor_ic(fv, _forward(n), stage="raw", horizon=20, min_xs=20) == []

    def test_all_nan_factor_is_skipped(self) -> None:
        fv = {"s": {"f": pd.Series([np.nan] * 60, index=_codes(60))}}
        assert compute_factor_ic(fv, _forward(), stage="raw", horizon=20) == []

    def test_zero_cross_sectional_variance_is_skipped(self) -> None:
        """横截面无方差 → Spearman 退化为 NaN → 不写占位行。"""
        fv = {"s": {"f": pd.Series([7.0] * 60, index=_codes(60))}}
        assert compute_factor_ic(fv, _forward(), stage="raw", horizon=20) == []

    def test_partial_nan_uses_aligned_count(self) -> None:
        """sample_size 是**对齐后**的有效数，不是 universe 大小。"""
        f = _monotone_factor()
        f.iloc[:10] = np.nan
        pts = compute_factor_ic({"s": {"f": f}}, _forward(), stage="raw", horizon=20)
        assert pts[0].sample_size == 50


class TestValidRatio:
    """⚠️ 与 `ic` 相反：**永远写行，包括 0.0**。理由见模块 docstring。"""

    def test_all_nan_factor_emits_zero_not_nothing(self) -> None:
        """这是本模块存在的**首要理由**——C1-3 的 `rs_6m` 0/2274 必须留下痕迹。

        若这里跟着 `ic` 一起跳过，K-6 就退化成一个只会报「一切正常」的指标。
        """
        fv = {"momentum": {"rs_6m": pd.Series([np.nan] * 2274, index=_codes(2274))}}
        pts = compute_factor_valid_ratio(fv, universe_size=2274, stage="raw")
        assert len(pts) == 1, "全 NaN 因子必须留下一行，而不是消失"
        assert pts[0].value == 0.0
        assert pts[0].metric == "valid_ratio"
        assert pts[0].sample_size == 2274, "分母是 universe 大小"

    def test_ratio_denominator_is_universe_not_series_length(self) -> None:
        """分母必须是 universe 大小。

        若用 Series 长度当分母，一个「只把有效值放进 Series」的上游实现会让
        有效率恒为 1.0——**指标永远报正常**，与没有这个指标等价。
        """
        f = pd.Series([1.0, 2.0, 3.0], index=_codes(3))   # 只含 3 个有效值
        pts = compute_factor_valid_ratio({"s": {"f": f}}, universe_size=100, stage="z")
        assert pts[0].value == 0.03
        assert pts[0].sample_size == 100

    def test_partial_validity(self) -> None:
        f = _monotone_factor(100)
        f.iloc[:25] = np.nan
        pts = compute_factor_valid_ratio({"s": {"f": f}}, universe_size=100, stage="raw")
        assert pts[0].value == 0.75

    def test_zero_universe_returns_nan_not_division_error(self) -> None:
        fv = {"s": {"f": pd.Series(dtype=float)}}
        pts = compute_factor_valid_ratio(fv, universe_size=0, stage="raw")
        assert len(pts) == 1 and math.isnan(pts[0].value)

    def test_horizon_is_zero_for_valid_ratio(self) -> None:
        """有效率无前向概念 → horizon 填 0（system_design §4.2 约定）。"""
        pts = compute_factor_valid_ratio(
            {"s": {"f": _monotone_factor()}}, universe_size=60, stage="raw"
        )
        assert pts[0].horizon == 0


class TestPointShapeMatchesTable:
    def test_fields_map_to_factor_panel_stat_columns(self) -> None:
        """`PanelStatPoint` 必须能直接落到 `factor_panel_stat` 的列上。

        少一个字段就得在写库处现编默认值——那正是口径漂移的起点。
        """
        from quantpilot.models.business import FactorPanelStat

        cols = set(FactorPanelStat.__table__.c.keys())
        fields = set(PanelStatPoint.__dataclass_fields__)
        # panel_run / trade_date 由写库方按批次统一填，不属单点计算
        assert fields | {"id", "panel_run", "trade_date", "created_at"} == cols
