"""Pydantic schemas：系统内通知 /notifications（Phase 10）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class NotificationItem(BaseModel):
    """GET /notifications 列表条目。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    notify_type: str
    title: str
    body: str
    payload: dict[str, Any] | None
    wx_pushed: bool
    wx_error: str | None
    read_at: datetime | None
    created_at: datetime


class NotificationListData(BaseModel):
    """GET /notifications data 字段：分页条目 + 总数 + 未读数（前端 Badge 共用）。"""

    items: list[NotificationItem]
    total: int
    unread: int


class UnreadCountData(BaseModel):
    """GET /notifications/unread-count data 字段。"""

    unread: int


class MarkReadData(BaseModel):
    """POST /notifications/{id}/read data 字段。"""

    id: int
    read_at: datetime


class MarkAllReadData(BaseModel):
    """POST /notifications/read-all data 字段。"""

    updated: int


class WxStatusData(BaseModel):
    """GET /notifications/wx-status data 字段（Phase 10 §6.4）。"""

    wx_configured: bool
    uid_masked: str | None = None
