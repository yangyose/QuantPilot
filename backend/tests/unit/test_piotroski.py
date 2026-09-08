"""Piotroski F-Score（V1.5-C C2 / SDD-EXT-04）：9 项二元信号。

## 本文件最要紧的一条：**缺数据 ≠ 低分**

把缺失项记 0 会让**数据缺口伪装成基本面恶化**，进而把股票错误地踢出均值回归——
正是 SDD-EXT-04 想避免的反面，也是 C-4「不静默掩盖问题」的直接落地。
故：任一必需字段 NaN → 该项记 **NaN**（不记 0）；缺 ≥3 项 → `f_score = NaN`（不可判），
由调用方决定不可判时怎么办（设计 §4.4：**不门控**）。

## 同比口径

`Δ` 一律指**同比上年同期**（report_period 同月日、年份 −1），非环比——
季报口径下环比有季节性偏误。取数由 Service 负责，本层只接收 (current, prior) 两张表。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantpilot.engine.piotroski import F_SCORE_ITEMS, compute_f_score

_FIELDS = ["roa", "ocfps", "eps", "current_ratio", "grossprofit_margin",
           "assets_turn", "total_share", "debt_to_asset"]


def _frame(codes, **overrides) -> pd.DataFrame:
    """默认构造一组「9 项全过」的值，再按需覆写。"""
    base = {
        "roa": 0.08, "ocfps": 1.20, "eps": 1.00, "current_ratio": 2.0,
        "grossprofit_margin": 0.30, "assets_turn": 0.90,
        "total_share": 1.0e9, "debt_to_asset": 0.40,
    }
    base.update(overrides)
    return pd.DataFrame(
        {k: [v] * len(codes) for k, v in base.items()},
        index=pd.Index(codes, name="ts_code"),
    )


def _prior(codes, **overrides) -> pd.DataFrame:
    """上年同期：默认比 current 差一档，使 9 项全部为 1。"""
    base = {
        "roa": 0.05, "ocfps": 0.90, "eps": 0.85, "current_ratio": 1.5,
        "grossprofit_margin": 0.25, "assets_turn": 0.80,
        "total_share": 1.0e9, "debt_to_asset": 0.50,
    }
    base.update(overrides)
    return pd.DataFrame(
        {k: [v] * len(codes) for k, v in base.items()},
        index=pd.Index(codes, name="ts_code"),
    )


class TestNineItems:
    def test_all_nine_pass_gives_nine(self) -> None:
        s, detail = compute_f_score(_frame(["A"]), _prior(["A"]))
        assert list(detail.columns) == list(F_SCORE_ITEMS), "项名与顺序必须稳定"
        assert len(F_SCORE_ITEMS) == 9, "经典 Piotroski 是 9 项（roadmap 曾误记 8 项）"
        assert s.loc["A"] == 9.0
        assert (detail.loc["A"] == 1).all()

    def test_all_nine_fail_gives_zero(self) -> None:
        cur = _frame(["A"], roa=-0.01, ocfps=-0.5, eps=1.0, current_ratio=1.0,
                     grossprofit_margin=0.20, assets_turn=0.70,
                     total_share=1.5e9, debt_to_asset=0.60)
        s, detail = compute_f_score(cur, _prior(["A"]))
        assert s.loc["A"] == 0.0
        assert (detail.loc["A"] == 0).all()

    # 字段 → 依赖它的项。一个字段喂多项是正常的（`ocfps` 同时进 #2 与 #4），
    # 断言「只有对应那一项变」会误判；正确的不变量是**变化范围不超出依赖集**。
    _AFFECTS = {
        "roa": {"roa_positive", "roa_improved"},
        "ocfps": {"cfo_positive", "accrual_quality"},
        "eps": {"accrual_quality"},
        "debt_to_asset": {"leverage_down"},
        "current_ratio": {"liquidity_up"},
        "grossprofit_margin": {"margin_up"},
        "assets_turn": {"turnover_up"},
        "total_share": {"no_dilution"},
    }

    @pytest.mark.parametrize(
        ("field", "value", "item"),
        [
            ("roa", -0.01, "roa_positive"),
            ("ocfps", -0.1, "cfo_positive"),
            ("eps", 2.0, "accrual_quality"),        # ocfps(1.2) <= eps(2.0)
            ("debt_to_asset", 0.60, "leverage_down"),
            ("current_ratio", 1.0, "liquidity_up"),
            ("grossprofit_margin", 0.20, "margin_up"),
            ("assets_turn", 0.70, "turnover_up"),
            ("total_share", 1.5e9, "no_dilution"),
        ],
    )
    def test_single_field_flips_only_its_own_items(self, field, value, item) -> None:
        """改一个字段：目标项必须翻转，且**变化不外溢到无关项**。"""
        _, d0 = compute_f_score(_frame(["A"]), _prior(["A"]))
        _, d1 = compute_f_score(_frame(["A"], **{field: value}), _prior(["A"]))
        assert d1.loc["A", item] == 0, f"{item} 未随 {field} 变化"
        changed = {c for c in F_SCORE_ITEMS if d1.loc["A", c] != d0.loc["A", c]}
        assert changed <= self._AFFECTS[field], (
            f"改 {field} 影响了它不该影响的项：{changed - self._AFFECTS[field]}"
        )

    def test_roa_improved_uses_year_over_year(self) -> None:
        s, d = compute_f_score(_frame(["A"], roa=0.04), _prior(["A"]))  # 0.04 < 0.05
        assert d.loc["A", "roa_improved"] == 0
        assert d.loc["A", "roa_positive"] == 1, "ROA 为正与 ROA 改善是两项，不得混"

    def test_share_issuance_tolerance(self) -> None:
        """ε=0.001 容差：股本微增（≤0.1%）仍算「未增发」。

        没有容差的话，四舍五入或极小的股权激励都会把这项打成 0。
        """
        _, d_ok = compute_f_score(_frame(["A"], total_share=1.0005e9), _prior(["A"]))
        assert d_ok.loc["A", "no_dilution"] == 1
        _, d_bad = compute_f_score(_frame(["A"], total_share=1.01e9), _prior(["A"]))
        assert d_bad.loc["A", "no_dilution"] == 0


class TestMissingIsNotZero:
    """⚠️ 本组是 C2 的核心不变量：**缺数据 ≠ 低分**。"""

    def test_missing_field_makes_item_nan_not_zero(self) -> None:
        _, d = compute_f_score(_frame(["A"], roa=np.nan), _prior(["A"]))
        assert pd.isna(d.loc["A", "roa_positive"]), "缺 roa 却记成 0 —— 缺口伪装成恶化"
        assert pd.isna(d.loc["A", "roa_improved"])

    def test_one_missing_still_judgeable(self) -> None:
        s, _ = compute_f_score(_frame(["A"], assets_turn=np.nan), _prior(["A"]))
        assert s.loc["A"] == 8.0, "缺 1 项 → 按可判的 8 项求和"

    def test_two_missing_still_judgeable(self) -> None:
        s, _ = compute_f_score(
            _frame(["A"], assets_turn=np.nan, grossprofit_margin=np.nan), _prior(["A"])
        )
        assert s.loc["A"] == 7.0

    def test_three_missing_is_unjudgeable(self) -> None:
        """缺 ≥3 项 → NaN（不可判），**不是低分**。

        调用方据此决定「不门控」（设计 §4.4）——不可判不等于不合格。
        """
        cur = _frame(["A"], assets_turn=np.nan, grossprofit_margin=np.nan,
                     current_ratio=np.nan)
        s, _ = compute_f_score(cur, _prior(["A"]))
        assert pd.isna(s.loc["A"])

    def test_missing_prior_also_counts_as_missing(self) -> None:
        """上年同期缺失同样使该项不可判——同比项需要两端都有值。"""
        _, d = compute_f_score(_frame(["A"]), _prior(["A"], roa=np.nan))
        assert pd.isna(d.loc["A", "roa_improved"])
        assert d.loc["A", "roa_positive"] == 1, "当期 roa 有值，这一项仍可判"

    def test_absent_stock_in_prior_is_all_nan(self) -> None:
        """次新股无上年同期 → 同比项全 NaN；纯当期项仍可判。"""
        cur = _frame(["A", "B"])
        pri = _prior(["A"])            # B 缺席
        s, d = compute_f_score(cur, pri)
        assert pd.isna(s.loc["B"]), "5 个同比项全缺 → 不可判"
        assert d.loc["B", "roa_positive"] == 1


class TestEngineLayerPurity:
    def test_does_not_mutate_inputs(self) -> None:
        cur, pri = _frame(["A"]), _prior(["A"])
        c0, p0 = cur.copy(deep=True), pri.copy(deep=True)
        compute_f_score(cur, pri)
        pd.testing.assert_frame_equal(cur, c0)
        pd.testing.assert_frame_equal(pri, p0)

    def test_empty_input_returns_empty(self) -> None:
        s, d = compute_f_score(_frame([]), _prior([]))
        assert s.empty and d.empty
        assert list(d.columns) == list(F_SCORE_ITEMS)
