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
    # ts_code 是索引（RM-17 修复后 set_index）；publish_date 仍是 column
    assert result.index[0] == "000001.SZ"
    # 只能拿到 publish_date=2025-08-30 的记录
    assert result.iloc[0]["publish_date"] == date(2025, 8, 30)


async def test_repo_a5_forecast_upsert_and_pit(repo: MarketDataRepository) -> None:
    """REPO-A5（V1.5-A A5）：upsert_financial_forecast 幂等 + get_latest_forecast PIT。

    同 (ts_code, 20251231) 有 forecast(priority1) + express(priority2) →
    get_latest_forecast 取 data_priority 高者（express）；PIT 不返回 as_of 之后的发布。
    """
    df = pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
        "report_period": [date(2025, 12, 31), date(2025, 12, 31), date(2025, 9, 30)],
        "pre_announce_date": [date(2026, 2, 20), date(2026, 2, 28), date(2025, 10, 30)],
        "est_net_profit": [9.0e7, 1.0e8, 8.0e7],
        "est_net_profit_yoy": [0.18, 0.20, 0.10],
        "data_priority": [1, 2, 1],
        "source_type": ["forecast", "express", "forecast"],
    })
    n1 = await repo.upsert_financial_forecast(df)
    assert n1 == 3
    # 幂等：再 upsert 同数据不新增行
    await repo.upsert_financial_forecast(df)

    # as_of 覆盖到 2/28（express 已发）→ 取 20251231 express（priority 2 优先）
    result = await repo.get_latest_forecast(["000001.SZ"], as_of_date=date(2026, 3, 1))
    assert len(result) == 1
    assert result.index[0] == "000001.SZ"
    assert result.iloc[0]["report_period"] == date(2025, 12, 31)
    assert result.iloc[0]["source_type"] == "express"
    assert float(result.iloc[0]["est_net_profit"]) == 1.0e8

    # PIT：as_of 在 express 发布前（2/25）→ 取 20251231 forecast（唯一已发布该期）
    result2 = await repo.get_latest_forecast(["000001.SZ"], as_of_date=date(2026, 2, 25))
    assert result2.iloc[0]["source_type"] == "forecast"

    # PIT：as_of 早于所有 20251231 发布（1/1）→ 回退到 20250930 forecast
    result3 = await repo.get_latest_forecast(["000001.SZ"], as_of_date=date(2026, 1, 1))
    assert result3.iloc[0]["report_period"] == date(2025, 9, 30)


@pytest.mark.asyncio
async def test_repo_05_active_codes_as_of_pit(repo: MarketDataRepository) -> None:
    """REPO-05: get_active_stock_codes_as_of PIT 过滤 — RM-18 修复。

    场景：stock_info 含 3 只股票：
      - A：2018 上市，未退市（应在所有 PIT 查询里）
      - B：2022 上市，未退市（2021-05-13 查不到，2023-06-01 能查到）
      - C：2015 上市，2020-12-31 退市（2019 能查到，2021 查不到）

    断言：
      - PIT(2021-05-13) → {A}
      - PIT(2019-06-01) → {A, C}
      - PIT(2023-06-01) → {A, B}
    """
    df = pd.DataFrame(
        {
            "ts_code": ["A.SZ", "B.SZ", "C.SZ"],
            "name": ["A", "B", "C"],
            "market": ["MAIN", "MAIN", "MAIN"],
            "sw_industry_l1": [None, None, None],
            "sw_industry_l2": [None, None, None],
            "list_date": [date(2018, 1, 1), date(2022, 1, 1), date(2015, 1, 1)],
            "delist_date": [None, None, date(2020, 12, 31)],
            "is_active": [True, True, False],
        }
    )
    await repo.upsert_stock_list(df)

    codes_2021 = set(await repo.get_active_stock_codes_as_of(date(2021, 5, 13)))
    assert codes_2021 == {"A.SZ"}, f"2021-05-13 应只 A（B 未上市/C 已退市），得 {codes_2021}"

    codes_2019 = set(await repo.get_active_stock_codes_as_of(date(2019, 6, 1)))
    assert codes_2019 == {"A.SZ", "C.SZ"}, f"2019-06-01 应 A+C，得 {codes_2019}"

    codes_2023 = set(await repo.get_active_stock_codes_as_of(date(2023, 6, 1)))
    assert codes_2023 == {"A.SZ", "B.SZ"}, f"2023-06-01 应 A+B，得 {codes_2023}"


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
