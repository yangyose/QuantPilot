"""NotificationChannel ABC（Phase 10）。

SDD §13.1 通知渠道抽象；V1.0 仅 WxPusher + InApp，V1.5 可扩 ServerChan/Email/Slack。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class NotificationChannel(ABC):
    """通知渠道抽象基类。

    ⚠️ **契约就是这里声明的全部**：`NotificationService` 只允许访问本类声明的成员。
    2026-09-02 之前它还直接读 `WxPusherAdapter` 私有的 `configured` / `uid`，
    使「只实现 `send()` 的新渠道」一接入就 AttributeError——而 ABC 的存在
    让人以为可以直接扩展。这条边界当时靠「唯一实现恰好是 WxPusherAdapter」
    掩盖着，ABC 从未真正生效。由 `TestDependsOnlyOnABC` 的 AST 用例钉死。
    """

    @property
    @abstractmethod
    def configured(self) -> bool:
        """渠道是否已配置可用。

        **未配置不是运行期异常**：`NotificationService` 会跳过发送、不记 ERROR、
        不写 `wx_error`（语义锚点：`wx_error IS NOT NULL` ⟺ 真的尝试过且失败了）。
        渠道自己应在实例化时 WARN 一次，而不是每条通知都报。
        """
        raise NotImplementedError

    @property
    def target(self) -> str:
        """收件目标的可读标识，**仅用于日志**。默认返回渠道类名。

        故意不叫 `uid`：不同渠道的收件标识形态不同（WxPusher 是 UID_x，
        企业微信是 touser + agentid），日志只需要一个能区分收件人的字符串。
        """
        return type(self).__name__

    @abstractmethod
    async def send(self, title: str, body: str) -> bool:
        """发送一条通知。

        Returns:
            True 表示渠道发送成功；False 表示失败（已重试），上层应降级到 InApp。
        """
        raise NotImplementedError
