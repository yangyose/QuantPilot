"""Phase 13 §4.1 + §4.2.2：调度器 + 数据延迟健康端点。

- `GET /api/v1/health/scheduler` JWT 鉴权 → SchedulerHealthService.snapshot()
- `GET /api/v1/health/data` JWT 鉴权 → 数据延迟 + 近 30 日 validator 错误聚合
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.api.deps import get_current_user_id, get_db

router = APIRouter()


@router.get("/scheduler")
async def health_scheduler(
    request: Request,
    _: int = Depends(get_current_user_id),
) -> dict:
    """调度器健康摘要。

    返回 `{running, jobs: [...], total_jobs}`；scheduler 未启动时
    running=False + 空 jobs 列表（非错误）。
    """
    svc = getattr(request.app.state, "scheduler_health", None)
    if svc is None:
        return {
            "code": 0,
            "data": {"running": False, "jobs": [], "total_jobs": 0},
            "msg": "ok",
        }
    return {"code": 0, "data": svc.snapshot(), "msg": "ok"}


@router.get("/data")
async def health_data(
    session: AsyncSession = Depends(get_db),
    _: int = Depends(get_current_user_id),
) -> dict:
    """数据延迟 + 近 30 日 DataValidator 错误聚合。

    `data_latency_days`：今日 - max(trade_date) （自然日；P2-5 推迟改交易日）
    `recent_violations`：近 30 日 (data_type, metric_key) 累积值
    `window_days`：30
    """
    from quantpilot.models.market import DailyQuote, FinancialData, IndexHistory

    today = date.today()

    # 数据延迟（按表分组）；FinancialData 用 publish_date 而非 trade_date。
    latency: dict[str, int] = {}
    latency_specs = (
        ("daily_quote", DailyQuote.trade_date),
        ("financial_data", FinancialData.publish_date),
        ("index_history", IndexHistory.trade_date),
    )
    # R13-P1-1：同步刷新 DATA_LATENCY Gauge，保证 Prometheus pull 端能拿到最新值
    from quantpilot.core.metrics import DATA_LATENCY
    for data_type, col in latency_specs:
        stmt = select(func.max(col))
        result = await session.execute(stmt)
        max_td = result.scalar_one_or_none()
        if max_td is not None:
            latency[data_type] = (today - max_td).days
            DATA_LATENCY.labels(data_type=data_type).set(latency[data_type])
        else:
            latency[data_type] = -1  # 无数据

    # 近 30 日 validator 错误（DataQualityMetric 表在 P13-B 创建；当前回退到 {}）
    recent_violations: dict[str, dict[str, float]] = {}
    try:
        from quantpilot.data.data_quality_repository import (  # type: ignore[import-not-found]
            DataQualityRepository,
        )
        repo = DataQualityRepository()
        recent_violations = await repo.get_recent_violations(session, days=30)
    except (ImportError, AttributeError):
        # P13-B 尚未实施时 graceful 降级，端点结构仍可用
        recent_violations = {}

    return {
        "code": 0,
        "data": {
            "data_latency_days": latency,
            "recent_violations": recent_violations,
            "window_days": 30,
        },
        "msg": "ok",
    }


__all__ = ["router"]
