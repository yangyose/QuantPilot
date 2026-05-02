"""URF-F5r/F5r2/F7r/F7r2: UniverseFilter P5-PRE-4 恢复后的单元测试。"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from quantpilot.data.calendar import TradingCalendar
from quantpilot.engine.universe import UniverseFilter

TODAY = date(2026, 4, 8)


def _make_calendar(today: date = TODAY) -> TradingCalendar:
    """生成含 today 之前 90 个交易日的最简历 TradingCalendar。"""
    td = today
    result: list[date] = []
    while len(result) < 90:
        if td.weekday() < 5:
            result.append(td)
        td -= timedelta(days=1)
    return TradingCalendar(sorted(result))


def _make_stock_info(
    codes: list[str],
    *,
    sw_industry: str = "医药生物",
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "is_st": [False] * len(codes),
            "list_date": [date(2020, 1, 1)] * len(codes),
            "is_suspended": [False] * len(codes),
            "sw_industry_l1": [sw_industry] * len(codes),
        },
        index=pd.Index(codes, name="ts_code"),
    )


def _make_financials(
    codes: list[str],
    *,
    net_profit_yoy: float = 10.0,
    total_equity: float = 1e9,
    debt_to_asset: float = 0.5,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "total_equity": [total_equity] * len(codes),
            "net_profit_yoy": [net_profit_yoy] * len(codes),
            "debt_to_asset": [debt_to_asset] * len(codes),
        },
        index=pd.Index(codes, name="ts_code"),
    )


def _make_financials_history(
    codes: list[str],
    yoy_per_period: list[float],
) -> pd.DataFrame:
    """构造 MultiIndex (ts_code, report_period) 的财务历史 DataFrame。

    yoy_per_period: 按期从最新到最旧排列的 net_profit_yoy 值列表。
    """
    rows = []
    periods = [date(2024, 12, 31), date(2024, 9, 30)]
    for code in codes:
        for i, yoy in enumerate(yoy_per_period):
            if i < len(periods):
                rows.append({
                    "ts_code": code,
                    "report_period": periods[i],
                    "net_profit_yoy": yoy,
                })
    if not rows:
        return pd.DataFrame(columns=["net_profit_yoy"])
    df = pd.DataFrame(rows).set_index(["ts_code", "report_period"])
    return df


def _make_daily_quotes(
    codes: list[str],
    *,
    avg_amount: float | None = None,
    amount: float = 10_000_000.0,
) -> pd.DataFrame:
    data: dict = {
        "amount": [amount] * len(codes),
        "vol": [100_000] * len(codes),
        "limit_up": [False] * len(codes),
    }
    if avg_amount is not None:
        data["avg_amount"] = [avg_amount] * len(codes)
    return pd.DataFrame(data, index=pd.Index(codes, name="ts_code"))


f = UniverseFilter()
cal = _make_calendar()


# ---------------------------------------------------------------------------
# URF-F5r: 仅一期财务数据 → 降级为单期（不报错，盈利股通过）
# ---------------------------------------------------------------------------
def test_urf_f5r_single_period_degrades_gracefully() -> None:
    """URF-F5r: financials_history 只有 1 期，且盈利 → 通过 F-5"""
    codes = ["000001.SZ"]
    stock_info = _make_stock_info(codes)
    financials = _make_financials(codes, net_profit_yoy=5.0)
    # 只提供 1 期，net_profit_yoy=5.0（盈利）
    history = _make_financials_history(codes, yoy_per_period=[5.0])
    daily = _make_daily_quotes(codes, avg_amount=10_000_000.0)

    universe = f.filter(
        stock_info=stock_info,
        financials=financials,
        daily_quotes=daily,
        today=TODAY,
        calendar=cal,
        financials_history=history,
    )
    assert "000001.SZ" in universe


# ---------------------------------------------------------------------------
# URF-F5r2: 两期均有盈利 → 通过 F-5
# ---------------------------------------------------------------------------
def test_urf_f5r2_two_profitable_periods_pass() -> None:
    """URF-F5r2: financials_history 有 2 期，均盈利 → 通过 F-5"""
    codes = ["000002.SZ"]
    stock_info = _make_stock_info(codes)
    financials = _make_financials(codes, net_profit_yoy=8.0)
    history = _make_financials_history(codes, yoy_per_period=[8.0, 5.0])
    daily = _make_daily_quotes(codes, avg_amount=10_000_000.0)

    universe = f.filter(
        stock_info=stock_info,
        financials=financials,
        daily_quotes=daily,
        today=TODAY,
        calendar=cal,
        financials_history=history,
    )
    assert "000002.SZ" in universe


# ---------------------------------------------------------------------------
# URF-F7r: avg_amount<500万 → 被 F-7 过滤（20日均成交额不足）
# ---------------------------------------------------------------------------
def test_urf_f7r_low_avg_amount_filtered() -> None:
    """URF-F7r: daily_quotes 含 avg_amount 列且 < 500万 → F-7 过滤"""
    codes = ["000003.SZ"]
    stock_info = _make_stock_info(codes)
    financials = _make_financials(codes)
    daily = _make_daily_quotes(codes, avg_amount=3_000_000.0)  # 低于 500 万

    universe = f.filter(
        stock_info=stock_info,
        financials=financials,
        daily_quotes=daily,
        today=TODAY,
        calendar=cal,
    )
    assert "000003.SZ" not in universe


# ---------------------------------------------------------------------------
# URF-F7r2: avg_amount>=500万 → 通过 F-7
# ---------------------------------------------------------------------------
def test_urf_f7r2_sufficient_avg_amount_passes() -> None:
    """URF-F7r2: avg_amount=800万 >= 500万 → 通过 F-7 流动性过滤"""
    codes = ["000004.SZ"]
    stock_info = _make_stock_info(codes)
    financials = _make_financials(codes)
    daily = _make_daily_quotes(codes, avg_amount=8_000_000.0)

    universe = f.filter(
        stock_info=stock_info,
        financials=financials,
        daily_quotes=daily,
        today=TODAY,
        calendar=cal,
    )
    assert "000004.SZ" in universe
