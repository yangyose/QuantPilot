"""UT: 每日管线 is_st 自愈（ingest_daily 在 _st_codes=None 时自建当日 ST 集合）。

生产 bug（2026-06-18 发现）：tushare 日线适配器默认 is_st=False，真实 ST 标记只在
ingest_history 经 namechange 注入；每日管线 ingest_daily(trade_date) 不传 _st_codes →
回填用尽后每天写入 is_st 全 False → universe ST 过滤失效 → *ST 仙股混入买入信号。

修复：ingest_daily 在 _st_codes is None（每日路径）时调 _build_current_st_codes 自愈。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd

from quantpilot.services.data_service import DataService


def _svc(namechange_side) -> DataService:
    adapter = SimpleNamespace()
    adapter.fetch_namechange = AsyncMock(side_effect=namechange_side)
    return DataService(
        adapter=adapter,
        validator=SimpleNamespace(),
        repo=SimpleNamespace(),
        calendar=SimpleNamespace(),
    )


def _namechange_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["ts_code", "name", "start_date", "end_date"])


async def test_build_current_st_codes_marks_active_st() -> None:
    """当前生效的 *ST 票（end_date=None）→ 进 ST 集合；普通票不进。"""
    df = _namechange_df([
        {"ts_code": "600421.SH", "name": "*ST华嵘",
         "start_date": date(2024, 5, 1), "end_date": None},
        {"ts_code": "000001.SZ", "name": "平安银行",
         "start_date": date(2020, 1, 1), "end_date": None},
    ])
    svc = _svc([df])
    codes = await svc._build_current_st_codes(date(2026, 6, 17))
    assert codes == {"600421.SH"}


async def test_build_current_st_codes_excludes_lifted_st() -> None:
    """曾 ST 但已摘帽（end_date < trade_date）→ 不在当日 ST 集合。"""
    df = _namechange_df([
        {"ts_code": "600000.SH", "name": "ST浦发",
         "start_date": date(2022, 1, 1), "end_date": date(2023, 6, 1)},
    ])
    svc = _svc([df])
    codes = await svc._build_current_st_codes(date(2026, 6, 17))
    assert codes == set()


async def test_build_current_st_codes_failure_returns_none() -> None:
    """namechange 拉取失败 → 返回 None（is_st 保持 False，降级且记日志）。"""
    svc = _svc(ConnectionError("tushare namechange 5xx"))
    codes = await svc._build_current_st_codes(date(2026, 6, 17))
    assert codes is None


async def test_build_current_st_codes_lookback_5y() -> None:
    """namechange 回溯起点 = trade_date - 5 年（覆盖早年被 ST 的票）。"""
    df = _namechange_df([])
    svc = _svc([df])
    await svc._build_current_st_codes(date(2026, 6, 17))
    call = svc._adapter.fetch_namechange.await_args
    start_arg = call.args[0]
    assert start_arg == date(2021, 6, 18)  # 2026-06-17 - 365*5 天
