"""UT-P13-D-01: Phase 13 AKShareAdapter.fetch_daily_quotes / fetch_index_history 单元测试。

依据 docs/design/phases/phase13_production_observability.md §3.6 + §6.1：
- UT-P13-D-01a: fetch_daily_quotes 字段映射 + 单位换算 + ts_code 必填守卫 + 大宇宙拒绝
- UT-P13-D-01b: fetch_index_history 字段映射 + symbol 转换 + 日期窗口过滤
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from quantpilot.data.adapters.akshare import AKShareAdapter


def _fake_stock_hist(symbol, period, start_date, end_date, adjust):
    """模拟 ak.stock_zh_a_hist 返回（中文列名）。"""
    return pd.DataFrame({
        "日期": [start_date],
        "开盘": [10.5],
        "收盘": [10.8],
        "最高": [10.9],
        "最低": [10.4],
        "成交量": [12345],  # 手
        "成交额": [13_180_000.0],  # 元
        "涨跌幅": [2.86],  # %
        "涨跌额": [0.30],
        "换手率": [3.45],  # %
    })


def _fake_index_daily(symbol):
    """模拟 ak.stock_zh_index_daily 返回。"""
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-05-20", "2026-05-21", "2026-05-22"]),
        "open": [4100.0, 4150.0, 4180.0],
        "high": [4180.0, 4200.0, 4210.0],
        "low": [4090.0, 4140.0, 4170.0],
        "close": [4150.0, 4180.0, 4200.0],
        "volume": [1_000_000, 1_100_000, 1_050_000],
    })


async def test_ut_p13_d_01a_fetch_daily_quotes_field_mapping() -> None:
    """UT-P13-D-01a: fetch_daily_quotes 字段映射 + 单位换算 + 守卫。"""
    adapter = AKShareAdapter()

    with patch("akshare.stock_zh_a_hist", side_effect=_fake_stock_hist):
        df = await adapter.fetch_daily_quotes(
            trade_date=date(2026, 5, 22),
            ts_codes=["000001.SZ", "600000.SH"],
        )

    assert len(df) == 2
    expected_cols = {
        "ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
        "pct_chg", "vol", "amount", "turnover_rate", "float_mkt_cap",
        "adj_factor", "is_suspended", "is_st", "limit_up", "limit_down",
    }
    assert expected_cols.issubset(set(df.columns))

    row = df.iloc[0]
    assert row["ts_code"] == "000001.SZ"
    assert row["open"] == pytest.approx(10.5)
    assert row["close"] == pytest.approx(10.8)
    assert row["pct_chg"] == pytest.approx(0.0286)  # % → 小数
    assert row["vol"] == pytest.approx(12345 * 100)  # 手 → 股
    assert row["amount"] == pytest.approx(13_180_000.0)  # 元（AKShare 已是元）
    assert row["turnover_rate"] == pytest.approx(0.0345)  # % → 小数
    assert row["adj_factor"] == 1.0  # AKShare 无此字段，置默认
    assert bool(row["is_st"]) is False
    assert bool(row["is_suspended"]) is False
    assert bool(row["limit_up"]) is False
    assert bool(row["limit_down"]) is False


async def test_ut_p13_d_01a_no_universe_raises_not_implemented() -> None:
    """ts_codes=None 拒绝（无全市场接口）。"""
    adapter = AKShareAdapter()
    with pytest.raises(NotImplementedError, match="ts_codes"):
        await adapter.fetch_daily_quotes(trade_date=date(2026, 5, 22), ts_codes=None)


async def test_ut_p13_d_01a_oversize_universe_raises() -> None:
    """超过 1000 只拒绝（避免无限拉取）。"""
    adapter = AKShareAdapter()
    huge = [f"{i:06d}.SZ" for i in range(1001)]
    with pytest.raises(NotImplementedError, match="1000"):
        await adapter.fetch_daily_quotes(trade_date=date(2026, 5, 22), ts_codes=huge)


async def test_ut_p13_d_01b_fetch_index_history_field_mapping() -> None:
    """UT-P13-D-01b: fetch_index_history symbol 转换 + 日期过滤 + vol 单位。"""
    adapter = AKShareAdapter()

    captured = {}

    def _spy(symbol):
        captured["symbol"] = symbol
        return _fake_index_daily(symbol)

    with patch("akshare.stock_zh_index_daily", side_effect=_spy):
        df = await adapter.fetch_index_history(
            index_code="000300.SH",
            start_date=date(2026, 5, 21),
            end_date=date(2026, 5, 22),
        )

    assert captured["symbol"] == "sh000300"
    assert len(df) == 2  # 5-20 被窗口过滤掉
    expected_cols = {"index_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg"}
    assert expected_cols.issubset(set(df.columns))
    assert df["index_code"].iloc[0] == "000300.SH"
    # vol 单位手→股（虽 fake 数据已是「手」，这里只断 ×100 转换正确）
    assert df["vol"].iloc[0] == pytest.approx(1_100_000 * 100)
    # pct_chg = (4200/4180) - 1 ≈ 0.00478
    assert df["pct_chg"].iloc[-1] == pytest.approx((4200 - 4180) / 4180, rel=1e-3)


async def test_ut_p13_d_01b_index_symbol_conversion() -> None:
    """index_code 转换规则单测。"""
    assert AKShareAdapter._akshare_index_symbol("000300.SH") == "sh000300"
    assert AKShareAdapter._akshare_index_symbol("000016.SH") == "sh000016"
    assert AKShareAdapter._akshare_index_symbol("399001.SZ") == "sz399001"
    assert AKShareAdapter._akshare_index_symbol("plain") == "plain"
