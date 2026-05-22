"""R13-P1-3 回归：DailyPipeline 失败告警必须走 HEALTH_ALERT 而非 PIPELINE_FAILURE。

Phase 13 §3.4.1 "运维告警统一入口" 要求 pipeline 失败、数据源不可用、因子衰减
持续等运维事件共用 notify_type=HEALTH_ALERT，便于运维仪表盘按 alert_type
下钻聚合。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from quantpilot.pipeline.daily_pipeline import DailyPipeline


async def test_ut_r13_p1_3_pipeline_failure_uses_health_alert(monkeypatch) -> None:
    """_notify_pipeline_failure 必须调 notify_health_alert("pipeline_failed", ...)
    而不是旧的 notify("PIPELINE_FAILURE", ...)。"""
    captured = {}

    fake_session = AsyncMock()
    fake_session.commit = AsyncMock()

    class _FakeSessionFactory:
        def __call__(self):
            return _FakeAsyncCtx()

    class _FakeAsyncCtx:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *args):
            return False

    pipeline = DailyPipeline(
        session_factory=_FakeSessionFactory(),
        adapter=SimpleNamespace(),
        validator=SimpleNamespace(),
        calendar=SimpleNamespace(),
        redis=None,
        notification_channel=None,
    )

    # Patch ConfigService + NotificationService 构造 → 拦截 notify_health_alert
    async def _fake_notify_health_alert(alert_type, body, payload=None):
        captured["alert_type"] = alert_type
        captured["body"] = body
        captured["payload"] = payload
        return MagicMock()

    fake_notifier = MagicMock()
    fake_notifier.notify_health_alert = _fake_notify_health_alert

    monkeypatch.setattr(
        "quantpilot.services.notification_service.NotificationService",
        lambda *a, **kw: fake_notifier,
    )
    monkeypatch.setattr(
        "quantpilot.services.config_service.ConfigService",
        lambda *a, **kw: MagicMock(),
    )

    exc = ValueError("boom")
    await pipeline._notify_pipeline_failure(
        run_id=42, trade_date=date(2026, 5, 22), exc=exc, config_snapshot=None,
    )

    assert captured.get("alert_type") == "pipeline_failed", (
        f"应调 notify_health_alert('pipeline_failed', ...)，实际 {captured!r}"
    )
    assert captured["payload"]["run_id"] == 42
    assert captured["payload"]["trade_date"] == "2026-05-22"
    assert captured["payload"]["error"] == "ValueError"
