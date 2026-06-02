"""交易日历入库单元测试（CAL）。

UT-CAL-01：TradingCalendar.from_repo 从（fake）repo 加载开市日构造日历。
UT-CAL-02：build_calendar_rows 把开市日列表 + 范围重建为「全历法日 + is_open」行。
UT-CAL-03：missing_trading_days 对开市日与实际入库日做差集。
"""
from __future__ import annotations

from datetime import date

from quantpilot.data.calendar import (
    TradingCalendar,
    build_calendar_rows,
    missing_trading_days,
    resolve_audit_range,
)


class _FakeCalRepo:
    """仅实现 get_trade_calendar_dates 的最小 fake，验证 from_repo 接线。"""

    def __init__(self, open_dates: list[date]) -> None:
        self._open = sorted(open_dates)
        self.calls: list[tuple] = []

    async def get_trade_calendar_dates(
        self, start: date, end: date, *, only_open: bool = True, exchange: str = "SSE"
    ) -> list[date]:
        self.calls.append((start, end, only_open, exchange))
        return [d for d in self._open if start <= d <= end]


async def test_cal_01_from_repo_builds_calendar() -> None:
    """UT-CAL-01：from_repo 用 repo 返回的开市日构造可用 TradingCalendar。"""
    open_days = [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]
    repo = _FakeCalRepo(open_days)

    cal = await TradingCalendar.from_repo(repo, date(2026, 1, 1), date(2026, 1, 10))

    assert isinstance(cal, TradingCalendar)
    assert cal.is_trade_date(date(2026, 1, 2)) is True
    # 1/3、1/4 为周末，非开市日
    assert cal.is_trade_date(date(2026, 1, 3)) is False
    assert cal.get_trade_dates(date(2026, 1, 1), date(2026, 1, 10)) == open_days
    # 只查询 only_open 主路径
    assert repo.calls[0][2] is True


def test_cal_02_build_calendar_rows_full_days_with_is_open() -> None:
    """UT-CAL-02：每个自然日一行，开市日 is_open=True，其余 False。"""
    open_days = [date(2026, 1, 2), date(2026, 1, 5)]
    rows = build_calendar_rows(open_days, date(2026, 1, 1), date(2026, 1, 5), exchange="SSE")

    # 1/1..1/5 共 5 个自然日，逐日一行
    assert [r["cal_date"] for r in rows] == [
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 3),
        date(2026, 1, 4),
        date(2026, 1, 5),
    ]
    open_map = {r["cal_date"]: r["is_open"] for r in rows}
    assert open_map[date(2026, 1, 2)] is True
    assert open_map[date(2026, 1, 5)] is True
    assert open_map[date(2026, 1, 1)] is False  # 元旦
    assert open_map[date(2026, 1, 3)] is False  # 周六
    assert all(r["exchange"] == "SSE" for r in rows)


def test_cal_03_missing_trading_days_diff() -> None:
    """UT-CAL-03：开市日中未出现在 present 集合的即缺口（升序）。"""
    open_days = [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
    present = {date(2026, 1, 2), date(2026, 1, 6)}

    missing = missing_trading_days(open_days, present)

    assert missing == [date(2026, 1, 5), date(2026, 1, 7)]


def test_cal_03b_missing_trading_days_none() -> None:
    """UT-CAL-03b：全部开市日均入库 → 无缺口。"""
    open_days = [date(2026, 1, 2), date(2026, 1, 5)]
    present = {date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 9)}
    assert missing_trading_days(open_days, present) == []


def test_cal_04_resolve_audit_range_caps_to_data_span() -> None:
    """UT-CAL-04：默认范围 = 数据实际区间，夹在日历内（评审 CAL-C-01）。"""
    coverage = (date(2020, 6, 1), date(2026, 8, 30))  # 日历：含早于回填起点 + 未来前瞻
    data_min, data_max = date(2021, 5, 13), date(2026, 5, 29)  # daily_quote 实际区间

    # 无参 → 取数据区间，不含 2020-06（早于回填起点）也不含 2026-08（未来未采集）
    start, end = resolve_audit_range(None, None, coverage, data_min, data_max)
    assert start == date(2021, 5, 13)
    assert end == date(2026, 5, 29)


def test_cal_04b_resolve_audit_range_explicit_args_win() -> None:
    """UT-CAL-04b：显式 --start/--end 优先于默认数据区间。"""
    coverage = (date(2020, 6, 1), date(2026, 8, 30))
    start, end = resolve_audit_range(
        date(2022, 1, 1), date(2022, 12, 31), coverage, date(2021, 5, 13), date(2026, 5, 29)
    )
    assert (start, end) == (date(2022, 1, 1), date(2022, 12, 31))


def test_cal_04c_resolve_audit_range_no_data_falls_back_to_coverage() -> None:
    """UT-CAL-04c：数据区间缺失（None）时退回日历 coverage 边界。"""
    coverage = (date(2021, 5, 13), date(2026, 5, 29))
    start, end = resolve_audit_range(None, None, coverage, None, None)
    assert (start, end) == coverage
