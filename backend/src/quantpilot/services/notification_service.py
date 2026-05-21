"""NotificationService：推送通知（Phase 10 真实实现）。

Phase 7 时为 no-op stub。Phase 10 改造目标：
- 统一入口 `notify(notify_type, title, body, payload)`：
  1. 读 `NotificationConfig` 偏好，按类型/时段过滤
  2. **始终写入** `in_app_notification`（兜底渠道，SDD §13.1）
  3. WxPusher 启用且在推送时段内 → 调 `WxPusherAdapter.send`，结果回写 `wx_pushed/wx_error`
- 5 类便捷方法 + `_render_*` 模板（SDD §13.3）：
  notify_signal / notify_market_state_change / notify_stop_loss_warn /
  notify_risk_warn / notify_factor_alert
- 去重：同 `notify_type` + 同 `payload` 在最近 1 天内只发一次
- 日志级别（v1.1 评审 Q-4）：
  · WxPusher 3 次失败 → ERROR
  · in_app 写库失败 → ERROR + re-raise（best-effort 由上层 commit 决定）
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.core.config_defaults import NotificationConfig
from quantpilot.models.business import InAppNotification, Signal
from quantpilot.notification.wxpusher import WxPusherAdapter
from quantpilot.services.config_service import ConfigService

logger = logging.getLogger(__name__)

DEDUP_WINDOW = timedelta(days=1)

# 推送时段判断使用 A 股交易时区（生产容器默认 UTC，避免 8 小时偏移）
_PUSH_TZ = ZoneInfo("Asia/Shanghai")


# notify_type → 偏好开关字段名映射
_TYPE_PREF_MAP: dict[str, str] = {
    "SIGNAL_BUY": "notify_signal_buy",
    "SIGNAL_SELL": "notify_signal_sell",
    "MARKET_STATE": "notify_market_state",
    "STOP_LOSS_WARN": "notify_stop_loss_warn",
    "RISK_WARN": "notify_risk_warn",
    "FACTOR_ALERT": "notify_factor_alert",
}


class NotificationService:
    """推送通知服务。"""

    def __init__(
        self,
        session: AsyncSession,
        config_service: ConfigService,
        wxpusher: WxPusherAdapter | None = None,
    ) -> None:
        self._session = session
        self._cfg = config_service
        self._wx = wxpusher

    # ───────────────────── 统一入口 ─────────────────────
    async def notify(
        self,
        notify_type: str,
        title: str,
        body: str,
        payload: dict[str, Any] | None = None,
    ) -> InAppNotification | None:
        """统一通知入口。返回写入的 InAppNotification（被去重/被禁用时返回 None）。"""
        prefs = await self._cfg.get_notification_prefs()
        if not self._is_enabled(prefs, notify_type):
            return None

        if await self._is_duplicate(notify_type, payload):
            logger.info(
                "notification_dedup_skip type=%s payload=%s", notify_type, payload
            )
            return None

        notif = InAppNotification(
            notify_type=notify_type,
            title=title,
            body=body,
            payload=payload,
            wx_pushed=False,
        )
        self._session.add(notif)

        if prefs.wx_enabled and self._wx is not None and self._in_push_window(prefs):
            ok = await self._wx.send(title, body)
            notif.wx_pushed = ok
            if not ok:
                notif.wx_error = "WxPusher 重试 3 次均失败，已降级为系统内通知"
                logger.error(
                    "notification_degraded_to_in_app type=%s uid=%s title=%s",
                    notify_type, self._wx.uid, title,
                )

        try:
            await self._session.flush()
        except Exception:
            logger.exception(
                "in_app_notification_write_failed type=%s title=%s",
                notify_type, title,
            )
            raise

        # Phase 13 §3.1.2 埋点：wx_pushed=True → wxpusher 成功；False → in_app 兜底
        from quantpilot.core.metrics import NOTIFICATIONS_SENT
        if notif.wx_pushed:
            NOTIFICATIONS_SENT.labels(
                notify_type=notify_type, channel="wxpusher", status="success",
            ).inc()
        else:
            NOTIFICATIONS_SENT.labels(
                notify_type=notify_type, channel="in_app", status="success",
            ).inc()

        return notif

    # ───────────────────── 5 类便捷方法 ─────────────────────
    async def notify_signal(
        self,
        signal: Signal,
        *,
        name: str | None = None,
        amount: float | None = None,
    ) -> InAppNotification | None:
        if signal.signal_type == "BUY":
            title, body = self._render_signal_buy(signal, name=name, amount=amount)
            notify_type = "SIGNAL_BUY"
        else:
            title, body = self._render_signal_sell(signal, name=name)
            notify_type = "SIGNAL_SELL"
        return await self.notify(
            notify_type, title, body, {"signal_id": signal.id, "ts_code": signal.ts_code},
        )

    async def notify_market_state_change(
        self, old_state: str, new_state: str, trade_date: str | None = None,
    ) -> InAppNotification | None:
        title, body = self._render_market_state_change(old_state, new_state, trade_date)
        return await self.notify(
            "MARKET_STATE", title, body, {"old": old_state, "new": new_state},
        )

    async def notify_stop_loss_warn(
        self,
        ts_code: str,
        name: str | None,
        current_price: float,
        stop_loss_price: float,
        distance_pct: float,
    ) -> InAppNotification | None:
        title, body = self._render_stop_loss_warn(
            ts_code, name, current_price, stop_loss_price, distance_pct
        )
        return await self.notify(
            "STOP_LOSS_WARN", title, body, {"ts_code": ts_code},
        )

    async def notify_risk_warn(
        self, event_type: str, message: str, payload: dict[str, Any] | None = None,
    ) -> InAppNotification | None:
        title, body = self._render_risk_warn(event_type, message)
        return await self.notify("RISK_WARN", title, body, payload)

    async def notify_factor_alert(
        self, alert_type: str, strategy: str, factor: str, ic_mean: float | None = None,
    ) -> InAppNotification | None:
        title, body = self._render_factor_alert(alert_type, strategy, factor, ic_mean)
        return await self.notify(
            "FACTOR_ALERT", title, body,
            {"strategy": strategy, "factor": factor, "alert_type": alert_type},
        )

    # ───────────────────── 查询/标记（Phase 10 §5.4 REST） ─────────────────────
    async def list_notifications(
        self,
        *,
        notify_type: str | None = None,
        unread_only: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[InAppNotification], int]:
        """列出通知（按 created_at DESC 分页）。返回 (items, total)。"""
        base_filters = []
        if notify_type is not None:
            base_filters.append(InAppNotification.notify_type == notify_type)
        if unread_only:
            base_filters.append(InAppNotification.read_at.is_(None))

        count_stmt = select(func.count(InAppNotification.id))
        for f in base_filters:
            count_stmt = count_stmt.where(f)
        total = (await self._session.execute(count_stmt)).scalar_one()

        list_stmt = select(InAppNotification)
        for f in base_filters:
            list_stmt = list_stmt.where(f)
        list_stmt = (
            list_stmt.order_by(InAppNotification.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(list_stmt)).scalars().all()
        return list(rows), int(total)

    async def count_unread(self) -> int:
        """未读通知数量（read_at IS NULL）。"""
        stmt = select(func.count(InAppNotification.id)).where(
            InAppNotification.read_at.is_(None)
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def mark_read(self, notification_id: int) -> InAppNotification | None:
        """标记单条已读，已读则保持原 read_at 不变。不存在 → None。"""
        stmt = select(InAppNotification).where(InAppNotification.id == notification_id)
        notif = (await self._session.execute(stmt)).scalar_one_or_none()
        if notif is None:
            return None
        if notif.read_at is None:
            notif.read_at = datetime.now(timezone.utc)
            await self._session.flush()
        return notif

    async def mark_all_read(self) -> int:
        """批量标记全部未读为已读。返回影响行数。"""
        stmt = (
            update(InAppNotification)
            .where(InAppNotification.read_at.is_(None))
            .values(read_at=datetime.now(timezone.utc))
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return int(result.rowcount or 0)

    # ───────────────────── 内部：偏好 / 时段 / 去重 ─────────────────────
    @staticmethod
    def _is_enabled(prefs: NotificationConfig, notify_type: str) -> bool:
        field = _TYPE_PREF_MAP.get(notify_type)
        if field is None:
            return True  # 未登记类型默认放行（如 PIPELINE_FAILURE）
        return bool(getattr(prefs, field, True))

    @staticmethod
    def _in_push_window(prefs: NotificationConfig, now: datetime | None = None) -> bool:
        """当前小时是否在 [push_start_hour, push_end_hour) 内（按 Asia/Shanghai）。"""
        if now is None:
            current = datetime.now(tz=_PUSH_TZ)
        elif now.tzinfo is None:
            current = now
        else:
            current = now.astimezone(_PUSH_TZ)
        h = current.hour
        start, end = prefs.push_start_hour, prefs.push_end_hour
        if start <= end:
            return start <= h < end
        # 跨日时段（如 22:00 → 次日 06:00）
        return h >= start or h < end

    async def _is_duplicate(
        self, notify_type: str, payload: Mapping[str, Any] | None,
    ) -> bool:
        if payload is None:
            return False
        cutoff = datetime.now(timezone.utc) - DEDUP_WINDOW
        stmt = (
            select(InAppNotification.id)
            .where(
                InAppNotification.notify_type == notify_type,
                InAppNotification.payload == dict(payload),
                InAppNotification.created_at >= cutoff,
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # ───────────────────── 模板（SDD §13.3） ─────────────────────
    @staticmethod
    def _fmt(val: float | Decimal | None, default: str = "—") -> str:
        if val is None:
            return default
        return f"{float(val):.2f}"

    def _render_signal_buy(
        self,
        signal: Signal,
        *,
        name: str | None,
        amount: float | None,
    ) -> tuple[str, str]:
        display_name = name or signal.ts_code
        score = self._fmt(signal.score, "—")
        strength = signal.signal_strength or "MODERATE"
        price_low = self._fmt(signal.suggested_price_low)
        price_high = self._fmt(signal.suggested_price_high)
        suggested_pct = (
            f"{float(signal.suggested_pct) * 100:.1f}"
            if signal.suggested_pct is not None
            else "—"
        )
        amount_str = f"{amount:.0f}" if amount is not None else "—"
        stop_loss = self._fmt(signal.stop_loss_price)
        # 计算 stop_loss_pct：(price_low - stop_loss) / price_low
        stop_loss_pct = "—"
        if signal.stop_loss_price is not None and signal.suggested_price_low is not None:
            ref = float(signal.suggested_price_low)
            if ref > 0:
                stop_loss_pct = f"{(1 - float(signal.stop_loss_price) / ref) * 100:.1f}"

        title = f"【QuantPilot 买入信号】{display_name}"
        body = (
            f"【QuantPilot 买入信号】\n"
            f"标的：{display_name}({signal.ts_code})\n"
            f"评分：{score}/100（{strength}）\n"
            f"理由：{signal.reason or '—'}\n"
            f"建议：买入价区间 {price_low}-{price_high} 元\n"
            f"仓位：总资产的 {suggested_pct}%（约 {amount_str} 元）\n"
            f"止损：{stop_loss} 元（-{stop_loss_pct}%）\n"
            f"⚠️ 提醒：A股T+1，买入当日不可卖出"
        )
        return title, body

    def _render_signal_sell(
        self, signal: Signal, *, name: str | None,
    ) -> tuple[str, str]:
        display_name = name or signal.ts_code
        score = self._fmt(signal.score, "—")
        title = f"【QuantPilot 卖出信号】{display_name}"
        body = (
            f"【QuantPilot 卖出信号】\n"
            f"标的：{display_name}({signal.ts_code})\n"
            f"评分：{score}/100\n"
            f"理由：{signal.reason or '—'}\n"
            f"建议：择机卖出"
        )
        return title, body

    @staticmethod
    def _render_market_state_change(
        old_state: str, new_state: str, trade_date: str | None,
    ) -> tuple[str, str]:
        title = f"【QuantPilot 市场状态变更】{old_state} → {new_state}"
        date_line = f"日期：{trade_date}\n" if trade_date else ""
        body = (
            f"【QuantPilot 市场状态变更】\n"
            f"{date_line}"
            f"由 {old_state} 切换为 {new_state}。\n"
            f"策略权重将自动调整，请查看仪表盘。"
        )
        return title, body

    @staticmethod
    def _render_stop_loss_warn(
        ts_code: str,
        name: str | None,
        current_price: float,
        stop_loss_price: float,
        distance_pct: float,
    ) -> tuple[str, str]:
        display_name = name or ts_code
        title = f"【QuantPilot 止损预警】{display_name}"
        body = (
            f"【QuantPilot 止损预警】\n"
            f"标的：{display_name}({ts_code})\n"
            f"现价：{current_price:.2f} 元\n"
            f"止损价：{stop_loss_price:.2f} 元\n"
            f"距止损：{distance_pct * 100:.2f}%\n"
            f"⚠️ 接近止损线，请关注。"
        )
        return title, body

    @staticmethod
    def _render_risk_warn(event_type: str, message: str) -> tuple[str, str]:
        title = f"【QuantPilot 风险告警】{event_type}"
        body = (
            f"【QuantPilot 风险告警】\n"
            f"类型：{event_type}\n"
            f"详情：{message}"
        )
        return title, body

    @staticmethod
    def _render_factor_alert(
        alert_type: str, strategy: str, factor: str, ic_mean: float | None,
    ) -> tuple[str, str]:
        title = f"【QuantPilot 因子告警】{strategy}.{factor}"
        ic_line = f"近 3 月 IC 均值：{ic_mean:+.4f}\n" if ic_mean is not None else ""
        body = (
            f"【QuantPilot 因子告警】\n"
            f"策略：{strategy}\n"
            f"因子：{factor}\n"
            f"告警类型：{alert_type}\n"
            f"{ic_line}"
            f"建议复核策略权重或暂停该因子。"
        )
        return title, body
