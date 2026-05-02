"""CAL-01~07: TradingCalendar 单元测试"""
from datetime import date
from unittest.mock import AsyncMock

import pytest

from quantpilot.data.calendar import TradingCalendar

# 2026 年已知交易日（元旦 2026-01-01 为非交易日，2026-01-02 为首个交易日）
TRADE_DATES = [
    date(2026, 1, 2),   # 周五
    date(2026, 1, 5),   # 周一
    date(2026, 1, 6),
    date(2026, 1, 7),
    date(2026, 1, 8),
    date(2026, 1, 9),   # 周五
    date(2026, 1, 12),  # 周一
    date(2026, 1, 13),
    date(2026, 1, 14),
    date(2026, 1, 15),
]


@pytest.fixture
def cal() -> TradingCalendar:
    return TradingCalendar(TRADE_DATES)


def test_cal_01_is_trade_date_true(cal: TradingCalendar) -> None:
    """CAL-01: 已知交易日返回 True"""
    assert cal.is_trade_date(date(2026, 1, 2)) is True


def test_cal_02_is_trade_date_false_holiday(cal: TradingCalendar) -> None:
    """CAL-02: 节假日返回 False"""
    assert cal.is_trade_date(date(2026, 1, 1)) is False


def test_cal_03_get_prev_trade_date(cal: TradingCalendar) -> None:
    """CAL-03: get_prev_trade_date(n=1) 返回上一个交易日"""
    # 2026-01-05（周一）的前一个交易日是 2026-01-02（周五）
    assert cal.get_prev_trade_date(date(2026, 1, 5)) == date(2026, 1, 2)


def test_cal_04_get_trade_dates(cal: TradingCalendar) -> None:
    """CAL-04: get_trade_dates 返回 [start, end] 范围内全部交易日"""
    result = cal.get_trade_dates(date(2026, 1, 2), date(2026, 1, 9))
    assert result == [
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 8),
        date(2026, 1, 9),
    ]


def test_cal_05_count_trade_days(cal: TradingCalendar) -> None:
    """CAL-05: count_trade_days 跨节假日区间计数正确"""
    # 2026-01-01（非交易日）到 2026-01-05：含 1/2 和 1/5 两个交易日
    assert cal.count_trade_days(date(2026, 1, 1), date(2026, 1, 5)) == 2


def test_cal_06_offset_trade_date(cal: TradingCalendar) -> None:
    """CAL-06: offset_trade_date(d, 5) 向后偏移 5 个交易日"""
    # 从 2026-01-02 向后 5：1/5, 1/6, 1/7, 1/8, 1/9 → 2026-01-09
    assert cal.offset_trade_date(date(2026, 1, 2), 5) == date(2026, 1, 9)


@pytest.mark.asyncio
async def test_cal_07_from_adapter_delegates_to_fetch_trade_calendar() -> None:
    """CAL-07: from_adapter() 委托 adapter.fetch_trade_calendar，结果构成有效日历"""
    dates = [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]
    adapter = AsyncMock()
    adapter.fetch_trade_calendar = AsyncMock(return_value=dates)

    cal = await TradingCalendar.from_adapter(
        adapter, date(2026, 1, 1), date(2026, 1, 31)
    )

    adapter.fetch_trade_calendar.assert_called_once_with(date(2026, 1, 1), date(2026, 1, 31))
    assert cal.is_trade_date(date(2026, 1, 2)) is True
    assert cal.is_trade_date(date(2026, 1, 3)) is False
    assert cal.get_trade_dates(date(2026, 1, 1), date(2026, 1, 31)) == dates
