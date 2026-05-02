"""通知渠道模块（Phase 10）。

包含 NotificationChannel ABC 与 WxPusherAdapter 实现。
对齐 system_design §5.10 与 SDD §13.1。
"""
from quantpilot.notification.base import NotificationChannel
from quantpilot.notification.wxpusher import WxPusherAdapter

__all__ = ["NotificationChannel", "WxPusherAdapter"]
