"""UT-P13-B-01~02: Phase 13 DataQualityRepository 单元测试（无 DB，验逻辑）。

依据 docs/design/phases/phase13_production_observability.md §3.4.2 + §6.1。

实际 DB 行为由 INT-P13-A-01 覆盖；本文件用 AsyncMock 验证 SQL 构造正确。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

from quantpilot.data.data_quality_repository import DataQualityRepository


async def test_ut_p13_b_01_upsert_metric_calls_pg_insert_on_conflict() -> None:
    """UT-P13-B-01: upsert_metric 构造 on_conflict_do_update 语句调用 session.execute。"""
    session = AsyncMock()
    await DataQualityRepository.upsert_metric(
        session,
        metric_date=date(2026, 5, 21),
        data_type="daily_quote",
        metric_key="completeness_violation_count",
        metric_value=3.0,
        details={"errors": ["x"], "invalid_count": 3},
    )
    assert session.execute.await_count == 1
    stmt = session.execute.await_args.args[0]
    # pg_insert + on_conflict_do_update 后是 Insert 对象
    assert "data_quality_metric" in str(stmt).lower()


async def test_ut_p13_b_02_get_recent_violations_returns_nested_dict() -> None:
    """UT-P13-B-02: get_recent_violations 聚合返回 {data_type: {metric_key: value}}。"""
    session = AsyncMock()

    class _R:
        def all(self):
            return [
                ("daily_quote", "completeness_violation_count", 5.0),
                ("daily_quote", "price_invalid_count", 2.0),
                ("financial_data", "pit_violation_count", 1.0),
            ]
    session.execute = AsyncMock(return_value=_R())

    agg = await DataQualityRepository.get_recent_violations(session, days=30)
    assert agg == {
        "daily_quote": {
            "completeness_violation_count": 5.0,
            "price_invalid_count": 2.0,
        },
        "financial_data": {"pit_violation_count": 1.0},
    }
