"""C3 影子模式的**零回归证明**（设计 §8.5 DoD）。

## 要证什么

低波动策略作为一等 composite 成员接入（进 `strategy_factors`、走五步管线、
被 ICIR 监控），但权重从 0 起步。**影子期必须对现有四策略零影响**——
不是「看起来差不多」，而是 `z_raw` 与 `composite_z` **逐值相同**。

## 为什么这条必须单独存在

`UT-C0-08b` 已证明「零权重策略不进正交化矩阵」这个**机制**成立。
本文件证的是**这一次具体接入**没有破坏它——机制对、接法错同样会出事，
例如：把 low_volatility 写进了 `orthogonalize_order`、或给了非 0 的默认权重、
或它的 NaN 行经别的路径漏进了合成。

⚠️ 判据是**逐值相等**（`==`，不是 `approx`）：影子期的零回归是**数学保证**
（`apply_monthly_rebalance` 的归一化分母含这个 0），不是数值近似。
用 `approx` 会让「其实变了一点点」蒙混过关。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from quantpilot.core.config_defaults import DEFAULT_STRATEGY_WEIGHTS
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.scorer import Scorer

_CODES = [f"00000{i}.SZ" for i in range(1, 13)]
_ORDER = ["value", "mean_reversion", "trend", "momentum"]


def _factors(mapping: dict[str, dict[str, float]]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for s, codes in mapping.items():
        df = pd.DataFrame.from_dict(
            {c: [v] for c, v in codes.items()}, orient="index", columns=[f"f_{s}"]
        )
        df.index.name = "ts_code"
        out[s] = df
    return out


def _four() -> dict[str, pd.DataFrame]:
    return _factors({
        "trend": {c: i * 1.0 for i, c in enumerate(_CODES)},
        "momentum": {c: (i % 5) * 1.0 for i, c in enumerate(_CODES)},
        "mean_reversion": {c: (12 - i) * 1.0 for i, c in enumerate(_CODES)},
        "value": {c: math.sin(i) for i, c in enumerate(_CODES)},
    })


def _five(with_nan: bool = False) -> dict[str, pd.DataFrame]:
    """五策略。`with_nan` 制造「低波动对部分股票算不出来」——这正是陷阱 1 的触发条件。"""
    f = _four()
    vals = {c: (float("nan") if (with_nan and i < 4) else -0.01 * i)
            for i, c in enumerate(_CODES)}
    f.update(_factors({"low_volatility": vals}))
    return f


def _snapshot() -> dict:
    return {
        "industry": {c: ("TECH" if i % 2 == 0 else "FINANCE")
                     for i, c in enumerate(_CODES)},
        "market_cap": pd.Series(
            np.linspace(1e9, 2e9, num=len(_CODES)),
            index=pd.Index(_CODES, name="ts_code"),
        ),
        "beta": None,
    }


def _agg(weights: dict[str, float], factors) -> dict:
    res = Scorer().aggregate(
        market_state=MarketStateEnum.OSCILLATION,
        strategy_factors=factors,
        snapshot=_snapshot(),
        weights_runtime=weights,
        weights_source="default_matrix",
        orthogonalize_order=_ORDER,
        hysteresis_status="stable",
    )
    return {r.ts_code: r for r in res}


_W4 = {"trend": 0.15, "momentum": 0.15, "mean_reversion": 0.40, "value": 0.30}
_W5 = {**_W4, "low_volatility": 0.0}


class TestShadowModeIsExactlyZeroRegression:
    def test_composite_z_identical_stock_by_stock(self) -> None:
        """⚠️ 逐值相等，不是近似。"""
        before, after = _agg(_W4, _four()), _agg(_W5, _five())
        assert set(before) == set(after)
        for code in before:
            b, a = before[code].composite_z, after[code].composite_z
            assert (b is None) == (a is None)
            if b is not None:
                assert a == b, f"{code}: composite_z {b} → {a}"

    def test_four_strategies_z_raw_identical(self) -> None:
        """现有四策略的 `z_raw` 必须逐值不变——这是 DoD 原文要求。"""
        before, after = _agg(_W4, _four()), _agg(_W5, _five())
        for code in before:
            rb = before[code].score_breakdown_raw or {}
            ra = after[code].score_breakdown_raw or {}
            for s in ("trend", "momentum", "mean_reversion", "value"):
                if s in rb:
                    assert s in ra, f"{code}: {s} 从 score_breakdown_raw 消失了"
                    assert ra[s]["z_raw"] == rb[s]["z_raw"], f"{code}/{s} z_raw 变了"

    def test_low_volatility_nan_rows_do_not_flatten_composite(self) -> None:
        """⚠️ 陷阱 1：低波动对部分股票是 NaN 时，**不得**把这些股票的
        composite 打成 0（= Φ(0)×100 = 50 分的假中位分）。

        这正是零权重策略仍进 Gram-Schmidt 会造成的后果。
        """
        before, after = _agg(_W4, _four()), _agg(_W5, _five(with_nan=True))
        for code in before:
            b, a = before[code].composite_z, after[code].composite_z
            if b is not None:
                assert a == b, f"{code}: NaN 行把 composite_z 由 {b} 压成了 {a}"

    def test_scores_are_not_all_fifty(self) -> None:
        """反向守卫：若上面三条因为「两边都退化成 50 分」而恒真，这条会红。

        §4.11 元判据：一个判据若在机制生效与失效时给出相同结果，它就不是判据。
        """
        after = _agg(_W5, _five(with_nan=True))
        scores = {round(r.composite_score, 4) for r in after.values()}
        assert len(scores) > 1, "全体同分 → 前面的『逐值相等』失去意义"


class TestShadowStrategyIsStillObservable:
    def test_low_volatility_enters_strategy_z_all(self) -> None:
        """影子策略必须仍被观测到——否则拿不到日级 IC，永远无法被评估为可激活。

        这正是 C0-07 修的那条：IC 观测与 composite 加权解耦。
        """
        after = _agg(_W5, _five())
        any_seen = any(
            (r.strategy_z_all or {}).get("low_volatility") is not None
            for r in after.values()
        )
        assert any_seen, "低波动未进 strategy_z_all → 日级 IC 断供 → 永远无法激活"

    def test_low_volatility_absent_from_score_breakdown_raw(self) -> None:
        """权重为 0 → **不进** `score_breakdown_raw`（该字段只含真正参与合成的策略）。

        进了的话前端「主要驱动」会显示一个权重 0 的策略。
        """
        after = _agg(_W5, _five())
        for r in after.values():
            assert "low_volatility" not in (r.score_breakdown_raw or {})


class TestDefaultMatrixKeepsFourStrategyWeights:
    @pytest.mark.parametrize("state", ["uptrend", "downtrend", "oscillation"])
    def test_relative_weights_unchanged(self, state: str) -> None:
        """加 0 权重项不改变四策略的相对权重——影子期零回归的数学保证。"""
        w = getattr(DEFAULT_STRATEGY_WEIGHTS, state)
        assert w["low_volatility"] == 0.0
        four = {k: v for k, v in w.items() if k != "low_volatility"}
        assert sum(four.values()) == pytest.approx(1.0), "四策略权重之和应仍为 1"
