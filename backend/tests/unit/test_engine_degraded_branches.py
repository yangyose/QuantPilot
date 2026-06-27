"""Phase 15 §15-4：Engine 层降级/边界分支覆盖补强。

针对 value/trend/mean_reversion 策略的「数据不足 / 缺列 / 空历史」降级分支——
这些正是 C-4 关注的静默降级路径，补 UT 既提覆盖率又验证降级行为正确。
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from quantpilot.engine.factor_pipeline import FactorPipeline, FactorPipelineConfig
from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
from quantpilot.engine.strategies.trend import TrendStrategy
from quantpilot.engine.strategies.value import ValueStrategy, _compute_historical_percentile

# ---------------------------------------------------------------------------
# ValueStrategy._compute_historical_percentile 边界
# ---------------------------------------------------------------------------

def test_pct_empty_history_all_nan() -> None:
    """pe_pb_history 为空 → 全 NaN。"""
    universe = pd.Index(["A.SZ", "B.SZ"], name="ts_code")
    curr = pd.Series([10.0, 12.0], index=universe)
    out = _compute_historical_percentile(universe, curr, pd.DataFrame(), "pe_ttm")
    assert out.isna().all()


def test_pct_missing_col_all_nan() -> None:
    """col 不在 history 列 → 全 NaN。"""
    universe = pd.Index(["A.SZ"], name="ts_code")
    curr = pd.Series([10.0], index=universe)
    mi = pd.MultiIndex.from_tuples([("A.SZ", date(2025, 1, 2))], names=["ts_code", "trade_date"])
    hist = pd.DataFrame({"pb": [1.0]}, index=mi)
    out = _compute_historical_percentile(universe, curr, hist, "pe_ttm")
    assert out.isna().all()


def test_pct_curr_nan_and_missing_code_and_empty_series() -> None:
    """curr NaN / code 不在历史 / 该 code 历史全 NaN → 三类均 NaN，仅正常 code 有值。"""
    universe = pd.Index(["A.SZ", "B.SZ", "C.SZ", "D.SZ"], name="ts_code")
    # A: curr NaN；B: 历史缺该 code；C: 历史该 code 全 NaN；D: 正常
    curr = pd.Series([float("nan"), 10.0, 10.0, 10.0], index=universe)
    tuples = (
        [("C.SZ", date(2025, 1, i + 1)) for i in range(3)]
        + [("D.SZ", date(2025, 1, i + 1)) for i in range(3)]
    )
    mi = pd.MultiIndex.from_tuples(tuples, names=["ts_code", "trade_date"])
    hist = pd.DataFrame(
        {"pe_ttm": [float("nan"), float("nan"), float("nan"), 5.0, 8.0, 12.0]},
        index=mi,
    )
    out = _compute_historical_percentile(universe, curr, hist, "pe_ttm")
    assert pd.isna(out["A.SZ"])  # curr NaN
    assert pd.isna(out["B.SZ"])  # code 不在历史
    assert pd.isna(out["C.SZ"])  # 历史全 NaN → dropna 后空
    assert not pd.isna(out["D.SZ"])  # 正常


# ---------------------------------------------------------------------------
# ValueStrategy compute_raw_factors / score / _build_reason 降级
# ---------------------------------------------------------------------------

def _value_snapshot(*, with_roe: bool, with_industry: bool) -> dict:
    universe = pd.Index(["A.SZ", "B.SZ"], name="ts_code")
    daily_quotes = pd.DataFrame(
        {"pe_ttm": [10.0, 20.0], "pb": [1.0, 2.0]}, index=universe
    )
    fin_cols: dict[str, list] = {}
    if with_roe:
        fin_cols["roe"] = [8.0, 15.0]
    if with_industry:
        fin_cols["sw_industry_l1"] = ["制造", "制造"]
    financials = pd.DataFrame(fin_cols or {"_dummy": [0, 0]}, index=universe)
    mi = pd.MultiIndex.from_tuples(
        [(c, date(2025, 1, i + 1)) for c in universe for i in range(3)],
        names=["ts_code", "trade_date"],
    )
    pe_pb_history = pd.DataFrame(
        {"pe_ttm": [9, 10, 11, 19, 20, 21], "pb": [1, 1, 1, 2, 2, 2]}, index=mi
    )
    return {
        "trade_date": date(2025, 1, 31),
        "daily_quotes": daily_quotes,
        "financials": financials,
        "pe_pb_history": pe_pb_history,
    }


def test_value_compute_raw_factors_missing_roe_column() -> None:
    """financials 无 roe 列 → roe_quality 全 NaN（降级占位）。"""
    universe = pd.Index(["A.SZ", "B.SZ"], name="ts_code")
    df = ValueStrategy().compute_raw_factors(
        universe, _value_snapshot(with_roe=False, with_industry=False)
    )
    assert df["roe_quality"].isna().all()


def test_value_score_missing_industry_skips_value_trap() -> None:
    """缺 sw_industry_l1 → 跳过价值陷阱规避（返回原始 score，无截断）。"""
    universe = pd.Index(["A.SZ", "B.SZ"], name="ts_code")
    scores = ValueStrategy().score(
        universe, _value_snapshot(with_roe=True, with_industry=False)
    )
    assert len(scores) == 2
    # 未触发「得分已限制在50」reason
    assert all("限制在50" not in s.reason for s in scores)


def test_value_build_reason_fair_label() -> None:
    """pe_percentile 介于 0.3~0.5 → label='合理'。"""
    row = pd.Series({"pe_percentile": 0.4, "pb_percentile": 0.4, "roe_quality": 12.0})
    reason = ValueStrategy()._build_reason("A.SZ", row, 60.0)
    assert "合理" in reason


# ---------------------------------------------------------------------------
# Trend / MeanReversion：数据不足（reindex 后全 NaN 行）→ NaN 因子
# ---------------------------------------------------------------------------

def _short_price_snapshot() -> tuple[pd.Index, dict]:
    """universe 含一个无价格历史的 code（reindex 后全 NaN，dropna 后长度 0）。"""
    universe = pd.Index(["HASDATA.SZ", "NODATA.SZ"], name="ts_code")
    cols = pd.date_range("2025-01-01", periods=10, freq="B").date.tolist()
    adj = pd.DataFrame(
        {c: [10.0 + i] for i, c in enumerate(cols)},
        index=pd.Index(["HASDATA.SZ"], name="ts_code"),
    )
    return universe, {"trade_date": date(2025, 1, 31), "adj_prices": adj}


def test_trend_insufficient_history_yields_nan() -> None:
    """trend：不足 65 日（NODATA 全 NaN / HASDATA 仅 10 日）→ 因子 NaN。"""
    universe, snap = _short_price_snapshot()
    df = TrendStrategy().compute_raw_factors(universe, snap)
    assert df.loc["NODATA.SZ"].isna().all()
    assert df.loc["HASDATA.SZ"].isna().all()  # 10 < 65


def test_mean_reversion_insufficient_history_yields_nan() -> None:
    """mean_reversion：不足 25 日 → 因子 NaN。"""
    universe, snap = _short_price_snapshot()
    df = MeanReversionStrategy().compute_raw_factors(universe, snap)
    assert df.loc["NODATA.SZ"].isna().all()
    assert df.loc["HASDATA.SZ"].isna().all()  # 10 < 25


# ---------------------------------------------------------------------------
# FactorPipeline.neutralize 降级（开关开但缺数据 / 行业全缺）→ 全 NaN
# ---------------------------------------------------------------------------

def _neut_values() -> tuple[pd.Series, dict[str, str]]:
    values = pd.Series([1.0, 2.0, 3.0], index=["A.SZ", "B.SZ", "C.SZ"], name="trend_z")
    industries = {"A.SZ": "制造", "B.SZ": "金融", "C.SZ": "科技"}
    return values, industries


def test_pipeline_market_cap_on_but_none_returns_nan() -> None:
    """neutralize_market_cap 开但 market_cap=None → 全 NaN 降级。"""
    values, industries = _neut_values()
    cfg = FactorPipelineConfig(neutralize_industry=True, neutralize_market_cap=True)
    out = FactorPipeline(cfg).neutralize(values, industries, market_cap=None)
    assert out.isna().all()


def test_pipeline_beta_on_but_none_returns_nan() -> None:
    """neutralize_beta 开但 beta=None → 全 NaN 降级。"""
    values, industries = _neut_values()
    cfg = FactorPipelineConfig(
        neutralize_industry=True, neutralize_market_cap=False, neutralize_beta=True
    )
    out = FactorPipeline(cfg).neutralize(values, industries, beta=None)
    assert out.isna().all()


def test_pipeline_all_industry_missing_returns_nan() -> None:
    """industry 全缺（空映射）→ notna 过滤后 df 空 → 全 NaN。"""
    values, _ = _neut_values()
    cfg = FactorPipelineConfig(neutralize_industry=True)
    out = FactorPipeline(cfg).neutralize(values, {}, market_cap=None)
    assert out.isna().all()
