"""Phase 13 §3.4.2 DataQualityRepository（无状态、按方法接收 session）。

参考 AttributionRepository / FactorICRepository 同款无状态模式，方便 DataService
内自管理 per-day session 调用。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.business import DataQualityMetric


class DataQualityRepository:
    """数据质量指标 upsert / 范围查询 / 近 N 日聚合。"""

    @staticmethod
    async def upsert_metric(
        session: AsyncSession,
        metric_date: date,
        data_type: str,
        metric_key: str,
        metric_value: float,
        details: dict | None = None,
    ) -> None:
        """按 (metric_date, data_type, metric_key) 幂等 upsert（同日重跑 ingest）。"""
        stmt = pg_insert(DataQualityMetric).values(
            metric_date=metric_date,
            data_type=data_type,
            metric_key=metric_key,
            metric_value=metric_value,
            details=details,
        ).on_conflict_do_update(
            constraint="uq_data_quality_date_type_key",
            set_={"metric_value": metric_value, "details": details},
        )
        await session.execute(stmt)

    @staticmethod
    async def get_metrics_by_range(
        session: AsyncSession,
        start: date,
        end: date,
        data_type: str | None = None,
    ) -> list[DataQualityMetric]:
        """返回 [start, end] 范围内的指标行（可选按 data_type 过滤）。"""
        stmt = select(DataQualityMetric).where(
            and_(
                DataQualityMetric.metric_date >= start,
                DataQualityMetric.metric_date <= end,
            )
        )
        if data_type is not None:
            stmt = stmt.where(DataQualityMetric.data_type == data_type)
        stmt = stmt.order_by(DataQualityMetric.metric_date.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_recent_violations(
        session: AsyncSession,
        days: int = 30,
    ) -> dict[str, dict[str, float]]:
        """返回近 N 日各 (data_type, metric_key) 累积 metric_value 聚合。

        结构：`{data_type: {metric_key: cumulative_value}}` 供 /health/data 端点直消费。
        """
        today = date.today()
        start = today - timedelta(days=days)
        stmt = (
            select(
                DataQualityMetric.data_type,
                DataQualityMetric.metric_key,
                func.sum(DataQualityMetric.metric_value).label("total"),
            )
            .where(
                DataQualityMetric.metric_date >= start,
                DataQualityMetric.metric_date <= today,
            )
            .group_by(DataQualityMetric.data_type, DataQualityMetric.metric_key)
        )
        result = await session.execute(stmt)
        agg: dict[str, dict[str, float]] = {}
        for data_type, metric_key, total in result.all():
            agg.setdefault(data_type, {})[metric_key] = float(total)
        return agg


_ = Any  # silence unused import in type hints
__all__ = ["DataQualityRepository"]
