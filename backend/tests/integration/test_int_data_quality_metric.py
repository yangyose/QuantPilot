"""INT-P13-A-01: DataValidator 错误自动写入 data_quality_metric 表。

依据 docs/design/phases/phase13_production_observability.md §6.2。
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.data_quality_repository import DataQualityRepository
from quantpilot.models.business import DataQualityMetric


async def test_int_p13_a_01_upsert_metric_and_query_roundtrip(
    db_session: AsyncSession,
) -> None:
    """INT-P13-A-01: DataQualityRepository upsert + query 端到端（真 DB）。

    DataService.ingest_daily 的 _record_validation 走的同样代码路径：
    1. upsert_metric 写入
    2. upsert_metric 同 (date, type, key) 第二次 update value
    3. get_recent_violations 近 30 日聚合返回结构
    """
    metric_date = date.today() - timedelta(days=2)

    # 第一次写入
    await DataQualityRepository.upsert_metric(
        db_session,
        metric_date=metric_date,
        data_type="daily_quote",
        metric_key="errors_count",
        metric_value=3.0,
        details={"errors": ["completeness violation"], "invalid_count": 2},
    )
    await db_session.flush()

    # 第二次 update（值变化 + details 更新）
    await DataQualityRepository.upsert_metric(
        db_session,
        metric_date=metric_date,
        data_type="daily_quote",
        metric_key="errors_count",
        metric_value=5.0,
        details={"errors": ["completeness violation", "price invalid"], "invalid_count": 3},
    )
    await db_session.flush()

    # 校验只有 1 行（幂等）+ 值是最新的
    stmt = select(DataQualityMetric).where(
        DataQualityMetric.metric_date == metric_date,
        DataQualityMetric.data_type == "daily_quote",
        DataQualityMetric.metric_key == "errors_count",
    )
    result = await db_session.execute(stmt)
    rows = list(result.scalars().all())
    assert len(rows) == 1
    assert float(rows[0].metric_value) == 5.0
    assert rows[0].details["invalid_count"] == 3

    # 多 data_type / metric_key 写入后聚合
    await DataQualityRepository.upsert_metric(
        db_session, metric_date=metric_date, data_type="daily_quote",
        metric_key="invalid_rows_count", metric_value=3.0,
    )
    await DataQualityRepository.upsert_metric(
        db_session, metric_date=metric_date, data_type="financial_data",
        metric_key="errors_count", metric_value=1.0,
    )
    await db_session.flush()

    agg = await DataQualityRepository.get_recent_violations(db_session, days=30)
    assert agg["daily_quote"]["errors_count"] == 5.0
    assert agg["daily_quote"]["invalid_rows_count"] == 3.0
    assert agg["financial_data"]["errors_count"] == 1.0
