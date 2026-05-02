"""REPO-01~04: MarketDataRepository 集成测试（需要真实 PostgreSQL）"""
from datetime import date

import pandas as pd
import pytest

from quantpilot.data.repository import MarketDataRepository


@pytest.fixture
def repo(db_session):
    return MarketDataRepository(db_session)


@pytest.mark.asyncio
async def test_repo_01_upsert_stock_list(repo: MarketDataRepository) -> None:
    """REPO-01: upsert_stock_list 批量插入 → 查询确认行数"""
    df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "name": ["平安银行", "万科A"],
            "market": ["MAIN", "MAIN"],
            "sw_industry_l1": [None, None],
            "sw_industry_l2": [None, None],
            "list_date": [date(1991, 4, 3), date(1991, 1, 29)],
            "delist_date": [None, None],
            "is_active": [True, True],
        }
    )
    count = await repo.upsert_stock_list(df)
    assert count == 2

    codes = await repo.get_active_stock_codes()
    assert "000001.SZ" in codes
    assert "000002.SZ" in codes


@pytest.mark.asyncio
async def test_repo_02_upsert_daily_quotes_idempotent(repo: MarketDataRepository) -> None:
    """REPO-02: 重复 upsert 同一天数据 → 不报错，数据被更新（幂等性）"""
    df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": [date(2026, 1, 2)],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "pre_close": [10.0],
            "pct_chg": [0.05],
            "vol": [100_000],
            "amount": [1_000_000.0],
            "turnover_rate": [0.01],
            "float_mkt_cap": [1e10],
            "adj_factor": [1.0],
            "is_suspended": [False],
            "is_st": [False],
            "limit_up": [False],
            "limit_down": [False],
        }
    )
    count1 = await repo.upsert_daily_quotes(df)
    assert count1 == 1

    # 第二次 upsert（更新 close）
    df2 = df.copy()
    df2["close"] = [10.8]
    count2 = await repo.upsert_daily_quotes(df2)
    assert count2 == 1  # 仍返回 1（upsert 行数）


@pytest.mark.asyncio
async def test_repo_03_get_latest_financial_pit(repo: MarketDataRepository) -> None:
    """REPO-03: get_latest_financial PIT 查询 → 不返回 as_of_date 之后的公告"""
    df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "report_period": [date(2025, 6, 30), date(2025, 9, 30)],
            "publish_date": [date(2025, 8, 30), date(2026, 1, 5)],  # 第二条晚于 as_of
            "pe_ttm": [12.0, 13.0],
            "pb": [1.0, 1.1],
            "roe": [0.12, 0.13],
            "net_profit_yoy": [0.1, 0.1],
            "revenue_yoy": [0.08, 0.09],
            "dividend_yield": [0.03, 0.03],
            "total_equity": [1e10, 1e10],
            "debt_to_asset": [0.5, 0.5],
        }
    )
    await repo.upsert_financial_data(df)

    as_of = date(2026, 1, 2)
    result = await repo.get_latest_financial(["000001.SZ"], as_of_date=as_of)

    assert len(result) == 1
    # 只能拿到 publish_date=2025-08-30 的记录
    assert result.iloc[0]["publish_date"] == date(2025, 8, 30)


@pytest.mark.asyncio
async def test_repo_04_upsert_index_history_ohlcv(repo: MarketDataRepository) -> None:
    """REPO-04: upsert_index_history + get_index_history 范围查询，含 high/low 字段"""
    df = pd.DataFrame(
        {
            "index_code": ["000300.SH", "000300.SH"],
            "trade_date": [date(2026, 1, 2), date(2026, 1, 5)],
            "open": [4000.0, 4010.0],
            "high": [4050.0, 4060.0],
            "low": [3980.0, 3990.0],
            "close": [4020.0, 4030.0],
            "vol": [1_000_000, 1_100_000],
            "pct_chg": [0.005, 0.0025],
        }
    )
    await repo.upsert_index_history(df)

    result = await repo.get_index_history("000300.SH", date(2026, 1, 1), date(2026, 1, 10))
    assert len(result) == 2
    assert "high" in result.columns
    assert "low" in result.columns
    assert float(result.iloc[0]["high"]) == pytest.approx(4050.0)
