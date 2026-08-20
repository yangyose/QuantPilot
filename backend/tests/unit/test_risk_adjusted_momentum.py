"""V1.5-C C1-2：风险调整动量（SDD-EXT-08）+ 策略参数化收尾。

依据 docs/design/phases/v1_5_c_strategy_expansion.md §3.2 C1-2。

SDD §7.2.3 注把「涨幅 / 波动率」列为 L2+ 可配置增强项，V1.5 定为默认行为：
`risk_adj_return_3m = return_3m / max(σ60, 1e-6)`，减少对高波动标的的偏向。

σ 不年化——横截面 rank 与 Z-score 对正的常数缩放不变，年化只增计算不增信息；
理由文本里才把 σ 年化展示（面向用户可读）。

同批兑现 momentum / mean_reversion 两处【降级说明】剩余的恢复条件：
`lookback_long`、`rsi_period`、`bbands_period`、`bbands_std` 真正传入计算。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from quantpilot.core.config_defaults import (
    MeanReversionStrategyConfig,
    MomentumStrategyConfig,
)
from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
from quantpilot.engine.strategies.momentum import MomentumStrategy
from tests.unit.test_strategies_impl import _make_snapshot_for_strategies


def _volatile_snapshot() -> tuple[list[str], object]:
    """两只近 3M 涨幅相同、但波动率差一个量级的股票。

    CALM 单调平滑上涨，WILD 锯齿式上涨到同一终点 —— 风险调整前二者 return_3m
    相同，调整后 CALM 必须胜出。这正是 SDD-EXT-08 想要的效果。
    """
    n = 130
    codes = ["CALM", "WILD"]
    calm = [100.0 * (1.0 + 0.003 * i) for i in range(n)]
    wild = [
        100.0 * (1.0 + 0.003 * i) * (1.10 if i % 2 else 0.90)
        for i in range(n - 1)
    ]
    wild.append(calm[-1])          # 终点对齐 → 同样的 return_3m
    wild[-61] = calm[-61]          # 起点对齐（return_3m 取倒数第 61 列）
    price_series = {"CALM": calm, "WILD": wild}
    snapshot = _make_snapshot_for_strategies(
        codes, close_values=[calm[-1], wild[-1]], price_series=price_series,
    )
    return codes, snapshot


# ============================================================
# UT-C1-04：risk_adj_return_3m 定义
# ============================================================

def test_ut_c1_04a_risk_adjusted_factor_replaces_return_3m() -> None:
    """UT-C1-04a: 默认 risk_adjusted=True → 因子列名为 risk_adj_return_3m。"""
    codes, snapshot = _volatile_snapshot()
    factors = MomentumStrategy().compute_raw_factors(pd.Index(codes), snapshot)

    assert "risk_adj_return_3m" in factors.columns, "风险调整因子列缺失"
    assert "return_3m" not in factors.columns, "原始 return_3m 应被替换而非并存"
    assert set(MomentumStrategy().weights) == {
        "risk_adj_return_3m", "rs_6m", "industry_rs",
    }, "weights 键须与产出因子列一致，否则该因子被 score() 静默丢弃"


def test_ut_c1_04b_equals_return_over_sigma() -> None:
    """UT-C1-04b: risk_adj_return_3m == return_3m / max(σ60, 1e-6)，σ 用对数收益。"""
    codes, snapshot = _volatile_snapshot()
    idx = pd.Index(codes)

    risk_adj = MomentumStrategy().compute_raw_factors(idx, snapshot)["risk_adj_return_3m"]
    plain = MomentumStrategy(
        MomentumStrategyConfig(risk_adjusted=False),
    ).compute_raw_factors(idx, snapshot)["return_3m"]

    prices = snapshot["adj_prices"].astype(float)          # type: ignore[index]
    for code in codes:
        series = prices.loc[code]
        log_ret = np.diff(np.log(series.to_numpy(dtype=float)))[-60:]
        sigma = float(pd.Series(log_ret).std())
        expected = float(plain[code]) / max(sigma, 1e-6)
        assert math.isclose(float(risk_adj[code]), expected, rel_tol=1e-9), (
            f"{code}: 期望 {expected}，实际 {risk_adj[code]}"
        )


def test_ut_c1_04c_low_volatility_wins_at_equal_return() -> None:
    """UT-C1-04c: 涨幅相同时低波动标的排序更高（SDD-EXT-08 的目的）。"""
    codes, snapshot = _volatile_snapshot()
    idx = pd.Index(codes)

    plain = MomentumStrategy(
        MomentumStrategyConfig(risk_adjusted=False),
    ).compute_raw_factors(idx, snapshot)["return_3m"]
    assert math.isclose(float(plain["CALM"]), float(plain["WILD"]), rel_tol=1e-9), (
        "测试数据前提不成立：两只股票的 return_3m 应相同，否则本用例无法归因到波动率"
    )

    risk_adj = MomentumStrategy().compute_raw_factors(idx, snapshot)["risk_adj_return_3m"]
    assert float(risk_adj["CALM"]) > float(risk_adj["WILD"]), (
        f"低波动 CALM({risk_adj['CALM']:.3f}) 应高于高波动 "
        f"WILD({risk_adj['WILD']:.3f})"
    )


def test_ut_c1_04d_insufficient_returns_yield_nan_sigma() -> None:
    """UT-C1-04d: 有效收益数 < volatility_window × 0.7 → σ 记 NaN → 因子 NaN。

    不足样本算出的 σ 不可靠，除以它会放大噪声；置 NaN 让该标的不参与本策略，
    而不是给一个看似正常的数。
    """
    codes = ["A", "B", "C"]
    price_series = {c: [100.0 + i * (j + 1) * 0.7 for i in range(130)]
                    for j, c in enumerate(codes)}
    snapshot = _make_snapshot_for_strategies(
        codes,
        close_values=[price_series[c][-1] for c in codes],
        price_series=price_series,
    )
    idx = pd.Index(codes)

    # volatility_window=200 → 需 ≥140 个收益，实际仅 129 → 全 NaN
    strict = MomentumStrategy(MomentumStrategyConfig(volatility_window=200))
    assert strict.compute_raw_factors(idx, snapshot)["risk_adj_return_3m"].isna().all()

    # 默认 60 → 需 ≥42 个，实际 129 → 有效
    assert not MomentumStrategy().compute_raw_factors(
        idx, snapshot,
    )["risk_adj_return_3m"].isna().all()


def test_ut_c1_04e_risk_adjusted_false_falls_back() -> None:
    """UT-C1-04e: risk_adjusted=False 一键回退原 return_3m（对照实验用）。"""
    codes, snapshot = _volatile_snapshot()
    strategy = MomentumStrategy(MomentumStrategyConfig(risk_adjusted=False))
    factors = strategy.compute_raw_factors(pd.Index(codes), snapshot)

    assert "return_3m" in factors.columns
    assert "risk_adj_return_3m" not in factors.columns
    assert set(strategy.weights) == {"return_3m", "rs_6m", "industry_rs"}


def test_ut_c1_04f_volatility_window_is_consumed() -> None:
    """UT-C1-04f: volatility_window 真正被计算消费（改参数 → σ 变 → 因子变）。"""
    codes, snapshot = _volatile_snapshot()
    idx = pd.Index(codes)

    v30 = MomentumStrategy(
        MomentumStrategyConfig(volatility_window=30),
    ).compute_raw_factors(idx, snapshot)["risk_adj_return_3m"]
    v90 = MomentumStrategy(
        MomentumStrategyConfig(volatility_window=90),
    ).compute_raw_factors(idx, snapshot)["risk_adj_return_3m"]

    assert not math.isclose(float(v30["WILD"]), float(v90["WILD"]), rel_tol=1e-6), (
        "volatility_window 未被消费"
    )


# ============================================================
# UT-C1-05：lookback_long 参数化（momentum 剩余降级说明）
# ============================================================

def test_ut_c1_05_lookback_long_is_consumed() -> None:
    """UT-C1-05: lookback_long 真正决定 rs_6m 的回看长度。"""
    codes, snapshot = _volatile_snapshot()
    idx = pd.Index(codes)

    rs_120 = MomentumStrategy(
        MomentumStrategyConfig(lookback_long=120),
    ).compute_raw_factors(idx, snapshot)["rs_6m"]
    rs_80 = MomentumStrategy(
        MomentumStrategyConfig(lookback_long=80),
    ).compute_raw_factors(idx, snapshot)["rs_6m"]

    assert not math.isclose(float(rs_120["CALM"]), float(rs_80["CALM"]), rel_tol=1e-6), (
        "lookback_long 未被消费"
    )


# ============================================================
# UT-C1-06：mean_reversion RSI / BBands 参数化
# ============================================================

def test_ut_c1_06a_rsi_period_is_consumed() -> None:
    """UT-C1-06a: rsi_period 真正传入 pandas_ta（兑现【降级说明】恢复条件）。"""
    codes = ["A", "B"]
    price_series = {
        "A": [100.0 + 8.0 * math.sin(i / 3.0) + i * 0.2 for i in range(130)],
        "B": [100.0 + 5.0 * math.cos(i / 4.0) + i * 0.1 for i in range(130)],
    }
    snapshot = _make_snapshot_for_strategies(
        codes,
        close_values=[price_series[c][-1] for c in codes],
        price_series=price_series,
    )
    idx = pd.Index(codes)

    rsi_14 = MeanReversionStrategy(
        MeanReversionStrategyConfig(rsi_period=14),
    ).compute_raw_factors(idx, snapshot)["rsi_oversold"]
    rsi_5 = MeanReversionStrategy(
        MeanReversionStrategyConfig(rsi_period=5),
    ).compute_raw_factors(idx, snapshot)["rsi_oversold"]

    assert not math.isclose(float(rsi_14["A"]), float(rsi_5["A"]), rel_tol=1e-6), (
        "rsi_period 未被消费"
    )


def test_ut_c1_06b_bbands_params_are_consumed() -> None:
    """UT-C1-06b: bbands_period / bbands_std 真正传入 pandas_ta。"""
    codes = ["A", "B"]
    price_series = {
        "A": [100.0 + 8.0 * math.sin(i / 3.0) + i * 0.2 for i in range(130)],
        "B": [100.0 + 5.0 * math.cos(i / 4.0) + i * 0.1 for i in range(130)],
    }
    snapshot = _make_snapshot_for_strategies(
        codes,
        close_values=[price_series[c][-1] for c in codes],
        price_series=price_series,
    )
    idx = pd.Index(codes)

    base = MeanReversionStrategy(
        MeanReversionStrategyConfig(bbands_period=20, bbands_std=2.0),
    ).compute_raw_factors(idx, snapshot)["bb_position"]
    wide = MeanReversionStrategy(
        MeanReversionStrategyConfig(bbands_period=20, bbands_std=3.0),
    ).compute_raw_factors(idx, snapshot)["bb_position"]
    short = MeanReversionStrategy(
        MeanReversionStrategyConfig(bbands_period=10, bbands_std=2.0),
    ).compute_raw_factors(idx, snapshot)["bb_position"]

    assert not math.isclose(float(base["A"]), float(wide["A"]), rel_tol=1e-6), (
        "bbands_std 未被消费"
    )
    assert not math.isclose(float(base["A"]), float(short["A"]), rel_tol=1e-6), (
        "bbands_period 未被消费"
    )


# ============================================================
# UT-C1-07：理由模板
# ============================================================

def test_ut_c1_07_reason_shows_annualized_sigma() -> None:
    """UT-C1-07: 理由文本含「风险调整」与**年化**波动率（计算不年化，展示年化）。"""
    codes, snapshot = _volatile_snapshot()
    results = MomentumStrategy().score(pd.Index(codes), snapshot)
    assert results, "测试前提不成立：应有评分结果"

    reason = results[0].reason
    assert "风险调整" in reason, f"理由未体现风险调整：{reason}"
    assert "波动率" in reason, f"理由未展示波动率：{reason}"
