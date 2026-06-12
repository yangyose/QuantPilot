"""INT：BacktestService._load_data_bundle financials 按回测窗口切界（内存优化 2026-06-12）。

原实现 `select(FinancialData)` 全表（生产 631 万行）→ 2GB 机长区间回测内存爆。改为按
[start-130d, end] 切 publish_date。financial_data 日级粒度下，PIT 在 trade_date 取
publish_date<=trade_date 的最近一行，必落在 [start-130d, start] 内，故下界安全；窗口外
行（start-130d 之前 / end 之后）永不被 PIT 引用，应被排除。

本测试验证切界正确性 + pe_pb_history 从同一窗口派生（不再二次全量 materialize）。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.engine.backtest.engine import BacktestConfig
from quantpilot.models.market import DailyQuote, FinancialData, StockInfo
from quantpilot.services.backtest_service import BacktestService

_TS = ["000001.SZ", "000002.SZ"]

# 回测窗口 [2024-06-03, 2024-06-04] → lookback_start = 2024-01-25
_IN_WINDOW = date(2024, 3, 15)      # 在 [2024-01-25, 2024-06-04] 内 → 保留
_BEFORE_WINDOW = date(2023, 6, 1)   # < lookback_start → 排除
_AFTER_WINDOW = date(2024, 12, 1)   # > end_date → 排除


async def _seed(session: AsyncSession) -> None:
    for c in _TS:
        session.add(StockInfo(
            ts_code=c, name=f"name_{c}", list_date=date(2015, 1, 1),
            delist_date=None, sw_industry_l1="银行", is_active=True,
        ))
    for d in (date(2024, 6, 3), date(2024, 6, 4)):
        for c in _TS:
            session.add(DailyQuote(
                ts_code=c, trade_date=d,
                open=Decimal("10.0"), high=Decimal("11.0"),
                low=Decimal("9.5"), close=Decimal("10.5"),
                vol=Decimal("100000"), amount=Decimal("1050000.0"),
                adj_factor=Decimal("1.0"),
                is_suspended=False, is_st=False, limit_up=False, limit_down=False,
                float_mkt_cap=Decimal("1.0e9"),
            ))
    # financials：每股 3 行（窗口内 / 窗口前 / 窗口后），pe_ttm 用 publish 年份区分
    for c in _TS:
        for pub, pe in (
            (_IN_WINDOW, "15.0"),
            (_BEFORE_WINDOW, "99.0"),
            (_AFTER_WINDOW, "1.0"),
        ):
            session.add(FinancialData(
                ts_code=c, report_period=date(pub.year - 1, 12, 31), publish_date=pub,
                pe_ttm=Decimal(pe), pb=Decimal("1.5"),
                net_profit_yoy=Decimal("0.10"), total_equity=Decimal("1.0e9"),
                debt_to_asset=Decimal("0.40"),
            ))
    await session.flush()


def _cfg() -> BacktestConfig:
    return BacktestConfig(
        start_date=date(2024, 6, 3), end_date=date(2024, 6, 4),
        initial_capital=1_000_000.0, strategy_config={}, account_config={},
    )


async def test_int_financials_bounded_to_window(db_session: AsyncSession) -> None:
    """financials 只含窗口内 publish_date；窗口前/后行被排除。"""
    await _seed(db_session)
    bundle = await BacktestService(session=db_session, engine=None)._load_data_bundle(_cfg())

    fin = bundle.financials
    assert not fin.empty
    pubs = set(fin["publish_date"].unique())
    assert pubs == {_IN_WINDOW}, f"应只含窗口内 publish_date，实际={pubs}"
    # 每股 1 行（仅窗口内）= 2 行
    assert len(fin) == len(_TS)
    # 窗口内值正确（pe_ttm=15.0），未被窗口前的 99.0 / 窗口后的 1.0 污染
    assert abs(float(fin["pe_ttm"].iloc[0]) - 15.0) < 1e-9


async def test_int_pe_pb_history_derived_in_window(db_session: AsyncSession) -> None:
    """pe_pb_history 从同一窗口派生，索引 (ts_code, publish_date) 仅含窗口内日期。"""
    await _seed(db_session)
    bundle = await BacktestService(session=db_session, engine=None)._load_data_bundle(_cfg())

    pe_pb = bundle.pe_pb_history
    assert not pe_pb.empty
    pubs = set(pe_pb.index.get_level_values("publish_date"))
    assert pubs == {_IN_WINDOW}
    assert set(pe_pb.columns) == {"pe_ttm", "pb"}
    assert len(pe_pb) == len(_TS)
