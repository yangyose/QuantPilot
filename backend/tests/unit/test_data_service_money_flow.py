"""MF-ING-01~04: DataService 的资金流向采集段（V1.5-C C4 / 设计 §6.3）。

设计要求：`ingest_daily` 新增独立段，**失败不阻断行情/财务主链路**（资金流是增强数据，
非信号必需）——所以它的异常**不进 `errors`**（`errors` 非空会让 ingest_history 整日 rollback，
把已入库的行情一起回滚）。可见性靠两条痕迹：`logger.exception` + `exception_occurred`
质量指标（data_type="money_flow"），以及 `IngestResult.money_flow_count`。

调用点用 AST 钉（§4.11「调用点是否真传参」）：只测 helper 本身，`ingest_daily` 忘了调它
照样全绿——那正是 C4 上线后「表建了、adapter 有了、每天 0 行」的形态。
"""
from __future__ import annotations

import ast
import logging
import pathlib
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd

from quantpilot.services.data_service import DataService, IngestResult

TD = date(2026, 9, 15)


def _svc(fetch_side, upsert_side=None) -> tuple[DataService, SimpleNamespace]:
    adapter = SimpleNamespace(fetch_money_flow=AsyncMock(side_effect=fetch_side))
    repo = SimpleNamespace(
        upsert_money_flow=AsyncMock(side_effect=upsert_side or (lambda df: len(df))),
        session=object(),
    )
    svc = DataService(
        adapter=adapter, validator=SimpleNamespace(), repo=repo, calendar=SimpleNamespace(),
    )
    return svc, repo


def _mf_df(n: int) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": [f"{i:06d}.SZ" for i in range(n)],
        "trade_date": [TD] * n,
        "net_mf_amount": [1.0] * n,
        "buy_elg_amount": [1.0] * n, "sell_elg_amount": [1.0] * n,
        "buy_lg_amount": [1.0] * n, "sell_lg_amount": [1.0] * n,
    })


async def test_mf_ing_01_success_upserts_and_records_zero_exception_metric() -> None:
    svc, repo = _svc([_mf_df(3)])
    errors: list[str] = []
    with patch.object(svc, "_record_exception_metric", new=AsyncMock()) as metric:
        n = await svc._ingest_money_flow(repo, TD, errors)
    assert n == 3
    assert errors == []
    repo.upsert_money_flow.assert_awaited_once()
    metric.assert_awaited_once_with(repo, TD, "money_flow", 0.0)


async def test_mf_ing_02_fetch_failure_does_not_enter_errors_but_leaves_traces(
    caplog,
) -> None:
    """失败：errors 不变（不阻断主链路）；logger.exception + 指标 1.0 两条痕迹必须都在。"""
    svc, repo = _svc(ConnectionError("tushare 5xx"))
    errors: list[str] = ["daily_quotes error: x"]   # 已有的错误不能被清掉
    with patch.object(svc, "_record_exception_metric", new=AsyncMock()) as metric, \
            caplog.at_level(logging.ERROR, logger="quantpilot.services.data_service"):
        n = await svc._ingest_money_flow(repo, TD, errors)
    assert n == 0
    assert errors == ["daily_quotes error: x"]
    repo.upsert_money_flow.assert_not_awaited()
    metric.assert_awaited_once_with(repo, TD, "money_flow", 1.0)
    assert any("money_flow_fetch_failed" in r.getMessage() for r in caplog.records)


async def test_mf_ing_03_empty_day_warns_not_silent(caplog) -> None:
    """交易日返回 0 行 = 可疑（全市场每天都有），必须 WARNING 而不是静默 0。"""
    svc, repo = _svc([pd.DataFrame(columns=_mf_df(0).columns)])
    with patch.object(svc, "_record_exception_metric", new=AsyncMock()), \
            caplog.at_level(logging.WARNING, logger="quantpilot.services.data_service"):
        n = await svc._ingest_money_flow(repo, TD, [])
    assert n == 0
    repo.upsert_money_flow.assert_not_awaited()
    assert any("money_flow_empty" in r.getMessage() for r in caplog.records)


def test_mf_ing_04_ingest_daily_calls_the_segment_and_result_carries_count() -> None:
    """调用点：`ingest_daily` 内必须真的调 `_ingest_money_flow`，且结果字段存在。"""
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "src/quantpilot/services/data_service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    ingest_daily = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "ingest_daily"
    )
    calls = [
        n for n in ast.walk(ingest_daily)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "_ingest_money_flow"
    ]
    assert len(calls) == 1, "ingest_daily 没有调用 _ingest_money_flow（或调了多次）"
    assert "money_flow_count" in IngestResult.__dataclass_fields__
