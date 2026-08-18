"""V1.5-C：零权重策略在 Scorer 中的两处缺陷。

UT-C0-07a~e（IC 可观测性）+ UT-C0-08a~b（§8.3 陷阱 1）。两者都由「某策略权重为 0」
触发，但机制相反：07 系列是零权重策略**产出太少**（不产 z_raw → 断掉 IC 监控），
08 系列是零权重策略**影响太多**（仍进 Gram-Schmidt → 其 NaN 行把整行 composite
打成假中位分）。


**问题背景**（2026-08-18 本地追平实测挖出）：`check_factor_offline_rules` R1
（ICIR<0 连续 6 月）把 trend / momentum 判为 offline → 月末 rebalance 权重置 0
→ `Scorer.aggregate` Step 5 只遍历 `valid_weights`（`float(w) > 0.0`）构建
`score_breakdown_raw` → 零权重策略无 `z_raw` → `extract_strategy_z` 抽不到 →
不产日级 IC 行 → ICIR 滚动窗口断供 → 该策略**永远无法被评估为已恢复**。

R1 本身每月重新评估、设计上带复活路径；断掉的是喂给它的观测数据。修复方式是把
**IC 观测**与**composite 加权**解耦：`CompositeScore.strategy_z_all` 记录全部
active 策略的 z_raw（含权重 0 者），`extract_strategy_z` 优先消费它。
`score_breakdown_raw` 语义不变（仍只含真正参与合成的策略），因此
signal_service / attribution_service / 前端「主要驱动」逻辑零影响。

覆盖：
- UT-C0-07a：零权重策略进 strategy_z_all、不进 score_breakdown_raw
- UT-C0-07b：extract_strategy_z 从 strategy_z_all 抽到零权重策略
- UT-C0-07c：无 strategy_z_all 的旧对象回退 score_breakdown_raw（回测 legacy 路径）
- UT-C0-07d：加入零权重策略不改变 composite_z（合成数学不受影响）
- UT-C0-07e：NaN z_raw 不进 strategy_z_all
- UT-C0-08a：零权重策略的 NaN 行不再把 composite_z 打成 0（假中位分 50）
- UT-C0-08b：零权重策略完全不参与正交化——其存在与否，composite_z 逐股一致
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quantpilot.engine.diagnostics.ic_aggregator import extract_strategy_z
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.scorer import Scorer

_CODES = [f"00000{i}.SZ" for i in range(1, 13)]


def _build_factors(mapping: dict[str, dict[str, float]]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for s, codes in mapping.items():
        df = pd.DataFrame.from_dict(
            {c: [v] for c, v in codes.items()}, orient="index", columns=[f"f_{s}"],
        )
        df.index.name = "ts_code"
        out[s] = df
    return out


def _build_snapshot(ts_codes: list[str]) -> dict:
    industries = {c: ("TECH" if i % 2 == 0 else "FINANCE") for i, c in enumerate(ts_codes)}
    return {
        "industry": industries,
        "market_cap": pd.Series(
            np.linspace(1e9, 2e9, num=len(ts_codes)),
            index=pd.Index(ts_codes, name="ts_code"),
        ),
        "beta": None,
    }


def _four_strategy_factors() -> dict[str, pd.DataFrame]:
    return _build_factors({
        "trend": {c: i * 1.0 for i, c in enumerate(_CODES)},
        "momentum": {c: (i % 5) * 1.0 for i, c in enumerate(_CODES)},
        "mean_reversion": {c: (12 - i) * 1.0 for i, c in enumerate(_CODES)},
        "value": {c: math.sin(i) for i, c in enumerate(_CODES)},
    })


def _aggregate(weights: dict[str, float], factors=None):
    scorer = Scorer()
    return scorer.aggregate(
        market_state=MarketStateEnum.OSCILLATION,
        strategy_factors=factors if factors is not None else _four_strategy_factors(),
        snapshot=_build_snapshot(_CODES),
        weights_runtime=weights,
        weights_source="icir",
        orthogonalize_order=["value", "mean_reversion", "trend", "momentum"],
        hysteresis_status="stable",
    )


# ============================================================
# UT-C0-07a：零权重策略进 strategy_z_all、不进 score_breakdown_raw
# ============================================================


def test_ut_c0_07a_zero_weight_strategy_kept_in_strategy_z_all() -> None:
    """生产实况权重（trend/momentum 被 R1 判 offline → 0）下仍能观测其 z_raw。"""
    results = _aggregate({
        "trend": 0.0, "momentum": 0.0, "mean_reversion": 0.3265, "value": 0.6735,
    })
    assert results, "5 步管线应产出结果"

    for r in results:
        # score_breakdown_raw 语义不变：只含真正参与合成的策略
        assert set(r.score_breakdown_raw) == {"mean_reversion", "value"}
        # strategy_z_all 覆盖全部 active 策略，含权重 0 的两个
        assert r.strategy_z_all is not None
        assert set(r.strategy_z_all) == {"trend", "momentum", "mean_reversion", "value"}
        # 与合成用的 z_raw 完全一致（同一来源，不是重算）
        for s, entry in r.score_breakdown_raw.items():
            assert r.strategy_z_all[s] == pytest.approx(entry["z_raw"])


# ============================================================
# UT-C0-07b：extract_strategy_z 从 strategy_z_all 抽到零权重策略
# ============================================================


def test_ut_c0_07b_extract_strategy_z_sees_zero_weight_strategies() -> None:
    """端到端：权重 0 的 trend/momentum 也进入日级 IC 的输入 Series。"""
    results = _aggregate({
        "trend": 0.0, "momentum": 0.0, "mean_reversion": 0.3265, "value": 0.6735,
    })
    strategy_z = extract_strategy_z(results)

    assert set(strategy_z) == {"trend", "momentum", "mean_reversion", "value"}
    for s, series in strategy_z.items():
        assert len(series) == len(results), f"{s} 应覆盖全部 ts_code"
        assert series.notna().all()


# ============================================================
# UT-C0-07c：无 strategy_z_all 的旧对象回退 score_breakdown_raw
# ============================================================


def test_ut_c0_07c_extract_strategy_z_falls_back_to_breakdown_raw() -> None:
    """aggregate_legacy / 回测 fallback 路径产出的对象无 strategy_z_all → 走旧字段。"""
    legacy = [
        SimpleNamespace(
            ts_code="000001.SZ",
            strategy_z_all=None,
            score_breakdown_raw={"trend": {"z_raw": 1.5, "weight": 1.0, "contribution": 1.5}},
        ),
        SimpleNamespace(
            ts_code="000002.SZ",
            strategy_z_all=None,
            score_breakdown_raw={"trend": {"z_raw": -0.5, "weight": 1.0, "contribution": -0.5}},
        ),
    ]
    strategy_z = extract_strategy_z(legacy)
    assert set(strategy_z) == {"trend"}
    assert strategy_z["trend"]["000001.SZ"] == pytest.approx(1.5)
    assert strategy_z["trend"]["000002.SZ"] == pytest.approx(-0.5)


# ============================================================
# UT-C0-07d：加入零权重策略不改变 composite_z
# ============================================================


def test_ut_c0_07d_zero_weight_does_not_affect_composite() -> None:
    """权重显式给 0 与整个策略缺席，composite_z 必须逐股一致。"""
    factors = _four_strategy_factors()
    with_zero = _aggregate(
        {"trend": 0.0, "momentum": 0.0, "mean_reversion": 0.3265, "value": 0.6735},
        factors=factors,
    )
    # 缺席对照组：strategy_factors 只给两个有权重的策略
    without = _aggregate(
        {"mean_reversion": 0.3265, "value": 0.6735},
        factors={k: v for k, v in factors.items() if k in ("mean_reversion", "value")},
    )

    left = {r.ts_code: r.composite_z for r in with_zero}
    right = {r.ts_code: r.composite_z for r in without}
    assert set(left) == set(right)
    for code in left:
        assert left[code] == pytest.approx(right[code]), f"{code} composite_z 被零权重策略污染"


# ============================================================
# UT-C0-07e：NaN z_raw 不进 strategy_z_all
# ============================================================


def test_ut_c0_07e_nan_z_raw_excluded_from_strategy_z_all() -> None:
    """某策略只覆盖部分 ts_code → 未覆盖股票在 strategy_z_all 中缺席（不写 NaN）。"""
    factors = _four_strategy_factors()
    # momentum（权重 0）只保留前 5 只
    factors["momentum"] = factors["momentum"].iloc[:5]

    results = _aggregate(
        {"trend": 0.0, "momentum": 0.0, "mean_reversion": 0.3265, "value": 0.6735},
        factors=factors,
    )
    covered = {str(c) for c in factors["momentum"].index}
    n_with_momentum = 0
    for r in results:
        assert r.strategy_z_all is not None
        for value in r.strategy_z_all.values():
            assert not (isinstance(value, float) and math.isnan(value))
        if "momentum" in r.strategy_z_all:
            n_with_momentum += 1
            assert r.ts_code in covered
    assert n_with_momentum == len(covered & {r.ts_code for r in results})


# ============================================================
# UT-C0-08a/b：§8.3 陷阱 1 —— 零权重策略不得参与 Gram-Schmidt
# ============================================================
#
# `Orthogonalizer.gram_schmidt` 的 `valid_mask = matrix.notna().all(axis=1)` 要求
# order 各列同时非 NaN；不满足的行残差整行 NaN，回到 Scorer 经 `z_col.fillna(0.0)`
# → weighted_z = 0 → composite_z = 0 → `Φ(0)×100 = 50` 的**假中位分**，而
# `any_valid`（基于 raw z）仍为 True 故该行不会被剔除。
#
# 生产实况：trend / momentum 权重已是 0.0000 却仍在正交化矩阵里，凡在这两个策略上
# 有 NaN 的股票，其真实排序正被压平到中位。


def _partial_coverage_factors() -> tuple[dict[str, pd.DataFrame], set[str]]:
    """momentum（权重 0）只覆盖前 6 只 → 其余 6 只在矩阵中为 NaN。"""
    factors = _four_strategy_factors()
    del factors["trend"]  # 三策略即可复现，少一维便于推理
    factors["momentum"] = factors["momentum"].iloc[:6]
    uncovered = set(_CODES) - {str(c) for c in factors["momentum"].index}
    return factors, uncovered


def test_ut_c0_08a_zero_weight_nan_rows_do_not_flatten_composite() -> None:
    """momentum 未覆盖的股票不得因此拿到 composite_z=0 / composite_score=50。"""
    factors, uncovered = _partial_coverage_factors()
    assert uncovered, "构造应留出未被 momentum 覆盖的股票"

    results = _aggregate(
        {"momentum": 0.0, "mean_reversion": 0.3265, "value": 0.6735}, factors=factors,
    )
    hit = [r for r in results if r.ts_code in uncovered]
    assert len(hit) == len(uncovered), "未覆盖股票不应被剔除出结果"
    for r in hit:
        assert r.composite_z != pytest.approx(0.0, abs=1e-12), (
            f"{r.ts_code} composite_z 被零权重策略的 NaN 行打平"
        )
        assert r.composite_score != pytest.approx(50.0, abs=1e-9)


def test_ut_c0_08b_zero_weight_strategy_absent_from_orthogonalization() -> None:
    """带零权重策略 vs 该策略整个缺席：composite_z 必须逐股一致。"""
    factors, _ = _partial_coverage_factors()
    with_zero = _aggregate(
        {"momentum": 0.0, "mean_reversion": 0.3265, "value": 0.6735}, factors=factors,
    )
    without = _aggregate(
        {"mean_reversion": 0.3265, "value": 0.6735},
        factors={k: v for k, v in factors.items() if k != "momentum"},
    )

    left = {r.ts_code: r.composite_z for r in with_zero}
    right = {r.ts_code: r.composite_z for r in without}
    assert set(left) == set(right)
    for code in left:
        assert left[code] == pytest.approx(right[code]), f"{code} composite_z 受零权重策略影响"

    # 但 IC 观测不受影响：零权重策略仍在 strategy_z_all 里（C0-6 与本修复互不抵消）
    covered = [r for r in with_zero if r.strategy_z_all and "momentum" in r.strategy_z_all]
    assert covered, "零权重策略退出正交化后，仍须保留在 strategy_z_all 中供日级 IC 使用"
