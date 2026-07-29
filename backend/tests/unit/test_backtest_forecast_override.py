"""V1.5-A A5b 回测路径（SDD-EXT-03）：回测引擎前瞻 ROE 覆盖的 PIT 装配。

生产 ScoringService 已在 A5b 接入 apply_forecast_roe_override；本组测试覆盖**回测路径**
对称接入所需的两个引擎 helper：
- `_get_financials_at` 须保留 ``report_period`` 列（groupby(level=0).last() 原会丢弃
  index level 1），否则覆盖判定「快报期 > 正式财报期」无从比较。
- `_get_forecast_at` 对预加载全量 forecast 做 PIT 内存切片（pre_announce_date<=trade_date，
  每股取报告期最新、同期 data_priority 高者），与生产 repository.get_latest_forecast 同语义。
两者链上 apply_forecast_roe_override → 真空期 roe 被覆盖。
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from quantpilot.engine.backtest.engine import BacktestEngine
from quantpilot.engine.forecast_override import apply_forecast_roe_override


def _engine() -> BacktestEngine:
    return BacktestEngine(
        strategies=[], market_state_engine=None, universe_filter=None, scorer=None,
        signal_engine=None, position_engine=None, price_provider=None, calendar=None,
    )


def _financials_multiindex() -> pd.DataFrame:
    """MultiIndex(ts_code, report_period)，含 publish_date/roe/total_equity。"""
    df = pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"],
        "report_period": [date(2025, 6, 30), date(2025, 9, 30)],
        "publish_date": [date(2025, 8, 28), date(2025, 10, 30)],
        "roe": [0.08, 0.10],
        "total_equity": [1.0e9, 1.0e9],
    })
    return df.set_index(["ts_code", "report_period"])


def _forecast_flat() -> pd.DataFrame:
    """扁平全量 forecast（_load_data_bundle 预加载形态）。"""
    return pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"],
        "report_period": [date(2025, 12, 31), date(2025, 12, 31)],
        "pre_announce_date": [date(2026, 1, 15), date(2026, 1, 31)],
        "est_net_profit": [1.8e8, 2.0e8],   # 预告(1) 1.8e8 / 快报(2) 2.0e8
        "data_priority": [1, 2],
    })


def test_a5b_bt_financials_at_preserves_report_period() -> None:
    """_get_financials_at 返回值须含 report_period 列（供覆盖判定）。"""
    eng = _engine()
    fin_t = eng._get_financials_at(_financials_multiindex(), date(2025, 11, 15))
    assert "report_period" in fin_t.columns
    # PIT：11/15 时最近已发布期 = 三季报（publish 10/30 <= 11/15）
    assert fin_t.at["000001.SZ", "report_period"] == date(2025, 9, 30)


def test_a5b_bt_forecast_at_picks_latest_priority() -> None:
    """_get_forecast_at PIT 切片：取报告期最新、同期 data_priority 高者（快报 2 > 预告 1）。"""
    eng = _engine()
    fc_t = eng._get_forecast_at(_forecast_flat(), date(2026, 2, 10))
    assert list(fc_t.index) == ["000001.SZ"]
    assert fc_t.at["000001.SZ", "est_net_profit"] == 2.0e8  # express 覆盖 forecast
    assert fc_t.at["000001.SZ", "report_period"] == date(2025, 12, 31)


def test_a5b_bt_forecast_at_pit_excludes_future_announce() -> None:
    """pre_announce_date > trade_date 的行不可见（PIT）。"""
    eng = _engine()
    # 1/20 时只有预告(pre_announce 1/15)可见，快报(1/31)不可见
    fc_t = eng._get_forecast_at(_forecast_flat(), date(2026, 1, 20))
    assert fc_t.at["000001.SZ", "est_net_profit"] == 1.8e8
    # 全部未来 → 空
    assert eng._get_forecast_at(_forecast_flat(), date(2026, 1, 1)).empty


def test_a5b_bt_empty_forecast_returns_empty() -> None:
    eng = _engine()
    assert eng._get_forecast_at(pd.DataFrame(), date(2026, 2, 10)).empty


def test_a5b_bt_chain_overrides_roe_in_vacuum() -> None:
    """链路：financials_at + forecast_at + apply → 真空期 roe 被前瞻值覆盖。"""
    eng = _engine()
    trade_date = date(2026, 2, 10)  # 快报已发、年报未发 = 真空期
    fin_t = eng._get_financials_at(_financials_multiindex(), trade_date)
    fc_t = eng._get_forecast_at(_forecast_flat(), trade_date)
    out = apply_forecast_roe_override(fin_t, fc_t)
    # roe = est_net_profit / total_equity = 2.0e8 / 1.0e9 = 0.20（覆盖原三季报 0.10）
    assert abs(out.at["000001.SZ", "roe"] - 0.20) < 1e-9
