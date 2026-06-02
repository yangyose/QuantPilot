"""INT-CAL-01：trade_calendar repo 往返 + from_repo + 差集核验（需真实 PostgreSQL）。

用独立 exchange 'TST' 隔离，避免与其他用例/真实 SSE 数据相互污染。
"""
from datetime import date

import pytest

from quantpilot.data.calendar import (
    TradingCalendar,
    build_calendar_rows,
    missing_trading_days,
)
from quantpilot.data.repository import MarketDataRepository

_EX = "TST"


@pytest.fixture
def repo(db_session):
    return MarketDataRepository(db_session)


async def test_int_cal_01_roundtrip_and_diff(repo: MarketDataRepository) -> None:
    open_days = [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]
    rows = build_calendar_rows(open_days, date(2026, 1, 1), date(2026, 1, 6), exchange=_EX)

    written = await repo.upsert_trade_calendar(rows)
    assert written == 6  # 每个自然日一行

    # only_open=True 仅开市日
    got_open = await repo.get_trade_calendar_dates(
        date(2026, 1, 1), date(2026, 1, 6), only_open=True, exchange=_EX
    )
    assert got_open == open_days

    # only_open=False 全历法日
    got_all = await repo.get_trade_calendar_dates(
        date(2026, 1, 1), date(2026, 1, 6), only_open=False, exchange=_EX
    )
    assert len(got_all) == 6

    # 覆盖范围
    coverage = await repo.get_trade_calendar_coverage(exchange=_EX)
    assert coverage == (date(2026, 1, 1), date(2026, 1, 6))

    # from_repo 构造可用日历
    cal = await TradingCalendar.from_repo(
        repo, date(2026, 1, 1), date(2026, 1, 6), exchange=_EX
    )
    assert cal.is_trade_date(date(2026, 1, 5)) is True
    assert cal.is_trade_date(date(2026, 1, 3)) is False  # 周六

    # 差集核验：present 缺 1/5 → missing 命中
    present = {date(2026, 1, 2), date(2026, 1, 6)}
    assert missing_trading_days(got_open, present) == [date(2026, 1, 5)]


async def test_int_cal_02_upsert_idempotent_update(repo: MarketDataRepository) -> None:
    """重复 upsert 同 (exchange, cal_date) → is_open 被更新（幂等覆盖）。"""
    await repo.upsert_trade_calendar(
        [{"exchange": _EX, "cal_date": date(2026, 2, 16), "is_open": False}]
    )
    # 改判为开市日重灌
    await repo.upsert_trade_calendar(
        [{"exchange": _EX, "cal_date": date(2026, 2, 16), "is_open": True}]
    )
    got = await repo.get_trade_calendar_dates(
        date(2026, 2, 16), date(2026, 2, 16), only_open=True, exchange=_EX
    )
    assert got == [date(2026, 2, 16)]
