"""INT-P13-A-02: Phase 13 数据源降级集成测试。

依据 docs/design/phases/phase13_production_observability.md §6.3 + §7.2：
- 真 PostgreSQL：Tushare + AKShare 双失败 → notify_health_alert 持久化到
  in_app_notification 表（notify_type=HEALTH_ALERT，payload 含 alert_type）。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.business import InAppNotification
from quantpilot.services.config_service import ConfigService
from quantpilot.services.data_service import DataService
from quantpilot.services.notification_service import NotificationService


async def test_int_p13_a_02_double_failure_persists_health_alert(
    db_session: AsyncSession,
) -> None:
    """INT-P13-A-02: Tushare + AKShare 均失败 → in_app_notification 写入
    HEALTH_ALERT 记录（alert_type=data_source_unavailable）。"""
    cfg = ConfigService(db_session)
    notifier = NotificationService(db_session, cfg)

    tushare = SimpleNamespace()
    tushare.fetch_daily_quotes = AsyncMock(side_effect=ConnectionError("tushare 5xx"))
    akshare = SimpleNamespace()
    akshare.fetch_daily_quotes = AsyncMock(
        side_effect=RuntimeError("akshare 网络抖动")
    )

    svc = DataService(
        adapter=tushare,
        validator=SimpleNamespace(),
        repo=SimpleNamespace(),
        calendar=SimpleNamespace(),
        fallback_adapter=akshare,
        notifier=notifier,
    )

    with pytest.raises(RuntimeError):
        await svc._fetch_daily_quotes_with_fallback(date(2026, 5, 22))

    # 校验：HEALTH_ALERT 已落库
    await db_session.flush()
    result = await db_session.execute(
        select(InAppNotification)
        .where(InAppNotification.notify_type == "HEALTH_ALERT")
        .order_by(InAppNotification.id.desc())
    )
    notifs = result.scalars().all()
    assert len(notifs) >= 1, "未找到 HEALTH_ALERT 通知"
    latest = notifs[0]
    assert latest.payload["alert_type"] == "data_source_unavailable"
    assert "2026-05-22" in latest.payload.get("trade_date", "")
    assert "data_source_unavailable" in latest.title
