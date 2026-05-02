"""WxPusherAdapter：WxPusher HTTP 推送（Phase 10）。

SDD §13.1：3 次重试、间隔 30 秒；HTTP 200 + `code == 1000` 视为成功。
v1.1 评审 Q-4 日志级别约定：
- 单次请求失败（网络/HTTP 非 200/code != 1000）→ WARN（附 attempt 编号）
- 实例化时未配置环境变量 → WARN（仅一次，避免每次推送刷屏）
- 3 次重试全部失败 → 由 NotificationService 上层记 ERROR
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from quantpilot.notification.base import NotificationChannel

logger = logging.getLogger(__name__)

WXPUSHER_API_URL = "https://wxpusher.zjiecode.com/api/send/message"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_RETRY_INTERVAL_SECONDS = 30.0
MAX_ATTEMPTS = 3


class WxPusherAdapter(NotificationChannel):
    """WxPusher HTTP 适配器。

    构造时若 `app_token` 或 `uid` 为空，记一次 WARN 并把实例标记为未配置；
    所有 `send()` 调用直接返回 False（降级为 InApp）。
    """

    def __init__(
        self,
        app_token: str,
        uid: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retry_interval: float = DEFAULT_RETRY_INTERVAL_SECONDS,
    ) -> None:
        self._app_token = app_token
        self._uid = uid
        self._timeout = timeout
        self._retry_interval = retry_interval
        self._configured = bool(app_token) and bool(uid)
        if not self._configured:
            logger.warning(
                "wxpusher_not_configured: WXPUSHER_APP_TOKEN/WXPUSHER_UID 缺失，"
                "通知将仅走系统内"
            )

    @property
    def uid(self) -> str:
        """暴露 uid 供 NotificationService 写入降级日志。"""
        return self._uid

    @property
    def configured(self) -> bool:
        return self._configured

    async def send(self, title: str, body: str) -> bool:
        """发送一条 WxPusher 消息，失败重试 3 次（间隔 30s）。"""
        if not self._configured:
            return False

        payload = {
            "appToken": self._app_token,
            "content": body,
            "summary": title[:20],
            "contentType": 1,
            "uids": [self._uid],
        }

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(WXPUSHER_API_URL, json=payload)
                if resp.status_code == 200:
                    body_json = resp.json()
                    if body_json.get("code") == 1000:
                        return True
                    logger.warning(
                        "wxpusher_attempt_failed: attempt=%d/%d code=%s msg=%s",
                        attempt, MAX_ATTEMPTS, body_json.get("code"), body_json.get("msg"),
                    )
                else:
                    logger.warning(
                        "wxpusher_attempt_failed: attempt=%d/%d http_status=%d",
                        attempt, MAX_ATTEMPTS, resp.status_code,
                    )
            except Exception as exc:
                logger.warning(
                    "wxpusher_attempt_failed: attempt=%d/%d err=%s",
                    attempt, MAX_ATTEMPTS, exc,
                )

            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(self._retry_interval)

        return False
