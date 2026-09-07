"""URF-01~10: UniverseFilter 单元测试（纯函数，无 DB）。"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from quantpilot.data.calendar import TradingCalendar
from quantpilot.engine.universe import UniverseFilter

# ── 辅助工具 ─────────────────────────────────────────────────────────────────

def _trade_dates(n: int, end: date | None = None) -> list[date]:
    """生成 n 个连续工作日（跳过周末）。"""
    if end is None:
        end = date(2025, 1, 31)
    result: list[date] = []
    d = end
    while len(result) < n:
        if d.weekday() < 5:
            result.append(d)
        d -= timedelta(days=1)
    return sorted(result)


def _make_stock_info(
    codes: list[str],
    *,
    is_st: bool = False,
    list_date: date | None = None,
    is_suspended: bool = False,
    sw_industry_l1: str = "制造",
) -> pd.DataFrame:
    """生成测试用 stock_info DataFrame，所有行使用相同参数（可单独覆盖）。"""
    return pd.DataFrame(
        {
            "is_st": [is_st] * len(codes),
            "list_date": [list_date or date(2020, 1, 1)] * len(codes),
            "is_suspended": [is_suspended] * len(codes),
            "sw_industry_l1": [sw_industry_l1] * len(codes),
        },
        index=pd.Index(codes, name="ts_code"),
    )


def _make_financials(
    codes: list[str],
    *,
    total_equity: float | None = 1e9,
    net_profit_yoy: float | None = 10.0,
    debt_to_asset: float | None = 0.5,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "total_equity": [total_equity] * len(codes),
            "net_profit_yoy": [net_profit_yoy] * len(codes),
            "debt_to_asset": [debt_to_asset] * len(codes),
        },
        index=pd.Index(codes, name="ts_code"),
    )


def _make_daily_quotes(
    codes: list[str],
    *,
    amount: float = 1e7,          # 默认 1000 万，高于 500 万阈值
    vol: float = 1e4,
    limit_up: bool = False,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "amount": [amount] * len(codes),
            "vol": [vol] * len(codes),
            "limit_up": [limit_up] * len(codes),
        },
        index=pd.Index(codes, name="ts_code"),
    )


@pytest.fixture
def calendar() -> TradingCalendar:
    """覆盖 2019-01-01 ~ 2025-02-28 的工作日日历。"""
    start, end = date(2019, 1, 1), date(2025, 2, 28)
    days: list[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return TradingCalendar(days)


@pytest.fixture
def uf() -> UniverseFilter:
    return UniverseFilter()


TODAY = date(2025, 1, 31)
CODES = ["000001.SZ", "000002.SZ", "000003.SZ"]


# ── URF-01：ST 股票过滤 ────────────────────────────────────────────────────────

def test_urf_01_st_excluded(uf: UniverseFilter, calendar: TradingCalendar) -> None:
    """is_st=True 的股票不出现在结果中。"""
    stock_info = pd.concat([
        _make_stock_info(["000001.SZ"], is_st=False),
        _make_stock_info(["000002.SZ"], is_st=True),
    ])
    financials = _make_financials(["000001.SZ", "000002.SZ"])
    daily_quotes = _make_daily_quotes(["000001.SZ", "000002.SZ"])

    result = uf.filter(stock_info, financials, daily_quotes, TODAY, calendar)

    assert "000001.SZ" in result
    assert "000002.SZ" not in result


# ── URF-02：次新股过滤 ─────────────────────────────────────────────────────────

def test_urf_02_new_listing_excluded(uf: UniverseFilter, calendar: TradingCalendar) -> None:
    """上市不足 60 交易日的股票被排除。"""
    # 59 个交易日前上市 → 不足 60 → 排除
    too_new = calendar.get_prev_trade_date(TODAY, 59)
    # 60 个交易日前上市 → 恰好满足 → 保留
    just_ok = calendar.get_prev_trade_date(TODAY, 60)

    stock_info = pd.concat([
        _make_stock_info(["000001.SZ"], list_date=too_new),
        _make_stock_info(["000002.SZ"], list_date=just_ok),
    ])
    financials = _make_financials(["000001.SZ", "000002.SZ"])
    daily_quotes = _make_daily_quotes(["000001.SZ", "000002.SZ"])

    result = uf.filter(stock_info, financials, daily_quotes, TODAY, calendar)

    assert "000001.SZ" not in result
    assert "000002.SZ" in result


# ── URF-03：停牌过滤 ──────────────────────────────────────────────────────────

def test_urf_03_suspended_excluded(uf: UniverseFilter, calendar: TradingCalendar) -> None:
    """is_suspended=True 的股票被排除。"""
    stock_info = pd.concat([
        _make_stock_info(["000001.SZ"], is_suspended=False),
        _make_stock_info(["000002.SZ"], is_suspended=True),
    ])
    financials = _make_financials(["000001.SZ", "000002.SZ"])
    daily_quotes = _make_daily_quotes(["000001.SZ", "000002.SZ"])

    result = uf.filter(stock_info, financials, daily_quotes, TODAY, calendar)

    assert "000001.SZ" in result
    assert "000002.SZ" not in result


# ── URF-04：净资产为负过滤（含金融股豁免）────────────────────────────────────────

def test_urf_04_negative_equity_excluded(uf: UniverseFilter, calendar: TradingCalendar) -> None:
    """total_equity <= 0 时排除；金融股豁免。"""
    stock_info = pd.concat([
        _make_stock_info(["000001.SZ"], sw_industry_l1="制造"),
        _make_stock_info(["000002.SZ"], sw_industry_l1="制造"),
        _make_stock_info(["000003.SZ"], sw_industry_l1="银行"),    # 金融股豁免
    ])
    financials = pd.concat([
        _make_financials(["000001.SZ"], total_equity=1e9),          # 正常
        _make_financials(["000002.SZ"], total_equity=-1.0),         # 负净资产 → 排除
        _make_financials(["000003.SZ"], total_equity=-1.0),         # 银行豁免 → 保留
    ])
    daily_quotes = _make_daily_quotes(["000001.SZ", "000002.SZ", "000003.SZ"])

    result = uf.filter(stock_info, financials, daily_quotes, TODAY, calendar)

    assert "000001.SZ" in result
    assert "000002.SZ" not in result
    assert "000003.SZ" in result


# ── URF-05：连续亏损过滤（含金融股豁免）──────────────────────────────────────────

def test_urf_05_consecutive_loss_excluded(uf: UniverseFilter, calendar: TradingCalendar) -> None:
    """最近一期 net_profit_yoy < 0 时排除（单期降级实现，见 universe.py F-5 注释）；金融股豁免。"""
    stock_info = pd.concat([
        _make_stock_info(["000001.SZ"], sw_industry_l1="制造"),
        _make_stock_info(["000002.SZ"], sw_industry_l1="制造"),
        _make_stock_info(["000003.SZ"], sw_industry_l1="证券"),
    ])
    financials = pd.concat([
        _make_financials(["000001.SZ"], net_profit_yoy=5.0),        # 正常
        _make_financials(["000002.SZ"], net_profit_yoy=-5.0),       # 亏损 → 排除
        _make_financials(["000003.SZ"], net_profit_yoy=-5.0),       # 证券豁免 → 保留
    ])
    daily_quotes = _make_daily_quotes(["000001.SZ", "000002.SZ", "000003.SZ"])

    result = uf.filter(stock_info, financials, daily_quotes, TODAY, calendar)

    assert "000001.SZ" in result
    assert "000002.SZ" not in result
    assert "000003.SZ" in result


# ── URF-06：高杠杆过滤（含金融股豁免）────────────────────────────────────────────

def test_urf_06_high_leverage_excluded(uf: UniverseFilter, calendar: TradingCalendar) -> None:
    """debt_to_asset >= 0.9 时排除；金融股豁免。"""
    stock_info = pd.concat([
        _make_stock_info(["000001.SZ"], sw_industry_l1="制造"),
        _make_stock_info(["000002.SZ"], sw_industry_l1="制造"),
        _make_stock_info(["000003.SZ"], sw_industry_l1="保险"),
    ])
    financials = pd.concat([
        _make_financials(["000001.SZ"], debt_to_asset=0.5),         # 正常
        _make_financials(["000002.SZ"], debt_to_asset=0.95),        # 高杠杆 → 排除
        _make_financials(["000003.SZ"], debt_to_asset=0.95),        # 保险豁免 → 保留
    ])
    daily_quotes = _make_daily_quotes(["000001.SZ", "000002.SZ", "000003.SZ"])

    result = uf.filter(stock_info, financials, daily_quotes, TODAY, calendar)

    assert "000001.SZ" in result
    assert "000002.SZ" not in result
    assert "000003.SZ" in result


# ── URF-07：NULL 字段跳过（TD 修复前降级处理）──────────────────────────────────────

def test_urf_07_null_fields_skipped(uf: UniverseFilter, calendar: TradingCalendar) -> None:
    """F-4/F-5/F-6 对应字段为 NaN 时跳过该条件（不过滤），日志警告不抛异常。"""
    stock_info = _make_stock_info(["000001.SZ", "000002.SZ"])
    financials = pd.DataFrame(
        {
            "total_equity": [np.nan, np.nan],
            "net_profit_yoy": [np.nan, np.nan],
            "debt_to_asset": [np.nan, np.nan],
        },
        index=pd.Index(["000001.SZ", "000002.SZ"], name="ts_code"),
    )
    daily_quotes = _make_daily_quotes(["000001.SZ", "000002.SZ"])

    result = uf.filter(stock_info, financials, daily_quotes, TODAY, calendar)

    # NULL 字段 → 对应条件不过滤 → 两只都保留
    assert "000001.SZ" in result
    assert "000002.SZ" in result


# ── URF-08：组合场景（满足多条排除条件只过滤一次）────────────────────────────────────

def test_urf_08_multi_condition_excluded_once(
    uf: UniverseFilter, calendar: TradingCalendar
) -> None:
    """同时是 ST 且净资产为负，结果中出现一次排除，不报错。"""
    stock_info = _make_stock_info(["000001.SZ"], is_st=True)
    financials = _make_financials(["000001.SZ"], total_equity=-1.0)
    daily_quotes = _make_daily_quotes(["000001.SZ"])

    result = uf.filter(stock_info, financials, daily_quotes, TODAY, calendar)

    assert "000001.SZ" not in result


# ── URF-09：流动性过滤 ─────────────────────────────────────────────────────────

def test_urf_09_low_liquidity_excluded(uf: UniverseFilter, calendar: TradingCalendar) -> None:
    """当日成交额 < 500 万元时排除（单日降级实现，见 universe.py F-7 注释）；高于阈值的正常通过。"""
    stock_info = _make_stock_info(["000001.SZ", "000002.SZ"])
    financials = _make_financials(["000001.SZ", "000002.SZ"])
    daily_quotes = pd.concat([
        _make_daily_quotes(["000001.SZ"], amount=4_999_999),   # < 500万 → 排除
        _make_daily_quotes(["000002.SZ"], amount=5_000_001),   # > 500万 → 保留
    ])

    result = uf.filter(stock_info, financials, daily_quotes, TODAY, calendar)

    assert "000001.SZ" not in result
    assert "000002.SZ" in result


# ── URF-10：涨停封死过滤 ──────────────────────────────────────────────────────

def test_urf_10_limit_up_no_vol_excluded(uf: UniverseFilter, calendar: TradingCalendar) -> None:
    """limit_up=True 且 vol=0 时排除（无法买入）；有成交量的涨停股保留。"""
    stock_info = _make_stock_info(["000001.SZ", "000002.SZ"])
    financials = _make_financials(["000001.SZ", "000002.SZ"])
    daily_quotes = pd.concat([
        _make_daily_quotes(["000001.SZ"], limit_up=True, vol=0),     # 封死 → 排除
        _make_daily_quotes(["000002.SZ"], limit_up=True, vol=1000),  # 有成交 → 保留
    ])

    result = uf.filter(stock_info, financials, daily_quotes, TODAY, calendar)

    assert "000001.SZ" not in result
    assert "000002.SZ" in result


# ── URF-11：基本面覆盖率告警的阈值盲区（2026-09-07）──────────────────────────────

def _cov_case(uf, calendar, caplog, col: str, present: int, total: int = 200):
    """构造 `col` 只有 present/total 只有值的 financials，返回捕获的告警文本。"""
    codes = [f"{i:06d}.SZ" for i in range(total)]
    fin = _make_financials(codes)
    fin[col] = [fin[col].iloc[0]] * present + [float("nan")] * (total - present)
    with caplog.at_level("WARNING"):
        uf.filter(
            _make_stock_info(codes), fin, _make_daily_quotes(codes),
            TODAY, calendar,
        )
    return "\n".join(r.getMessage() for r in caplog.records)


def test_urf_11_low_coverage_warns_not_only_all_null(
    uf: UniverseFilter, calendar: TradingCalendar, caplog
) -> None:
    """1% 覆盖率必须告警——原实现只在**恰好 100% 全 NULL** 时才响。

    ⚠️ 这条钉的是阈值盲区，不是「有没有告警」。2026-09-07 于 5434 实测：
    `total_equity` 在 5 年 21 个采样日中有 **10 天**覆盖率低于 19%
    （最低 1.2%），而全 5y 面板 1114 日里只有 **18 天**触发了那条 WARNING
    ——因为它要求 100% 全 NULL。「F-4 对 98.8% 的股票不生效」在日志里
    与完全健康**长得一模一样**。§4.11 元判据：护栏在机制生效与失效时
    给出相同结果，它就不是护栏。

    判据同时要求把**真实覆盖率**打进日志：只说「跳过了」而不说「覆盖多少」，
    下次仍然只能分辨 0% 与非 0%。
    """
    msg = _cov_case(uf, calendar, caplog, "total_equity", present=2)
    assert "total_equity" in msg, "1% 覆盖率未告警 —— 阈值盲区仍在"
    assert "1.0%" in msg or "0.01" in msg, "告警未报出真实覆盖率"


def test_urf_11b_full_coverage_is_silent(
    uf: UniverseFilter, calendar: TradingCalendar, caplog
) -> None:
    """反向钉：覆盖率正常时不得刷告警（否则阈值等于没设）。"""
    msg = _cov_case(uf, calendar, caplog, "total_equity", present=200)
    assert "total_equity" not in msg


def test_urf_11c_covers_f5_and_f6_fields(
    uf: UniverseFilter, calendar: TradingCalendar, caplog
) -> None:
    """F-5 / F-6 用的是同一个 100% 阈值，同样要能报出低覆盖。

    这两个字段目前实测健康（92~100%），但盲区是结构性的：
    只钉 total_equity 的话，它们跌到 5% 时照样无声。
    """
    for col in ("net_profit_yoy", "debt_to_asset"):
        caplog.clear()
        msg = _cov_case(uf, calendar, caplog, col, present=4)
        assert col in msg, f"{col} 低覆盖未告警"
