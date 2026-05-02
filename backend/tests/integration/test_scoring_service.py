"""INT-04~08: ScoringService 集成测试（需真实 PostgreSQL）"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.market_state import MarketStateEnum, MarketStateRecord
from quantpilot.engine.pool import CandidatePoolManager
from quantpilot.engine.scorer import Scorer
from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
from quantpilot.engine.strategies.momentum import MomentumStrategy
from quantpilot.engine.strategies.trend import TrendStrategy
from quantpilot.engine.strategies.value import ValueStrategy
from quantpilot.engine.universe import UniverseFilter
from quantpilot.services.strategy_service import ScoringService
from quantpilot.services.watchlist_service import WatchlistService

# ---------------------------------------------------------------------------
# 测试常量
# ---------------------------------------------------------------------------
_TRADE_DATE = date(2025, 12, 31)          # 周三，测试评分日
_INDEX_CODE = "000300.SH"

# 5 只测试股票
_STOCK_CODES = ["INT04A.SZ", "INT04B.SZ", "INT04C.SZ", "INT04D.SZ", "INT04E.SZ"]

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _make_calendar() -> TradingCalendar:
    """生成 2025 全年合成交易日历（跳过周末）。"""
    dates: list[date] = []
    d = date(2025, 1, 2)
    while d <= date(2026, 1, 2):
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return TradingCalendar(dates)


def _make_trade_days(n: int, end: date = _TRADE_DATE) -> list[date]:
    """从 end 往前取 n 个工作日（跳过周末）。"""
    days: list[date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _insert_stock_info(ts_codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ts_codes,
        "name": [f"测试股{i}" for i in range(len(ts_codes))],
        "market": ["MAIN"] * len(ts_codes),
        "sw_industry_l1": ["计算机"] * len(ts_codes),
        "sw_industry_l2": ["软件"] * len(ts_codes),
        "list_date": [date(2024, 1, 2)] * len(ts_codes),  # 上市超 60 交易日
        "delist_date": [None] * len(ts_codes),
        "is_active": [True] * len(ts_codes),
    })


def _insert_daily_quotes(ts_codes: list[str], n_days: int = 130) -> pd.DataFrame:
    """生成 n_days 天合成行情（线性上涨，各股票涨幅不同以产生有差异的评分）。"""
    trade_days = _make_trade_days(n_days)
    rows = []
    for i, ts_code in enumerate(ts_codes):
        base = 10.0 + i * 2  # 各股起始价不同
        for j, td in enumerate(trade_days):
            growth = 1.0 + (i * 0.001) * j  # 不同股票不同涨幅
            close = base * growth
            rows.append({
                "ts_code": ts_code,
                "trade_date": td,
                "open": close * 0.995,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "pre_close": close / (1.0 + (i * 0.001)),
                "pct_chg": i * 0.1,
                "vol": 10_000_000,
                "amount": close * 10_000_000,
                "turnover_rate": 0.02,
                "float_mkt_cap": close * 1_000_000_000,
                "adj_factor": 1.0,
                "is_suspended": False,
                "is_st": False,
                "limit_up": False,
                "limit_down": False,
            })
    return pd.DataFrame(rows)


def _insert_financial_data(ts_codes: list[str]) -> pd.DataFrame:
    """每只股票一条财务记录。"""
    rows = []
    for i, ts_code in enumerate(ts_codes):
        rows.append({
            "ts_code": ts_code,
            "report_period": date(2025, 9, 30),
            "publish_date": date(2025, 11, 1),
            "pe_ttm": 20.0 - i,          # 各股 PE 不同
            "pb": 2.0 - i * 0.1,
            "roe": 0.10 + i * 0.01,       # 各股 ROE 不同
            "net_profit_yoy": 0.10 + i * 0.02,
            "revenue_yoy": 0.08 + i * 0.01,
            "dividend_yield": 0.02,
            "total_equity": 10_000_000_000.0,
            "debt_to_asset": 0.30,
        })
    return pd.DataFrame(rows)


def _insert_index_history(n_days: int = 130) -> pd.DataFrame:
    """生成 n_days 天 000300.SH 合成数据（线性上涨触发 UPTREND）。"""
    trade_days = _make_trade_days(n_days)
    rows = []
    for i, td in enumerate(trade_days):
        close = 3000.0 + i * 15
        rows.append({
            "index_code": _INDEX_CODE,
            "trade_date": td,
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "vol": 100_000_000,
            "pct_chg": 0.5,
        })
    return pd.DataFrame(rows)


async def _setup_base_data(repo: MarketDataRepository) -> None:
    """插入评分所需的基础数据。"""
    await repo.upsert_stock_list(_insert_stock_info(_STOCK_CODES))
    await repo.upsert_daily_quotes(_insert_daily_quotes(_STOCK_CODES))
    await repo.upsert_financial_data(_insert_financial_data(_STOCK_CODES))
    await repo.upsert_index_history(_insert_index_history())
    # 插入市场状态记录
    await repo.upsert_market_state(MarketStateRecord(
        trade_date=_TRADE_DATE,
        market_state=MarketStateEnum.UPTREND,
        trend_strength=30.0,
        adx_value=30.0,
        ma20=4000.0,
        ma60=3800.0,
        state_changed=False,
        description="合成 UPTREND",
    ))


def _make_scoring_service(session: AsyncSession, pool_capacity: int = 3) -> ScoringService:
    repo = MarketDataRepository(session)
    return ScoringService(
        repo=repo,
        universe_filter=UniverseFilter(),
        strategies=[TrendStrategy(), MomentumStrategy(), MeanReversionStrategy(), ValueStrategy()],
        scorer=Scorer(),
        pool_manager=CandidatePoolManager(pool_capacity=pool_capacity),
        calendar=_make_calendar(),
    )


# ---------------------------------------------------------------------------
# INT-04: 全流程评分（run_daily_scoring() 写入 candidate_pool）
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_04_run_daily_scoring_writes_pool(db_session: AsyncSession) -> None:
    """INT-04: ScoringService.run_daily_scoring() 完成后 candidate_pool 有数据，code=0"""
    repo = MarketDataRepository(db_session)
    await _setup_base_data(repo)

    svc = _make_scoring_service(db_session, pool_capacity=3)
    composite_scores = await svc.run_daily_scoring(_TRADE_DATE)

    # composite_scores 非空（5 只股票均通过过滤）
    assert len(composite_scores) > 0

    # candidate_pool 写入了记录；无白名单，pool_capacity=3，精确断言入池数
    pool_codes = await repo.get_pool_codes(_TRADE_DATE)
    assert len(pool_codes) == 3


# ---------------------------------------------------------------------------
# INT-05: 持仓保护（holding_codes 强制入池，is_holding=True）
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_05_holding_protection(db_session: AsyncSession) -> None:
    """INT-05: 传入 holding_codes 后 candidate_pool 中 is_holding=True 且在池中"""
    repo = MarketDataRepository(db_session)
    await _setup_base_data(repo)

    # INT04E.SZ 分数最低（i=4，基准价最高但只是线性涨幅），但因持仓保护强制入池
    holding_code = "INT04E.SZ"
    svc = _make_scoring_service(db_session, pool_capacity=2)
    await svc.run_daily_scoring(_TRADE_DATE, holding_codes=frozenset({holding_code}))

    pool_records = await repo.get_pool(trade_date=_TRADE_DATE, in_pool_only=True)
    pool_map = {r.ts_code: r for r in pool_records}

    assert holding_code in pool_map, "持仓保护：holding_code 应在候选池中"
    assert pool_map[holding_code].is_holding is True, "is_holding 应为 True"


# ---------------------------------------------------------------------------
# INT-06: 黑名单过滤（黑名单股票不出现在候选池）
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_06_blacklist_filter(db_session: AsyncSession) -> None:
    """INT-06: 黑名单股票被 UniverseFilter 前移除，不出现在候选池中"""
    repo = MarketDataRepository(db_session)
    await _setup_base_data(repo)

    blacklisted = "INT04A.SZ"
    # 添加黑名单
    await repo.add_watchlist(ts_code=blacklisted, list_type="BLACKLIST", note="集成测试")

    svc = _make_scoring_service(db_session, pool_capacity=5)  # 足够大，不受排名限制
    await svc.run_daily_scoring(_TRADE_DATE)

    pool_codes = await repo.get_pool_codes(_TRADE_DATE)
    assert blacklisted not in pool_codes, "黑名单股票不应出现在候选池中"


# ---------------------------------------------------------------------------
# INT-07: WatchlistService CRUD（add → get → remove 完整流程）
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_07_watchlist_service_crud(db_session: AsyncSession) -> None:
    """INT-07: WatchlistService add → get → remove 完整流程"""
    repo = MarketDataRepository(db_session)
    wl_svc = WatchlistService(repo=repo)

    ts_code = "INT07X.SZ"

    # 初始为空
    items = await wl_svc.get_list()
    initial_codes = {item.ts_code for item in items}
    assert ts_code not in initial_codes

    # 添加 WHITELIST
    added = await wl_svc.add(ts_code=ts_code, list_type="WHITELIST", note="集成测试备注")
    assert added.ts_code == ts_code
    assert added.list_type == "WHITELIST"
    assert added.note == "集成测试备注"

    # 查询确认存在
    items_after_add = await wl_svc.get_list(list_type="WHITELIST")
    codes_after_add = {item.ts_code for item in items_after_add}
    assert ts_code in codes_after_add

    # 幂等重复添加
    added_again = await wl_svc.add(ts_code=ts_code, list_type="WHITELIST", note="重复添加")
    assert added_again.ts_code == ts_code

    # 删除
    await wl_svc.remove(ts_code=ts_code, list_type="WHITELIST")

    # 确认删除
    items_after_remove = await wl_svc.get_list(list_type="WHITELIST")
    codes_after_remove = {item.ts_code for item in items_after_remove}
    assert ts_code not in codes_after_remove

    # 幂等删除（不存在时静默成功）
    await wl_svc.remove(ts_code=ts_code, list_type="WHITELIST")  # 不应抛异常


# ---------------------------------------------------------------------------
# INT-08: 白名单入池（白名单股票出现在候选池）
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_int_08_whitelist_in_pool(db_session: AsyncSession) -> None:
    """INT-08: 白名单股票额外入池（即使评分未进前 N）"""
    repo = MarketDataRepository(db_session)
    await _setup_base_data(repo)

    # 添加 INT04E.SZ 为白名单（INT04E 涨幅最低可能在评分中落后）
    whitelist_code = "INT04E.SZ"
    await repo.add_watchlist(ts_code=whitelist_code, list_type="WHITELIST")

    svc = _make_scoring_service(db_session, pool_capacity=2)
    await svc.run_daily_scoring(_TRADE_DATE)

    pool_codes = await repo.get_pool_codes(_TRADE_DATE)
    assert whitelist_code in pool_codes, "白名单股票应在候选池中"
