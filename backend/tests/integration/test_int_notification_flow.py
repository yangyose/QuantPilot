"""INT-NTF-01：通知入库链路集成测试（Phase 10 §10.3）。

覆盖：
- notify_signal/notify_market_state/notify_risk_warn/notify_factor_alert
  → InAppNotification 入库（即使 WxPusher 未配置，也始终落库 = SDD §13.1 兜底）
- 偏好关闭 → 不入库
- 同 payload 重复推送 → 去重
- 列表/未读计数/标记已读
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.business import InAppNotification, Signal
from quantpilot.services.config_service import ConfigService
from quantpilot.services.notification_service import NotificationService
from quantpilot.services.settings_service import SettingsService


# ---------------------------------------------------------------------------
# INT-NTF-01a: notify_market_state_change → InAppNotification 入库
# ---------------------------------------------------------------------------
async def test_int_ntf_01_market_state_change_persisted(db_session: AsyncSession) -> None:
    """市场状态变更 → 一条 InAppNotification 入库（wx_pushed=False，无 WxPusher）。"""
    cfg_svc = ConfigService(db_session)
    notifier = NotificationService(db_session, cfg_svc)

    notif = await notifier.notify_market_state_change(
        old_state="OSCILLATION",
        new_state="UPTREND",
        trade_date="2025-04-25",
    )

    assert notif is not None
    assert notif.notify_type == "MARKET_STATE"
    assert notif.payload == {"old": "OSCILLATION", "new": "UPTREND"}
    assert notif.wx_pushed is False
    assert "OSCILLATION" in notif.body and "UPTREND" in notif.body

    # 二次确认 DB 中存在
    rows = (
        await db_session.execute(select(InAppNotification).where(
            InAppNotification.notify_type == "MARKET_STATE"
        ))
    ).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# INT-NTF-01b: notify_signal（BUY）→ InAppNotification + payload 含 signal_id
# ---------------------------------------------------------------------------
async def test_int_ntf_01_signal_buy_persisted(db_session: AsyncSession) -> None:
    """BUY 信号 → SIGNAL_BUY notification（含 signal_id/ts_code 关联）。"""
    cfg_svc = ConfigService(db_session)
    notifier = NotificationService(db_session, cfg_svc)

    sig = Signal(
        ts_code="000001.SZ",
        signal_type="BUY",
        trade_date=date(2025, 4, 25),
        score=85.0,
        suggested_pct=0.10,
        suggested_price_low=10.0,
        suggested_price_high=10.5,
        stop_loss_price=9.2,
        signal_strength="STRONG",
        reason="趋势强烈",
        status="NEW",
    )
    db_session.add(sig)
    await db_session.flush()

    notif = await notifier.notify_signal(sig, name="平安银行", amount=10000.0)

    assert notif is not None
    assert notif.notify_type == "SIGNAL_BUY"
    assert notif.payload == {"signal_id": sig.id, "ts_code": "000001.SZ"}
    assert "平安银行" in notif.body


# ---------------------------------------------------------------------------
# INT-NTF-01c: 偏好关闭 → 不入库
# ---------------------------------------------------------------------------
async def test_int_ntf_01_preference_disabled_skips(db_session: AsyncSession) -> None:
    """notification_prefs.notify_risk_warn=False → notify_risk_warn 返回 None 且无入库。"""
    settings_svc = SettingsService(db_session)
    await settings_svc.upsert_setting(
        "notification_prefs",
        {"notify_risk_warn": False},
    )
    await db_session.flush()

    cfg_svc = ConfigService(db_session)
    notifier = NotificationService(db_session, cfg_svc)

    notif = await notifier.notify_risk_warn(
        event_type="CONCENTRATION_STOCK",
        message="行业占比超限",
        payload={"ts_code": "000001.SZ", "severity": "BLOCK"},
    )
    assert notif is None

    rows = (
        await db_session.execute(select(InAppNotification).where(
            InAppNotification.notify_type == "RISK_WARN"
        ))
    ).scalars().all()
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# INT-NTF-01d: 重复 payload → 去重（同 1 天内）
# ---------------------------------------------------------------------------
async def test_int_ntf_01_duplicate_payload_dedup(db_session: AsyncSession) -> None:
    """同 notify_type + 同 payload 在 1 天内只入库一次。"""
    cfg_svc = ConfigService(db_session)
    notifier = NotificationService(db_session, cfg_svc)

    n1 = await notifier.notify_factor_alert(
        alert_type="IC_DECAY",
        strategy="trend",
        factor="ma_cross",
        ic_mean=0.005,
    )
    n2 = await notifier.notify_factor_alert(
        alert_type="IC_DECAY",
        strategy="trend",
        factor="ma_cross",
        ic_mean=0.004,  # body 不同但 payload 相同
    )

    assert n1 is not None
    assert n2 is None  # 第二次去重

    rows = (
        await db_session.execute(select(InAppNotification).where(
            InAppNotification.notify_type == "FACTOR_ALERT"
        ))
    ).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# INT-NTF-01e: list_notifications + count_unread + mark_read 联动
# ---------------------------------------------------------------------------
async def test_int_ntf_01_list_unread_mark(db_session: AsyncSession) -> None:
    """生成 2 条 → list 返回 2 → unread=2 → mark 单条 → unread=1 → mark_all → unread=0。"""
    cfg_svc = ConfigService(db_session)
    notifier = NotificationService(db_session, cfg_svc)

    n1 = await notifier.notify_market_state_change("OSCILLATION", "UPTREND")
    n2 = await notifier.notify_risk_warn(
        event_type="DRAWDOWN",
        message="账户回撤超 20%",
        payload={"severity": "WARN"},
    )
    assert n1 is not None and n2 is not None

    items, total = await notifier.list_notifications(limit=10)
    assert total >= 2

    unread = await notifier.count_unread()
    assert unread >= 2

    marked = await notifier.mark_read(n1.id)
    assert marked is not None and marked.read_at is not None

    unread2 = await notifier.count_unread()
    assert unread2 == unread - 1

    affected = await notifier.mark_all_read()
    assert affected >= 1

    unread3 = await notifier.count_unread()
    assert unread3 == 0
