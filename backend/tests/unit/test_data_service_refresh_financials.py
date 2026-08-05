"""DataService.refresh_financials_full 签名契约 + 失败隔离单元测试。

背景（2026-08-05 管线验证挖出）：本方法曾逐股调 `fetch_financial_by_stock(ts_code)`
（str + 缺 start_date/end_date 两参），而适配器签名是 `(ts_codes: list[str], start, end)`
且内部自带 50 只/批 → 每股必抛 → total_equity 全市场恒 NULL → A5b 前瞻 ROE 覆盖与 F-4
universe 过滤长期失效（生产 2026-06-30 实证 success=0 fail=5515）。

这些测试锁死正确调用契约（list + 两个 date 参数），防签名再次漂移复发。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd

from quantpilot.services.data_service import DataService


def _fin_df(ts_code: str) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": [ts_code],
        "publish_date": [date(2026, 4, 28)],
        "report_period": [date(2026, 3, 31)],
        "roe": [0.12], "net_profit_yoy": [0.2],
        "revenue_yoy": [0.1], "debt_to_asset": [0.4],
    })


def _bal_df(ts_code: str) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": [ts_code],
        "publish_date": [date(2026, 4, 28)],
        "report_period": [date(2026, 3, 31)],
        "total_equity": [1.0e10],
    })


def _new_service(fin_side=None, bal_side=None, active_codes=None) -> DataService:
    adapter = SimpleNamespace()
    adapter.fetch_financial_by_stock = AsyncMock(
        side_effect=fin_side if fin_side is not None else (lambda *a, **k: _fin_df("X")),
    )
    adapter.fetch_balance_sheet = AsyncMock(
        side_effect=bal_side if bal_side is not None else (lambda *a, **k: _bal_df("X")),
    )
    repo = SimpleNamespace()
    repo.get_active_stock_codes = AsyncMock(
        return_value=active_codes if active_codes is not None else ["000001.SZ"],
    )
    repo.upsert_financial_data = AsyncMock(return_value=1)
    return DataService(
        adapter=adapter,
        validator=SimpleNamespace(),
        repo=repo,
        calendar=SimpleNamespace(),
    )


async def test_refresh_calls_adapter_with_list_and_dates() -> None:
    """签名契约：fetch_financial_by_stock / fetch_balance_sheet 均以
    (ts_codes: list, start_date: date, end_date: date) 被调用——不是逐股 str。"""
    svc = _new_service()
    await svc.refresh_financials_full(
        ts_codes=["000001.SZ", "600000.SH"],
        start_date=date(2024, 1, 1),
        end_date=date(2026, 8, 5),
    )
    for mock in (svc._adapter.fetch_financial_by_stock, svc._adapter.fetch_balance_sheet):
        args, kwargs = mock.await_args
        passed_codes, passed_start, passed_end = args[0], args[1], args[2]
        assert isinstance(passed_codes, list), "第 1 参必须是 list，历史 bug 传的是 str"
        assert passed_codes == ["000001.SZ", "600000.SH"]
        assert passed_start == date(2024, 1, 1)
        assert passed_end == date(2026, 8, 5)


async def test_refresh_upserts_both_fin_and_bal() -> None:
    """两处 upsert 都发生：fina_indicator（roe）+ balancesheet（total_equity）。"""
    svc = _new_service()
    await svc.refresh_financials_full(ts_codes=["000001.SZ"])
    # 1 批 × 2 次 upsert（fin + bal）
    assert svc._repo.upsert_financial_data.await_count == 2
    upserted_cols = [
        set(call.args[0].columns) for call in svc._repo.upsert_financial_data.await_args_list
    ]
    assert any("roe" in cols for cols in upserted_cols)
    assert any("total_equity" in cols for cols in upserted_cols)


async def test_refresh_defaults_ts_codes_and_dates() -> None:
    """ts_codes=None → 取活跃股；start/end=None → 默认 [今日-2y, 今日] 且 start<end。"""
    svc = _new_service(active_codes=["000001.SZ", "600000.SH"])
    await svc.refresh_financials_full()
    svc._repo.get_active_stock_codes.assert_awaited_once()
    args, _ = svc._adapter.fetch_balance_sheet.await_args
    passed_codes, passed_start, passed_end = args[0], args[1], args[2]
    assert passed_codes == ["000001.SZ", "600000.SH"]
    assert isinstance(passed_start, date) and isinstance(passed_end, date)
    assert passed_start < passed_end


async def test_refresh_batch_failure_isolated() -> None:
    """一批抛异常 → 计入 fail 但不阻断后续批（失败隔离）。"""
    # batch_size=1 → 3 只 = 3 批；第 2 批抛异常
    call_log: list[list[str]] = []

    def fin_side(codes, start, end):
        call_log.append(list(codes))
        if codes == ["B"]:
            raise RuntimeError("tushare 抖动")
        return _fin_df(codes[0])

    svc = _new_service(fin_side=fin_side)
    result = await svc.refresh_financials_full(
        ts_codes=["A", "B", "C"], batch_size=1,
    )
    assert result["success_count"] == 2
    assert result["fail_count"] == 1
    assert result["failed_codes"] == ["B"]
    # 第 3 批（C）在第 2 批失败后仍被调用 → 失败隔离生效
    assert ["C"] in call_log


async def test_refresh_skips_empty_upsert() -> None:
    """适配器返回空 DataFrame → 不调 upsert（避免空写）。"""
    svc = _new_service(
        fin_side=lambda *a, **k: pd.DataFrame(),
        bal_side=lambda *a, **k: pd.DataFrame(),
    )
    result = await svc.refresh_financials_full(ts_codes=["000001.SZ"])
    svc._repo.upsert_financial_data.assert_not_awaited()
    # 空返回不算失败（无异常）
    assert result["success_count"] == 1
    assert result["fail_count"] == 0
