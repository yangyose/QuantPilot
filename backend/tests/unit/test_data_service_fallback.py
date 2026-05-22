"""UT-P13-D-02: Phase 13 DataService Tushare→AKShare 降级单元测试。

依据 docs/design/phases/phase13_production_observability.md §3.6.2 + §6.1：
- UT-P13-D-02a: Tushare 抛异常 → 走 AKShare 成功 → DATA_SOURCE_FALLBACK("success") +1
- UT-P13-D-02b: Tushare 抛异常 + AKShare NotImplementedError → 抛出 + status="unavailable"
- UT-P13-D-02c: Tushare 抛异常 + AKShare 也抛异常 → 通知 notify_health_alert
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from quantpilot.core.metrics import DATA_SOURCE_FALLBACK
from quantpilot.services.data_service import DataService


def _fake_quote_df() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "trade_date": [date(2026, 5, 22)],
        "open": [10.0], "high": [10.5], "low": [9.9], "close": [10.3],
        "pre_close": [10.0], "pct_chg": [0.03], "vol": [1234500.0],
        "amount": [12_700_000.0], "turnover_rate": [0.03],
        "float_mkt_cap": [1.0e9], "adj_factor": [1.0],
        "is_suspended": [False], "is_st": [False],
        "limit_up": [False], "limit_down": [False],
    })


def _new_service(tushare_side, akshare_side, notifier=None) -> DataService:
    tushare = SimpleNamespace()
    tushare.fetch_daily_quotes = AsyncMock(side_effect=tushare_side)
    akshare = SimpleNamespace()
    akshare.fetch_daily_quotes = AsyncMock(side_effect=akshare_side)
    return DataService(
        adapter=tushare,
        validator=SimpleNamespace(),
        repo=SimpleNamespace(),
        calendar=SimpleNamespace(),
        fallback_adapter=akshare,
        notifier=notifier,
    )


def _counter_value(status: str) -> float:
    sample = DATA_SOURCE_FALLBACK.labels(
        from_source="tushare", to_source="akshare", status=status,
    )
    return sample._value.get()


async def test_ut_p13_d_02a_fallback_success_increments_counter() -> None:
    """Tushare 抛异常 → AKShare 成功 → counter("success") +1"""
    before_trying = _counter_value("trying")
    before_success = _counter_value("success")

    svc = _new_service(
        tushare_side=ConnectionError("tushare 5xx"),
        akshare_side=[_fake_quote_df()],
    )
    df = await svc._fetch_daily_quotes_with_fallback(date(2026, 5, 22))
    assert not df.empty
    assert df.iloc[0]["ts_code"] == "000001.SZ"
    assert _counter_value("trying") == pytest.approx(before_trying + 1)
    assert _counter_value("success") == pytest.approx(before_success + 1)


async def test_ut_p13_d_02b_fallback_not_implemented_marks_unavailable() -> None:
    """AKShare NotImplementedError → status="unavailable" + 抛出"""
    before = _counter_value("unavailable")
    svc = _new_service(
        tushare_side=ConnectionError("tushare 5xx"),
        akshare_side=NotImplementedError("ak 未实现"),
    )
    with pytest.raises(NotImplementedError):
        await svc._fetch_daily_quotes_with_fallback(date(2026, 5, 22))
    assert _counter_value("unavailable") == pytest.approx(before + 1)


async def test_ut_p13_d_02c_double_failure_triggers_notify() -> None:
    """Tushare + AKShare 均抛异常 → status="failed" + 调 notify_health_alert"""
    before_failed = _counter_value("failed")
    notifier = AsyncMock()
    notifier.notify_health_alert = AsyncMock()
    svc = _new_service(
        tushare_side=ConnectionError("tushare 5xx"),
        akshare_side=RuntimeError("akshare 网络抖动"),
        notifier=notifier,
    )
    with pytest.raises(RuntimeError):
        await svc._fetch_daily_quotes_with_fallback(date(2026, 5, 22))
    assert _counter_value("failed") == pytest.approx(before_failed + 1)
    notifier.notify_health_alert.assert_awaited_once()
    args, kwargs = notifier.notify_health_alert.await_args
    assert args[0] == "data_source_unavailable"


async def test_ut_p13_d_02d_empty_tushare_triggers_fallback() -> None:
    """Tushare 返回空 DataFrame → 视为失败 → 走 AKShare"""
    before_success = _counter_value("success")
    svc = _new_service(
        tushare_side=[pd.DataFrame()],  # 空 DataFrame
        akshare_side=[_fake_quote_df()],
    )
    df = await svc._fetch_daily_quotes_with_fallback(date(2026, 5, 22))
    assert not df.empty
    assert _counter_value("success") == pytest.approx(before_success + 1)


async def test_ut_p13_d_02e_no_fallback_adapter_propagates() -> None:
    """fallback_adapter=None 时直接 raise 原异常（不降级）"""
    tushare = SimpleNamespace()
    tushare.fetch_daily_quotes = AsyncMock(side_effect=ConnectionError("boom"))
    svc = DataService(
        adapter=tushare,
        validator=SimpleNamespace(),
        repo=SimpleNamespace(),
        calendar=SimpleNamespace(),
        fallback_adapter=None,
        notifier=None,
    )
    with pytest.raises(ConnectionError):
        await svc._fetch_daily_quotes_with_fallback(date(2026, 5, 22))
