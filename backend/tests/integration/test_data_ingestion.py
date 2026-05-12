"""ING-01~04: DataService 集成测试（Mock 适配器 + 真实 PostgreSQL）"""
from datetime import date
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.repository import MarketDataRepository
from quantpilot.data.validators import DataValidator
from quantpilot.services.data_service import DataService, IngestResult


def _make_adapter(
    trade_date: date,
    quote_rows: int = 2,
    financial_rows: int = 2,
) -> AsyncMock:
    """构造返回最小合法数据的 mock 适配器。"""
    ts_codes = [f"{i:06d}.SZ" for i in range(1, quote_rows + 1)]

    quote_df = pd.DataFrame(
        {
            "ts_code": ts_codes,
            "trade_date": [trade_date] * quote_rows,
            "open": [10.0] * quote_rows,
            "high": [11.0] * quote_rows,
            "low": [9.0] * quote_rows,
            "close": [10.5] * quote_rows,
            "pre_close": [10.0] * quote_rows,
            "pct_chg": [0.05] * quote_rows,
            "vol": [100_000] * quote_rows,
            "amount": [1_000_000.0] * quote_rows,
            "turnover_rate": [0.01] * quote_rows,
            "float_mkt_cap": [1e10] * quote_rows,
            "adj_factor": [1.0] * quote_rows,
            "is_suspended": [False] * quote_rows,
            "is_st": [False] * quote_rows,
            "limit_up": [False] * quote_rows,
            "limit_down": [False] * quote_rows,
        }
    )
    fin_ts = [f"{i:06d}.SZ" for i in range(1, financial_rows + 1)]
    fin_df = pd.DataFrame(
        {
            "ts_code": fin_ts,
            "report_period": [date(2025, 9, 30)] * financial_rows,
            "publish_date": [trade_date] * financial_rows,
            "pe_ttm": [12.0] * financial_rows,
            "pb": [1.0] * financial_rows,
            "roe": [0.12] * financial_rows,
            "net_profit_yoy": [0.1] * financial_rows,
            "revenue_yoy": [0.08] * financial_rows,
            "dividend_yield": [0.03] * financial_rows,
            "total_equity": [1e10] * financial_rows,
            "debt_to_asset": [0.5] * financial_rows,
        }
    )
    index_df = pd.DataFrame(
        {
            "index_code": ["000300.SH"],
            "trade_date": [trade_date],
            "open": [4000.0],
            "high": [4050.0],
            "low": [3980.0],
            "close": [4020.0],
            "vol": [1_000_000],
            "pct_chg": [0.005],
        }
    )

    adapter = AsyncMock()
    adapter.fetch_daily_quotes = AsyncMock(return_value=quote_df)
    adapter.fetch_financial_data = AsyncMock(return_value=fin_df)
    adapter.fetch_index_history = AsyncMock(return_value=index_df)
    adapter.fetch_index_components = AsyncMock(return_value=[])
    return adapter


@pytest.fixture
def repo(db_session):
    return MarketDataRepository(db_session)


@pytest.fixture
def validator():
    return DataValidator()


@pytest.fixture
def calendar():
    """最小交易日历：2026-01-02 ~ 2026-01-07（3 个交易日）"""
    return TradingCalendar([date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)])


@pytest.mark.asyncio
async def test_ing_01_ingest_daily_normal(repo, validator, calendar) -> None:
    """ING-01: ingest_daily() 正常流程 → 数据入库，返回 IngestResult"""
    # 先插入股票信息，使 prev_count 不为 0
    stock_df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "name": ["股票A", "股票B"],
            "market": ["MAIN", "MAIN"],
            "sw_industry_l1": [None, None],
            "sw_industry_l2": [None, None],
            "list_date": [date(2000, 1, 1), date(2000, 1, 1)],
            "delist_date": [None, None],
            "is_active": [True, True],
        }
    )
    await repo.upsert_stock_list(stock_df)

    trade_date = date(2026, 1, 2)
    adapter = _make_adapter(trade_date, quote_rows=2, financial_rows=2)
    service = DataService(adapter, validator, repo, calendar)

    result = await service.ingest_daily(trade_date)

    assert isinstance(result, IngestResult)
    assert result.trade_date == trade_date
    assert result.quote_count == 2
    assert result.financial_count == 2
    assert result.snapshot_version != ""
    assert len(result.snapshot_version) == 64  # SHA256 hex
    assert result.errors == []


@pytest.mark.asyncio
async def test_ing_02_ingest_daily_validation_fail(repo, validator, calendar) -> None:
    """ING-02: 完整性校验失败 → 不入库，errors 非空，snapshot_version 仍生成"""
    # 插入 100 个 active 股票，完整性阈值 = 100 × 0.95 = 95
    stocks = [
        {
            "ts_code": f"{i:06d}.SZ",
            "name": f"股票{i}",
            "market": "MAIN",
            "sw_industry_l1": None,
            "sw_industry_l2": None,
            "list_date": date(2000, 1, 1),
            "delist_date": None,
            "is_active": True,
        }
        for i in range(1, 101)
    ]
    await repo.upsert_stock_list(pd.DataFrame(stocks))

    trade_date = date(2026, 1, 2)
    # 只返回 10 条日线（< 100 × 0.95 = 95），触发完整性校验失败
    adapter = _make_adapter(trade_date, quote_rows=10, financial_rows=2)
    service = DataService(adapter, validator, repo, calendar)

    result = await service.ingest_daily(trade_date)

    assert result.errors  # 有错误
    assert result.quote_count == 0  # 未入库
    assert result.snapshot_version != ""  # 仍生成快照版本

    # 验证 DB 中确实无 daily_quote 数据（不仅依赖 IngestResult 对象断言）
    latest = await repo.get_latest_quote_date()
    assert latest is None


@pytest.mark.asyncio
async def test_ing_03_ingest_history_3_days(repo, validator, calendar) -> None:
    """ING-03: ingest_history() 3 日范围 → 3 日数据入库，get_latest_quote_date 正确"""
    # 插入 2 个 active 股票，避免完整性校验阻断
    stock_df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "name": ["股票A", "股票B"],
            "market": ["MAIN", "MAIN"],
            "sw_industry_l1": [None, None],
            "sw_industry_l2": [None, None],
            "list_date": [date(2000, 1, 1), date(2000, 1, 1)],
            "delist_date": [None, None],
            "is_active": [True, True],
        }
    )
    await repo.upsert_stock_list(stock_df)

    # calendar 有 3 个交易日：2026-01-02, 2026-01-05, 2026-01-06
    # 每个交易日返回 2 条日线（与 active 股票数量一致，通过完整性校验）
    def make_side_effect(td: date) -> pd.DataFrame:
        return _make_adapter(td, quote_rows=2).fetch_daily_quotes.return_value

    adapter = AsyncMock()
    adapter.fetch_daily_quotes = AsyncMock(
        side_effect=[
            make_side_effect(date(2026, 1, 2)),
            make_side_effect(date(2026, 1, 5)),
            make_side_effect(date(2026, 1, 6)),
        ]
    )
    adapter.fetch_financial_data = AsyncMock(
        return_value=pd.DataFrame(
            columns=[
                "ts_code", "report_period", "publish_date", "pe_ttm", "pb",
                "roe", "net_profit_yoy", "revenue_yoy", "dividend_yield",
                "total_equity", "debt_to_asset",
            ]
        )
    )
    index_df = pd.DataFrame(
        columns=["index_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg"]
    )
    adapter.fetch_index_history = AsyncMock(return_value=index_df)
    adapter.fetch_index_components = AsyncMock(return_value=[])
    adapter.fetch_index_components_range = AsyncMock(return_value={})

    service = DataService(adapter, validator, repo, calendar)
    # _repo=repo 走测试注入路径，共用 db_session fixture 的事务（含 rollback 隔离）
    summary = await service.ingest_history(
        date(2026, 1, 2), date(2026, 1, 6), _repo=repo
    )

    assert summary["success_count"] == 3
    assert summary["fail_count"] == 0
    latest = await repo.get_latest_quote_date()
    assert latest == date(2026, 1, 6)


@pytest.mark.asyncio
async def test_ing_04_ingest_history_st_pit_correct(repo, validator, calendar) -> None:
    """ING-04: ingest_history() 正确应用 namechange 缓存 — is_st 按 PIT 还原"""
    # 准备 2 个 active 股票
    stock_df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "name": ["股票A", "股票B"],
            "market": ["MAIN", "MAIN"],
            "sw_industry_l1": [None, None],
            "sw_industry_l2": [None, None],
            "list_date": [date(2000, 1, 1), date(2000, 1, 1)],
            "delist_date": [None, None],
            "is_active": [True, True],
        }
    )
    await repo.upsert_stock_list(stock_df)

    trade_date = date(2026, 1, 2)

    # 000001.SZ 在 2026-01-02 曾是 ST
    namechange_df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "name": ["*ST 某股"],
            "start_date": [date(2025, 1, 1)],
            "end_date": [date(2026, 6, 30)],
        }
    )

    adapter = AsyncMock()
    adapter.fetch_namechange = AsyncMock(return_value=namechange_df)
    adapter.fetch_daily_quotes = AsyncMock(
        return_value=pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": [trade_date, trade_date],
                "open": [10.0, 20.0],
                "high": [11.0, 21.0],
                "low": [9.0, 19.0],
                "close": [10.5, 20.5],
                "pre_close": [10.0, 20.0],
                "pct_chg": [0.05, 0.025],
                "vol": [100_000, 200_000],
                "amount": [1_000_000.0, 2_000_000.0],
                "turnover_rate": [0.01, 0.02],
                "float_mkt_cap": [1e10, 2e10],
                "adj_factor": [1.0, 1.0],
                "is_suspended": [False, False],
                "is_st": [False, False],  # adapter 默认 False
                "limit_up": [False, False],
                "limit_down": [False, False],
            }
        )
    )
    adapter.fetch_financial_data = AsyncMock(
        return_value=pd.DataFrame(
            columns=[
                "ts_code", "report_period", "publish_date", "pe_ttm", "pb",
                "roe", "net_profit_yoy", "revenue_yoy", "dividend_yield",
                "total_equity", "debt_to_asset",
            ]
        )
    )
    adapter.fetch_index_history = AsyncMock(
        return_value=pd.DataFrame(
            columns=["index_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg"]
        )
    )
    adapter.fetch_index_components = AsyncMock(return_value=[])
    adapter.fetch_index_components_range = AsyncMock(return_value={})

    service = DataService(adapter, validator, repo, calendar)
    summary = await service.ingest_history(trade_date, trade_date, _repo=repo)

    assert summary["success_count"] == 1
    assert summary["fail_count"] == 0

    # 直接查询 DB 验证 is_st 已被 namechange 缓存覆盖
    from sqlalchemy import text

    result = await repo._session.execute(
        text(
            "SELECT ts_code, is_st FROM daily_quote "
            "WHERE trade_date = :td ORDER BY ts_code"
        ),
        {"td": trade_date},
    )
    rows = {r.ts_code: r.is_st for r in result.all()}
    assert rows["000001.SZ"] is True, "000001.SZ 在该日期应为 ST"
    assert rows["000002.SZ"] is False, "000002.SZ 在该日期不应为 ST"


@pytest.mark.asyncio
async def test_ing_04b_namechange_lookback_5_years(repo, validator, calendar) -> None:
    """ING-04b（RM-16 回归）：ingest_history 必须用 5 年回溯调 fetch_namechange，
    否则早就叫 *ST 的股票（公告在窗口前）会全部缺失 → is_st 全 FALSE。"""
    from datetime import timedelta

    captured_args: list[tuple[date, date]] = []

    async def _capture_namechange(start_date: date, end_date: date) -> pd.DataFrame:
        captured_args.append((start_date, end_date))
        return pd.DataFrame(columns=["ts_code", "name", "start_date", "end_date"])

    adapter = AsyncMock()
    adapter.fetch_namechange = _capture_namechange
    adapter.fetch_daily_quotes = AsyncMock(
        return_value=pd.DataFrame(columns=["ts_code", "trade_date", "is_st"])
    )
    adapter.fetch_financial_data = AsyncMock(
        return_value=pd.DataFrame(
            columns=["ts_code", "report_period", "publish_date"]
        )
    )
    adapter.fetch_index_history = AsyncMock(return_value=pd.DataFrame())
    adapter.fetch_index_components = AsyncMock(return_value=[])
    adapter.fetch_index_components_range = AsyncMock(return_value={})

    service = DataService(adapter, validator, repo, calendar)
    ingest_start = date(2026, 1, 2)
    ingest_end = date(2026, 1, 6)
    await service.ingest_history(ingest_start, ingest_end, _repo=repo)

    assert len(captured_args) == 1
    actual_start, actual_end = captured_args[0]
    # 回溯起点应 ≈ ingest_start - 5 年
    expected_start = ingest_start - timedelta(days=365 * 5)
    assert actual_start == expected_start, (
        f"namechange 回溯起点应 = ingest_start - 5y = {expected_start}，"
        f"实际 {actual_start}（修复前 = {ingest_start} 即仅窗口内公告）"
    )
    assert actual_end == ingest_end


@pytest.mark.asyncio
async def test_ing_05_ingest_daily_index_components_written(repo, validator, calendar) -> None:
    """ING-05: fetch_index_components 返回非空列表时，成分股实际写入 DB"""
    stock_df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "name": ["股票A", "股票B"],
            "market": ["MAIN", "MAIN"],
            "sw_industry_l1": [None, None],
            "sw_industry_l2": [None, None],
            "list_date": [date(2000, 1, 1), date(2000, 1, 1)],
            "delist_date": [None, None],
            "is_active": [True, True],
        }
    )
    await repo.upsert_stock_list(stock_df)

    trade_date = date(2026, 1, 2)
    adapter = _make_adapter(trade_date, quote_rows=2, financial_rows=2)
    # 覆盖为非空成分股列表
    adapter.fetch_index_components = AsyncMock(return_value=["000001.SZ", "000002.SZ"])

    service = DataService(adapter, validator, repo, calendar)
    result = await service.ingest_daily(trade_date)

    assert result.errors == []
    # 验证 DB 中确实有成分股数据（任选一个 index）
    components = await repo.get_index_components("000300.SH", trade_date)
    assert len(components) == 2
    assert "000001.SZ" in components
    assert "000002.SZ" in components


@pytest.mark.asyncio
async def test_ing_06_pit_violation_invalid_rows_excluded(repo, validator, calendar) -> None:
    """ING-06: validate_financial_data PIT 违规行不入库，合规行正常入库（行级过滤）"""
    stock_df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "name": ["股票A", "股票B"],
            "market": ["MAIN", "MAIN"],
            "sw_industry_l1": [None, None],
            "sw_industry_l2": [None, None],
            "list_date": [date(2000, 1, 1), date(2000, 1, 1)],
            "delist_date": [None, None],
            "is_active": [True, True],
        }
    )
    await repo.upsert_stock_list(stock_df)

    trade_date = date(2026, 1, 2)
    adapter = _make_adapter(trade_date, quote_rows=2, financial_rows=0)
    # 财务数据：000001.SZ 合规，000002.SZ publish_date > trade_date（PIT 违规）
    fin_df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "report_period": [date(2025, 9, 30), date(2025, 9, 30)],
            "publish_date": [date(2026, 1, 2), date(2026, 1, 3)],  # 000002 违规
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
    adapter.fetch_financial_data = AsyncMock(return_value=fin_df)

    service = DataService(adapter, validator, repo, calendar)
    result = await service.ingest_daily(trade_date)

    # PIT 违规被记录到 errors
    assert any("PIT" in e for e in result.errors)

    # 用远期日期跳过 PIT 过滤，查询所有已入库的财务行
    fin_in_db = await repo.get_latest_financial(
        ["000001.SZ", "000002.SZ"], as_of_date=date(2030, 1, 1)
    )
    # 只有合规行（000001.SZ）入库，违规行（000002.SZ）被过滤掉
    assert len(fin_in_db) == 1
    # ts_code 是索引（RM-17 修复后 set_index）
    assert fin_in_db.index[0] == "000001.SZ"
