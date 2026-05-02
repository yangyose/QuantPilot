"""NotificationChannel ABC（Phase 10）。

SDD §13.1 通知渠道抽象；V1.0 仅 WxPusher + InApp，V1.5 可扩 ServerChan/Email/Slack。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class NotificationChannel(ABC):
    """通知渠道抽象基类。

    每个具体渠道（WxPusher / 邮件 / Slack 等）实现 `send()` 即可。
    """

    @abstractmethod
    async def send(self, title: str, body: str) -> bool:
        """发送一条通知。

        Returns:
            True 表示渠道发送成功；False 表示失败（已重试），上层应降级到 InApp。
        """
        raise NotImplementedError
