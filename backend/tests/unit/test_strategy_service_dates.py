"""unit/test_strategy_service_dates.py: V1.0 整改 Batch 2 — B2-3 闰年回归。

StrategyService._build_market_snapshot 的 start_pepb 计算原使用 date(yr-N, m, d)，
当 trade_date=2-29 时构造 date(yr-5, 2, 29)（非闰年）→ ValueError →
评分流水线整体降级（5 年一次，2024/2028/...）。

修复后改用 trade_date - timedelta(days=365 * N)，永不抛 ValueError。
"""
from __future__ import annotations

from datetime import date, timedelta

_PE_PB_HISTORY_YEARS = 5


class TestLeapYearStartPepb:
    """trade_date=2-29 时 start_pepb 计算回归（FIN-MED-10）。"""

    def test_b2_3_leap_year_2024_no_value_error(self) -> None:
        """trade_date=2024-02-29 → start_pepb 计算不抛 ValueError，落在 2019 年。"""
        trade_date = date(2024, 2, 29)
        start_pepb = trade_date - timedelta(days=365 * _PE_PB_HISTORY_YEARS)
        assert start_pepb < trade_date
        assert start_pepb.year == 2019

    def test_b2_3_leap_year_2028_no_value_error(self) -> None:
        """trade_date=2028-02-29 → start_pepb 计算不抛 ValueError。"""
        trade_date = date(2028, 2, 29)
        start_pepb = trade_date - timedelta(days=365 * _PE_PB_HISTORY_YEARS)
        assert start_pepb < trade_date

    def test_b2_3_non_leap_year_baseline(self) -> None:
        """非 2-29 日期不受影响，start_pepb 落在 yr-5 范围内。"""
        trade_date = date(2026, 5, 1)
        start_pepb = trade_date - timedelta(days=365 * _PE_PB_HISTORY_YEARS)
        assert start_pepb.year == 2021
        assert (trade_date - start_pepb).days == 365 * _PE_PB_HISTORY_YEARS

    def test_b2_3_old_implementation_raises_value_error(self) -> None:
        """回归证据：旧实现 date(yr-5, 2, 29) 在 yr-5 非闰年时抛 ValueError。"""
        trade_date = date(2024, 2, 29)
        try:
            date(trade_date.year - _PE_PB_HISTORY_YEARS, trade_date.month, trade_date.day)
        except ValueError as exc:
            assert "day is out of range" in str(exc)
        else:
            raise AssertionError(
                "旧实现应抛 ValueError（2019-02-29 非闰年），未抛说明回归测试失效"
            )
