"""UT-P13-F-01: Phase 13 NotificationService.notify_health_alert 单元测试。

依据 docs/design/phases/phase13_production_observability.md §3.4.1 + §6.1。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from quantpilot.core.config_defaults import NotificationConfig
from quantpilot.services.notification_service import NotificationService


async def test_ut_p13_f_01_notify_health_alert_uses_health_alert_type() -> None:
    """UT-P13-F-01: notify_health_alert 走 HEALTH_ALERT 类型 + payload 含 alert_type。"""
    # mock config_service.get_notification_config 返回启用的 prefs
    prefs = NotificationConfig(
        notify_risk_warn=True,
        notify_factor_alert=True,
        notify_signal_buy=True,
        notify_signal_sell=True,
        notify_market_state=True,
        notify_stop_loss_warn=True,
        wx_enabled=False,  # 不走 wxpusher，仅 in_app
    )
    cfg = MagicMock()
    cfg.get_notification_prefs = AsyncMock(return_value=prefs)

    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    # 抓住 InAppNotification 构造参数
    captured_notif = {}
    real_add = session.add

    def _spy_add(notif):
        captured_notif["instance"] = notif
        real_add(notif)
        # 模拟 ORM 默认值（id 不需要、wx_pushed 用 dataclass 默认）
    session.add = _spy_add

    svc = NotificationService(session=session, config_service=cfg, wxpusher=None)
    # mock _is_duplicate 返回 False（允许通知）
    svc._is_duplicate = AsyncMock(return_value=False)  # type: ignore[method-assign]

    result = await svc.notify_health_alert(
        "pipeline_failed",
        "DailyPipeline 跑挂：2026-05-21",
        payload={"trade_date": "2026-05-21", "run_id": 42},
    )

    assert result is not None
    notif = captured_notif["instance"]
    assert notif.notify_type == "HEALTH_ALERT"
    assert "pipeline_failed" in notif.title
    assert notif.payload["alert_type"] == "pipeline_failed"
    assert notif.payload["trade_date"] == "2026-05-21"
    assert notif.payload["run_id"] == 42
