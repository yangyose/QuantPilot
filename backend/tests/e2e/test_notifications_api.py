"""E2E 测试：系统内通知 /notifications（ASGI，Mock NotificationService）。

覆盖 Phase 10 §10.2 E2E-NTF-01~04 + API-74~78/API-84：
- 401 无鉴权
- 列表 / 筛选 / 分页
- 未读数
- 标记已读（存在 / 不存在 404）
- 全部标记已读
- wx-status（已配置 / 未配置）
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from quantpilot.api.deps import get_notification_service
from quantpilot.core.config import settings
from quantpilot.core.security import create_token
from quantpilot.main import app
from quantpilot.models.business import InAppNotification


def _auth() -> dict:
    return {"Authorization": f"Bearer {create_token('access', '1')}"}


def _mock_notif(
    nid: int = 1,
    notify_type: str = "SIGNAL_BUY",
    read_at: datetime | None = None,
    wx_pushed: bool = True,
) -> InAppNotification:
    n = MagicMock(spec=InAppNotification)
    n.id = nid
    n.notify_type = notify_type
    n.title = "【QuantPilot 买入信号】平安银行"
    n.body = "评分 85/100"
    n.payload = {"signal_id": nid, "ts_code": "000001.SZ"}
    n.wx_pushed = wx_pushed
    n.wx_error = None
    n.read_at = read_at
    n.created_at = datetime(2026, 4, 21, 15, 5, 0, tzinfo=timezone.utc)
    return n


# ───────────────────── GET /notifications ─────────────────────

async def test_napi_01_list_no_auth(client: AsyncClient) -> None:
    """GET /notifications 无鉴权 → 401。"""
    resp = await client.get("/api/v1/notifications")
    assert resp.status_code == 401


async def test_napi_02_list_ok(client: AsyncClient) -> None:
    """GET /notifications 有鉴权 → 200，返回 items + total + unread。"""
    mock = AsyncMock()
    mock.list_notifications = AsyncMock(
        return_value=([_mock_notif(1), _mock_notif(2, read_at=datetime(2026, 4, 21, 16))], 2)
    )
    mock.count_unread = AsyncMock(return_value=1)
    app.dependency_overrides[get_notification_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/notifications", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert "items" in data and isinstance(data["items"], list)
        assert data["total"] == 2
        assert data["unread"] == 1
        assert data["items"][0]["notify_type"] == "SIGNAL_BUY"
        assert data["items"][0]["payload"] == {"signal_id": 1, "ts_code": "000001.SZ"}
    finally:
        app.dependency_overrides.pop(get_notification_service, None)


async def test_napi_03_list_with_filters(client: AsyncClient) -> None:
    """GET /notifications?notify_type=SIGNAL_BUY&unread_only=true → 过滤参数传递到 service。"""
    mock = AsyncMock()
    mock.list_notifications = AsyncMock(return_value=([_mock_notif(1)], 1))
    mock.count_unread = AsyncMock(return_value=1)
    app.dependency_overrides[get_notification_service] = lambda: mock
    try:
        resp = await client.get(
            "/api/v1/notifications?notify_type=SIGNAL_BUY&unread_only=true&limit=5&offset=0",
            headers=_auth(),
        )
        assert resp.status_code == 200
        mock.list_notifications.assert_awaited_once_with(
            notify_type="SIGNAL_BUY",
            unread_only=True,
            limit=5,
            offset=0,
            account_id=1,  # G-4b：按当前账户隔离（conftest stub 返回 1）
        )
    finally:
        app.dependency_overrides.pop(get_notification_service, None)


async def test_napi_04_list_limit_exceeds_max_rejected(client: AsyncClient) -> None:
    """GET /notifications?limit=999 → 422（超过 200 上限）。"""
    resp = await client.get("/api/v1/notifications?limit=999", headers=_auth())
    assert resp.status_code == 422


# ───────────────────── GET /notifications/unread-count ─────────────────────

async def test_napi_05_unread_count_ok(client: AsyncClient) -> None:
    """GET /notifications/unread-count → 200 + {unread: N}。"""
    mock = AsyncMock()
    mock.count_unread = AsyncMock(return_value=7)
    app.dependency_overrides[get_notification_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/notifications/unread-count", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"] == {"unread": 7}
    finally:
        app.dependency_overrides.pop(get_notification_service, None)


async def test_napi_06_unread_count_no_auth(client: AsyncClient) -> None:
    """GET /notifications/unread-count 无鉴权 → 401。"""
    resp = await client.get("/api/v1/notifications/unread-count")
    assert resp.status_code == 401


# ───────────────────── POST /notifications/{id}/read ─────────────────────

async def test_napi_07_mark_read_ok(client: AsyncClient) -> None:
    """POST /notifications/{id}/read 合法 ID → 200 + {id, read_at}。"""
    now = datetime(2026, 4, 21, 16, 0, 0, tzinfo=timezone.utc)
    marked = _mock_notif(42, read_at=now)
    mock = AsyncMock()
    mock.mark_read = AsyncMock(return_value=marked)
    app.dependency_overrides[get_notification_service] = lambda: mock
    try:
        resp = await client.post("/api/v1/notifications/42/read", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["id"] == 42
        assert body["data"]["read_at"].startswith("2026-04-21T16:00")
        mock.mark_read.assert_awaited_once_with(42, account_id=1)
    finally:
        app.dependency_overrides.pop(get_notification_service, None)


async def test_napi_08_mark_read_not_found(client: AsyncClient) -> None:
    """POST /notifications/999999/read → 404。"""
    mock = AsyncMock()
    mock.mark_read = AsyncMock(return_value=None)
    app.dependency_overrides[get_notification_service] = lambda: mock
    try:
        resp = await client.post("/api/v1/notifications/999999/read", headers=_auth())
        assert resp.status_code == 404
        assert "999999" in resp.json()["msg"]
    finally:
        app.dependency_overrides.pop(get_notification_service, None)


# ───────────────────── POST /notifications/read-all ─────────────────────

async def test_napi_09_mark_all_read_ok(client: AsyncClient) -> None:
    """POST /notifications/read-all → 200 + {updated: N}。"""
    mock = AsyncMock()
    mock.mark_all_read = AsyncMock(return_value=12)
    app.dependency_overrides[get_notification_service] = lambda: mock
    try:
        resp = await client.post("/api/v1/notifications/read-all", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"] == {"updated": 12}
    finally:
        app.dependency_overrides.pop(get_notification_service, None)


# ───────────────────── GET /notifications/wx-status ─────────────────────

async def test_napi_10_wx_status_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /notifications/wx-status 已配置 → wx_configured=True + uid_masked。"""
    monkeypatch.setattr(settings, "wxpusher_app_token", "AT_fake123")
    monkeypatch.setattr(settings, "wxpusher_uid", "UID_abcdef")
    resp = await client.get("/api/v1/notifications/wx-status", headers=_auth())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["wx_configured"] is True
    assert data["uid_masked"] == "UID_***cdef"


async def test_napi_11_wx_status_unconfigured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /notifications/wx-status 未配置 → wx_configured=False + uid_masked=None。"""
    monkeypatch.setattr(settings, "wxpusher_app_token", "")
    monkeypatch.setattr(settings, "wxpusher_uid", "")
    resp = await client.get("/api/v1/notifications/wx-status", headers=_auth())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["wx_configured"] is False
    assert data["uid_masked"] is None


async def test_napi_12_wx_status_no_auth(client: AsyncClient) -> None:
    """GET /notifications/wx-status 无鉴权 → 401。"""
    resp = await client.get("/api/v1/notifications/wx-status")
    assert resp.status_code == 401
