"""UT-P12-B-01~06: 多因子回归归因 OLS 纯函数测试（Phase 12 P12-B1）。

依据 phase12_factor_lineage.md §3.2.1 + §6.1：
- 01: 样本 < 10 × factor → None
- 02: 矩阵奇异 → None（不抛 LinAlgError）
- 03: 大样本 + 真实 β 回归 → 系数 ±0.005 容差（n=5000 + seed=42；评审 P2-2 修订）
- 04: AttributionResult 字段完整（coefficients/t_stats/residual_std/r_squared/sample_size）
- 05: AttributionService.get_summary 区间聚合（评审 P2-11）
- 06: Phase 13 P1-4：AttributionService.run_monthly 严格交易日 lookback
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from quantpilot.engine.attribution.regression import AttributionResult, run_ols

_FACTORS = ["trend", "momentum", "mean_reversion", "value"]


def test_ut_p12_b_01_run_ols_returns_none_when_sample_too_small() -> None:
    """UT-P12-B-01: 样本 < 10 × factor 时返回 None。"""
    rng = np.random.default_rng(42)
    n = 35  # < 10 * 4 = 40
    index = pd.MultiIndex.from_tuples(
        [("2026-05-12", f"S{i}") for i in range(n)], names=["date", "ts_code"]
    )
    exposures = pd.DataFrame(
        rng.standard_normal((n, len(_FACTORS))), index=index, columns=_FACTORS
    )
    returns = pd.Series(rng.standard_normal(n), index=index)

    result = run_ols(exposures, returns)
    assert result is None


def test_ut_p12_b_02_run_ols_returns_none_when_matrix_singular() -> None:
    """UT-P12-B-02: 因子矩阵完全共线（singular）→ run_ols 返回 None，不抛 LinAlgError。"""
    rng = np.random.default_rng(42)
    n = 100
    index = pd.MultiIndex.from_tuples(
        [("2026-05-12", f"S{i}") for i in range(n)], names=["date", "ts_code"]
    )
    base = rng.standard_normal(n)
    # 4 因子全等 → 设计矩阵秩 = 1，加 const 后秩 = 2 < 5 列 → singular
    exposures = pd.DataFrame(
        {factor: base for factor in _FACTORS}, index=index,
    )
    returns = pd.Series(rng.standard_normal(n), index=index)

    result = run_ols(exposures, returns)
    assert result is None


def test_ut_p12_b_03_run_ols_recovers_known_betas() -> None:
    """UT-P12-B-03: 大样本 + 已知真实 β → 回归系数 ±0.005 容差。

    评审 P2-2 修订：n=5000 + seed=42 + ±0.005（原 n=40 + ±0.01 与 OLS 系数 SE
    不匹配会高概率失败）。
    """
    rng = np.random.default_rng(42)
    n = 5000
    true_betas = {"trend": 0.05, "momentum": 0.03, "mean_reversion": -0.02, "value": 0.04}

    index = pd.MultiIndex.from_tuples(
        [(f"2026-{month:02d}-01", f"S{i:04d}") for month in range(1, 13)
         for i in range(n // 12 + 1)][:n],
        names=["date", "ts_code"],
    )
    exposures = pd.DataFrame(
        rng.standard_normal((n, len(_FACTORS))), index=index, columns=_FACTORS,
    )
    noise = rng.standard_normal(n) * 0.01
    y_values = sum(true_betas[f] * exposures[f].to_numpy() for f in _FACTORS) + noise
    returns = pd.Series(y_values, index=index)

    result = run_ols(exposures, returns)
    assert result is not None
    for factor, true_b in true_betas.items():
        assert abs(result.coefficients[factor] - true_b) < 0.005, (
            f"{factor}: actual {result.coefficients[factor]:.6f} vs true {true_b:.4f}"
        )


async def test_ut_p12_b_05_get_summary_aggregates_correctly() -> None:
    """UT-P12-B-05: AttributionService.get_summary 区间聚合正确性。

    评审 P2-11：E2E-P12-B-03 只用 mock summary 验外壳，未单测内部聚合：
    - months_seen 去重：同 calc_date 多 factor 行的 sample_size 只计入一次
    - cum_beta：按 factor 全月累加
    - avg_r_squared：按月度（calc_date 单一）只统计一次
    """
    from datetime import date as _date
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from quantpilot.services.attribution_service import (
        _STRATEGIES,
        AttributionService,
    )

    # 2 个月 × 4 因子 = 8 行；同 calc_date 同 sample_size / 同 r_squared
    rows = []
    for month_offset, calc_date in enumerate([_date(2026, 3, 31), _date(2026, 4, 30)]):
        for i, factor in enumerate(_STRATEGIES):
            rows.append(SimpleNamespace(
                calc_date=calc_date,
                factor=factor,
                beta=0.01 * (i + 1),
                t_stat=1.5,
                residual_std=0.012,
                r_squared=0.05 + 0.01 * month_offset,
                sample_size=1000 + 100 * month_offset,
                window_days=20,
            ))

    mock_repo = AsyncMock()
    mock_repo.get_attribution_by_date_range = AsyncMock(return_value=rows)
    svc = AttributionService(session=AsyncMock(), repo=mock_repo)

    summary = await svc.get_summary(_date(2026, 3, 1), _date(2026, 4, 30))

    # cum_beta：每因子两月累加 = 2 × 0.01 × (i+1)
    for i, f in enumerate(_STRATEGIES):
        expected = 0.02 * (i + 1)
        assert abs(summary.cum_beta[f] - expected) < 1e-9, (
            f"{f}: got {summary.cum_beta[f]}, expected {expected}"
        )
    # months：2 个独立 calc_date
    assert summary.months == 2
    # total_sample：每月计一次 = 1000 + 1100 = 2100（不是 8 行各加 → 4400 + 4800）
    assert summary.total_sample == 1000 + 1100
    # avg_r_squared：(0.05 + 0.06) / 2 = 0.055
    assert summary.avg_r_squared is not None
    assert abs(summary.avg_r_squared - 0.055) < 1e-9


async def test_ut_p12_b_06_run_monthly_uses_trading_calendar_when_available() -> None:
    """UT-P12-B-06: Phase 13 启动核查 P1-4：AttributionService.run_monthly
    在注入 TradingCalendar 时用严格交易日 lookback（20 × lookback_months），
    未注入时 fallback 到日历天近似（30.5 × n）。
    """
    from datetime import date as _date
    from unittest.mock import AsyncMock

    from quantpilot.data.calendar import TradingCalendar
    from quantpilot.services.attribution_service import AttributionService

    # 构造 12 个月窗口的交易日列表：2025-04-01 起每月约 21 个交易日
    trade_dates: list[_date] = []
    cur = _date(2025, 4, 1)
    while cur <= _date(2026, 5, 31):
        if cur.weekday() < 5:
            trade_dates.append(cur)
        cur = cur + timedelta(days=1)
    calendar = TradingCalendar(trade_dates)

    month_end = _date(2026, 4, 30)
    expected_strict = calendar.get_prev_trade_date(month_end, n=20 * 12)

    # 严格路径：calendar 注入 → start 来自 calendar.get_prev_trade_date
    captured_start: dict[str, _date] = {}

    class _FakeSession:
        async def execute(self, stmt):
            # 抓取 WHERE 子句中 >= start 的字面值
            # 简化起见，直接返回空 result 触发 no_pool_rows 早退路径
            class _R:
                def all(self):
                    return []
            return _R()

    svc_strict = AttributionService(
        session=_FakeSession(),  # type: ignore[arg-type]
        repo=AsyncMock(),
        calendar=calendar,
        lookback_months=12,
    )
    # 通过 patch 方式抓 start
    original_get_prev = calendar.get_prev_trade_date

    def _spy(d, n):
        captured_start["d"] = d
        captured_start["n"] = n
        return original_get_prev(d, n)
    calendar.get_prev_trade_date = _spy  # type: ignore[method-assign]
    result_strict = await svc_strict.run_monthly(month_end)
    assert result_strict == []
    assert captured_start["d"] == month_end
    assert captured_start["n"] == 20 * 12
    _ = expected_strict  # 仅断言路径被走到

    # Fallback 路径：calendar=None → 走 timedelta(30.5×n)
    svc_fallback = AttributionService(
        session=_FakeSession(),  # type: ignore[arg-type]
        repo=AsyncMock(),
        calendar=None,
        lookback_months=12,
    )
    result_fallback = await svc_fallback.run_monthly(month_end)
    assert result_fallback == []


def test_ut_p12_b_04_attribution_result_fields_complete() -> None:
    """UT-P12-B-04: AttributionResult 字段完整。

    含 coefficients / t_stats / residual_std / r_squared / sample_size 五项。
    """
    rng = np.random.default_rng(42)
    n = 200
    index = pd.MultiIndex.from_tuples(
        [("2026-05-12", f"S{i}") for i in range(n)], names=["date", "ts_code"]
    )
    exposures = pd.DataFrame(
        rng.standard_normal((n, len(_FACTORS))), index=index, columns=_FACTORS,
    )
    returns = pd.Series(rng.standard_normal(n), index=index)

    result = run_ols(exposures, returns)
    assert isinstance(result, AttributionResult)
    assert set(result.coefficients.keys()) == set(_FACTORS)
    assert set(result.t_stats.keys()) == set(_FACTORS)
    assert isinstance(result.residual_std, float)
    assert 0.0 <= result.r_squared <= 1.0
    assert result.sample_size == n
