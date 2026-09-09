"""C2 门控：F-Score < 6 的股票不参与均值回归（SDD-EXT-04）。

## 落点必须是 `apply_constraints`

写在 `score()` 末尾的约束在五步管线里**完全不生效**——C1-1 记过这个教训
（价值陷阱护栏因此长期失效，而 value 占 composite 权重 0.57~0.87）。
`apply_constraints` 被 `compute_strategy_factors` 与 `score()` 同源调用。

## 三条分支，每条都有代价

1. **命中门控**（`f_score < 6`）→ 该行三列**全置 NaN**。
   ⚠️ **禁止置 0**：Z-score 后 0 是横截面均值，置 0 等于发了张中性分而不是排除。
2. **金融股**走替代判据 `roe > 0.05`。
   【降级说明】SDD 外评原文含「不良贷款率未显著上升」，Tushare `fina_indicator`
   无 NPL 字段 → V1.5-C 仅实现 ROE 分支。
3. **`f_score = NaN`（不可判）→ 不门控**。不可判 ≠ 不合格；
   把不可判当低分会让**数据缺口伪装成基本面恶化**（同 `compute_f_score` 的判据）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy

_COLS = ["rsi_oversold", "price_deviation", "bb_position"]


def _raw(codes) -> pd.DataFrame:
    return pd.DataFrame(
        {c: [0.5] * len(codes) for c in _COLS},
        index=pd.Index(codes, name="ts_code"),
    )


def _snapshot(codes, f_scores, *, industries=None, roe=None) -> dict:
    idx = pd.Index(codes, name="ts_code")
    return {
        "f_score": pd.Series(f_scores, index=idx, dtype=float),
        "stock_info": pd.DataFrame(
            {"sw_industry_l1": industries or ["制造"] * len(codes)}, index=idx
        ),
        "financials": pd.DataFrame(
            {"roe": roe if roe is not None else [0.10] * len(codes)}, index=idx
        ),
    }


def _gated() -> MeanReversionStrategy:
    """门控**打开**的策略实例。

    生产默认是影子模式（`piotroski_gate_enabled=False`，见文件末尾那组测试）。
    本文件绝大多数用例验的是「门控生效时怎么判」，故显式打开——
    不显式打开的话，它们会在影子默认下全部退化成「什么都没发生也算过」。
    """
    from quantpilot.core.config_defaults import MeanReversionStrategyConfig

    return MeanReversionStrategy(MeanReversionStrategyConfig(piotroski_gate_enabled=True))


def _apply(codes, f_scores, **kw) -> pd.DataFrame:
    return _gated().apply_constraints(
        _raw(codes), pd.Index(codes, name="ts_code"), _snapshot(codes, f_scores, **kw)
    )


class TestGate:
    def test_below_threshold_blanks_all_columns(self) -> None:
        out = _apply(["A"], [5.0])
        assert out.loc["A"].isna().all(), "命中门控须三列全 NaN"

    def test_at_threshold_passes(self) -> None:
        """`f_score >= 6` 通过——门槛是 6，不是 7（SDD-EXT-04 原文）。"""
        out = _apply(["A"], [6.0])
        assert out.loc["A"].notna().all()

    def test_blank_is_nan_not_zero(self) -> None:
        """⚠️ 置 0 而非 NaN 会让被门控的股票拿到「横截面均值」这张中性分。"""
        out = _apply(["A"], [3.0])
        assert not (out.loc["A"] == 0).any(), "置 0 = 发中性分，不是排除"

    def test_unjudgeable_is_not_gated(self) -> None:
        """`f_score = NaN` → 保留。不可判 ≠ 不合格。"""
        out = _apply(["A"], [np.nan])
        assert out.loc["A"].notna().all(), "不可判被当成低分门控了"

    def test_only_hit_rows_are_blanked(self) -> None:
        out = _apply(["A", "B"], [3.0, 8.0])
        assert out.loc["A"].isna().all()
        assert out.loc["B"].notna().all(), "门控外溢到了未命中的行"


class TestFinancialAlternative:
    def test_financial_with_good_roe_passes_despite_low_f_score(self) -> None:
        """金融股走 `roe > 0.05`，不看 F-Score（其会计科目不适用 Piotroski）。"""
        out = _apply(["A"], [2.0], industries=["银行"], roe=[0.12])
        assert out.loc["A"].notna().all()

    def test_financial_with_poor_roe_is_gated(self) -> None:
        out = _apply(["A"], [9.0], industries=["证券"], roe=[0.02])
        assert out.loc["A"].isna().all()

    def test_financial_industries_reuse_universe_constant(self) -> None:
        """行业集合必须复用 `UniverseFilter.FINANCIAL_INDUSTRIES`，不得另写一份。

        各写一份就会漂移，而「两处金融股定义不一致」在数字上看不出来。
        """
        from quantpilot.engine.strategies import mean_reversion as mod
        from quantpilot.engine.universe import UniverseFilter

        assert mod._FINANCIAL_INDUSTRIES is UniverseFilter.FINANCIAL_INDUSTRIES

    def test_financial_missing_roe_is_not_gated(self) -> None:
        """金融股 roe 缺失 → 不可判 → **不门控**（同主分支的口径）。"""
        out = _apply(["A"], [2.0], industries=["保险"], roe=[np.nan])
        assert out.loc["A"].notna().all()


class TestNoSnapshotIsNoOp:
    def test_absent_f_score_leaves_everything_untouched(self) -> None:
        """快照没给 `f_score`（回填未完成 / 回测路径）→ 恒等返回。

        C-4：可见的降级——门控不生效时不得悄悄改变因子值。
        """
        s = _gated()
        raw = _raw(["A", "B"])
        out = s.apply_constraints(raw, raw.index, {})
        pd.testing.assert_frame_equal(out, raw)

    def test_does_not_mutate_input(self) -> None:
        s = _gated()
        raw = _raw(["A"])
        before = raw.copy(deep=True)
        s.apply_constraints(raw, raw.index, _snapshot(["A"], [2.0]))
        pd.testing.assert_frame_equal(raw, before)


class TestServiceActuallyComputesFScore:
    """⚠️ 门控写对了 ≠ 门控会生效——`f_score` 必须真的被算出来并放进快照。

    这正是 CLAUDE.md §4.11 表第 4 例的形状：`compute_pool` 的持仓保护机制完全正确，
    只因链上没人传非空值，`candidate_pool.is_holding` 五年 0 行、硬止损不可达。

    判据只能在**调用点**上验（§4.11：构造 spy 再调用它的测试是自证式的）。
    这里用 AST 检查 `ScoringService` 确实调了取数与计算、并把结果塞进快照。
    """

    @staticmethod
    def _snapshot_src() -> str:
        import inspect

        from quantpilot.services.strategy_service import ScoringService

        return inspect.getsource(ScoringService._build_market_snapshot)

    def test_service_calls_yoy_pairs_and_compute_f_score(self) -> None:
        import ast

        tree = ast.parse(self._snapshot_src().lstrip())
        called = {
            (getattr(n.func, "attr", None) or getattr(n.func, "id", None))
            for n in ast.walk(tree) if isinstance(n, ast.Call)
        }
        assert "get_financials_yoy_pairs" in called, "未取同比配对 → f_score 无从算起"
        assert "compute_f_score" in called, "取了数却没算 F-Score"

    def test_snapshot_carries_f_score_key(self) -> None:
        """`f_score` 必须真的写进快照 dict——算了不放等于没算。"""
        assert '"f_score"' in self._snapshot_src(), "f_score 未写入快照"

    def test_snapshot_carries_stock_info_for_financial_branch(self) -> None:
        """金融股替代判据需要 `sw_industry_l1`，它来自快照的 `stock_info`。"""
        assert '"stock_info"' in self._snapshot_src(), (
            "快照缺 stock_info → 金融股永远走不到 ROE 替代分支"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 影子模式（2026-09-09）：门控默认**不真正剔除**，只计算并记日志。
#
# 为什么默认关：开发集 28 个采样日实测（`docs/reviews/scoring_monotonicity_2026-09-09.md`
# §7）——策略 IC 在 6 个阈值上基本不动，头部 5% 超额**不单调**（F<5 反比 F<4 差），
# 且无一显著（|t| ≤ 1.13）。F<7 的 +0.0005 代价是丢掉 72% 可选池。
# 机制先验合理，值得留在生产观察；但先验不能替代证据。
#
# ⚠️ 这两条测试钉的是「开关真的被消费」，不是「开关存在」。
# CLAUDE.md §4.4 记过：接了配置却毫无作用的代码，能跑过任何只验证「不抛异常」的测试。
# 判据是**改开关 → 结果必须变**。
# ─────────────────────────────────────────────────────────────────────────────


def test_gate_11_shadow_is_default_and_returns_raw_unchanged():
    """默认（影子）：命中门控的行也**原样返回**，不置 NaN。"""
    codes = ["000001.SZ", "000002.SZ"]
    raw = _raw(codes)
    out = MeanReversionStrategy().apply_constraints(
        raw, raw.index, _snapshot(codes, [3.0, 8.0])
    )
    pd.testing.assert_frame_equal(out, raw)


def test_gate_12_enabling_the_flag_changes_the_result():
    """开关打开 → 同一份输入，命中行三列全 NaN。

    与上一条构成「改参数 → 结果必须变」的一对：只写其中任何一条，
    开关没接线时都能绿。
    """
    from quantpilot.core.config_defaults import MeanReversionStrategyConfig

    codes = ["000001.SZ", "000002.SZ"]
    raw = _raw(codes)
    strat = MeanReversionStrategy(
        MeanReversionStrategyConfig(piotroski_gate_enabled=True)
    )
    out = strat.apply_constraints(raw, raw.index, _snapshot(codes, [3.0, 8.0]))

    assert out.loc["000001.SZ"].isna().all()          # F=3 < 6 → 剔除
    assert not out.loc["000002.SZ"].isna().any()      # F=8 → 保留
    assert not out.equals(raw)


def test_gate_13_threshold_is_configurable_and_consumed():
    """阈值同样必须被真消费——写死 6.0 时这条会红。"""
    from quantpilot.core.config_defaults import MeanReversionStrategyConfig

    codes = ["000001.SZ"]
    raw = _raw(codes)
    snap = _snapshot(codes, [5.0])

    lenient = MeanReversionStrategy(
        MeanReversionStrategyConfig(piotroski_gate_enabled=True, piotroski_min_score=5.0)
    ).apply_constraints(raw, raw.index, snap)
    strict = MeanReversionStrategy(
        MeanReversionStrategyConfig(piotroski_gate_enabled=True, piotroski_min_score=7.0)
    ).apply_constraints(raw, raw.index, snap)

    assert not lenient.isna().any().any()   # F=5 >= 5 → 留
    assert strict.isna().all().all()        # F=5 <  7 → 剔


def test_gate_14_shadow_still_logs_the_blocked_count(caplog):
    """影子模式必须仍然报出「本来会剔掉几只」——否则观察期什么也观察不到。"""
    import logging

    codes = ["000001.SZ", "000002.SZ"]
    raw = _raw(codes)
    with caplog.at_level(logging.INFO):
        MeanReversionStrategy().apply_constraints(
            raw, raw.index, _snapshot(codes, [3.0, 8.0])
        )
    line = "\n".join(caplog.messages)
    assert "piotroski_gate" in line
    assert "blocked=1" in line
    assert "shadow" in line          # 影子与真剔除必须能从日志区分开
