"""INT：BacktestService._load_data_bundle 加载 financial_forecast（V1.5-A A5b 回测路径）。

A5b 生产路径已接入前瞻 ROE 覆盖；回测路径需 _load_data_bundle 把 financial_forecast 全量
预加载进 BacktestDataBundle.forecast（扁平 DataFrame），交由引擎 _get_forecast_at 做 PIT
内存切片。本测试验证：① 按 [start-400d, end] 切界 pre_announce_date；② 窗口外行排除；
③ 关键列齐全（供覆盖判定与 est_net_profit → roe 换算）。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.engine.backtest.engine import BacktestConfig
from quantpilot.models.market import FinancialForecast
from quantpilot.services.backtest_service import BacktestService

# 回测窗口 [2024-06-03, 2024-06-04] → fin_lookback_start = start-400d = 2023-04-30
_IN_WINDOW = date(2024, 3, 15)      # 在 [2023-04-30, 2024-06-04] 内 → 保留
_BEFORE_WINDOW = date(2023, 1, 1)   # < fin_lookback_start(2023-04-30) → 排除
_AFTER_WINDOW = date(2024, 12, 1)   # > end_date → 排除


async def _seed(session: AsyncSession) -> None:
    # 同股同报告期两条：预告(1) / 快报(2)，pre_announce 均在窗口内
    session.add(FinancialForecast(
        ts_code="000001.SZ", report_period=date(2024, 3, 31),
        pre_announce_date=_IN_WINDOW, est_net_profit=Decimal("1.8e8"),
        est_net_profit_yoy=Decimal("0.15"), data_priority=1, source_type="forecast",
    ))
    session.add(FinancialForecast(
        ts_code="000001.SZ", report_period=date(2024, 3, 31),
        pre_announce_date=_IN_WINDOW, est_net_profit=Decimal("2.0e8"),
        est_net_profit_yoy=Decimal("0.18"), data_priority=2, source_type="express",
    ))
    # 窗口前 / 窗口后各一条 → 应被切界排除
    session.add(FinancialForecast(
        ts_code="000002.SZ", report_period=date(2022, 12, 31),
        pre_announce_date=_BEFORE_WINDOW, est_net_profit=Decimal("5.0e7"),
        est_net_profit_yoy=None, data_priority=2, source_type="express",
    ))
    session.add(FinancialForecast(
        ts_code="000002.SZ", report_period=date(2024, 9, 30),
        pre_announce_date=_AFTER_WINDOW, est_net_profit=Decimal("6.0e7"),
        est_net_profit_yoy=None, data_priority=1, source_type="forecast",
    ))
    await session.flush()


def _cfg() -> BacktestConfig:
    return BacktestConfig(
        start_date=date(2024, 6, 3), end_date=date(2024, 6, 4),
        initial_capital=1_000_000.0, strategy_config={}, account_config={},
    )


async def test_int_forecast_bounded_to_window(db_session: AsyncSession) -> None:
    """bundle.forecast 只含 pre_announce_date 在窗口内的行；窗口前/后行排除。"""
    await _seed(db_session)
    bundle = await BacktestService(session=db_session, engine=None)._load_data_bundle(_cfg())

    fc = bundle.forecast
    assert not fc.empty
    # 仅 000001.SZ 的两条（窗口内）；000002.SZ 全部窗口外
    assert set(fc["pre_announce_date"].unique()) == {_IN_WINDOW}
    assert set(fc["ts_code"].unique()) == {"000001.SZ"}
    assert set(fc.columns) >= {
        "ts_code", "report_period", "pre_announce_date", "est_net_profit", "data_priority",
    }


async def test_int_forecast_at_picks_express_over_forecast(db_session: AsyncSession) -> None:
    """经引擎 _get_forecast_at PIT 切片：同期取快报(2) 覆盖预告(1)。"""
    from quantpilot.engine.backtest.engine import BacktestEngine

    await _seed(db_session)
    bundle = await BacktestService(session=db_session, engine=None)._load_data_bundle(_cfg())
    engine = BacktestEngine(
        strategies=[], market_state_engine=None, universe_filter=None, scorer=None,
        signal_engine=None, position_engine=None, price_provider=None, calendar=None,
    )

    fc_t = engine._get_forecast_at(bundle.forecast, date(2024, 6, 3))
    assert list(fc_t.index) == ["000001.SZ"]
    # est_net_profit 归一化为元（Numeric → float）；快报 2.0e8 覆盖预告 1.8e8
    assert abs(float(fc_t.at["000001.SZ", "est_net_profit"]) - 2.0e8) < 1.0
