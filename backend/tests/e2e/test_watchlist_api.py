"""WAPI-01~05: 黑白名单 API E2E 测试（ASGI，Mock WatchlistService）"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from quantpilot.core.security import create_token
from quantpilot.main import app
from quantpilot.schemas.scoring import WatchlistItem


def _auth_header() -> dict:
    return {"Authorization": f"Bearer {create_token('access')}"}


def _mock_item(
    ts_code: str = "000001.SZ", list_type: str = "BLACKLIST", note: str = ""
) -> WatchlistItem:
    return WatchlistItem(
        ts_code=ts_code,
        list_type=list_type,
        note=note,
        created_at=datetime(2026, 4, 1, 8, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# WAPI-01: GET /api/v1/watchlist — 空列表
# ---------------------------------------------------------------------------
async def test_wapi_01_get_empty_list(client: AsyncClient) -> None:
    """WAPI-01: GET /api/v1/watchlist（空 DB）→ code=0，data=[]"""
    from quantpilot.api.deps import get_watchlist_service

    mock_svc = AsyncMock()
    mock_svc.get_list = AsyncMock(return_value=[])
    app.dependency_overrides[get_watchlist_service] = lambda: mock_svc
    try:
        resp = await client.get("/api/v1/watchlist", headers=_auth_header())
    finally:
        app.dependency_overrides.pop(get_watchlist_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"] == []


# ---------------------------------------------------------------------------
# WAPI-02: POST /api/v1/watchlist — 添加黑名单
# ---------------------------------------------------------------------------
async def test_wapi_02_add_blacklist(client: AsyncClient) -> None:
    """WAPI-02: POST /api/v1/watchlist → code=0，返回新记录"""
    from quantpilot.api.deps import get_watchlist_service

    mock_item = _mock_item("000001.SZ", "BLACKLIST", "测试备注")
    mock_svc = AsyncMock()
    mock_svc.add = AsyncMock(return_value=mock_item)
    app.dependency_overrides[get_watchlist_service] = lambda: mock_svc
    try:
        resp = await client.post(
            "/api/v1/watchlist",
            json={"ts_code": "000001.SZ", "list_type": "BLACKLIST", "note": "测试备注"},
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.pop(get_watchlist_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["ts_code"] == "000001.SZ"
    assert body["data"]["list_type"] == "BLACKLIST"
    assert "created_at" in body["data"]


# ---------------------------------------------------------------------------
# WAPI-03: POST /api/v1/watchlist — 重复添加（幂等）
# ---------------------------------------------------------------------------
async def test_wapi_03_add_idempotent(client: AsyncClient) -> None:
    """WAPI-03: 重复 POST 同一条目 → code=0，不报错（幂等返回已有记录）"""
    from quantpilot.api.deps import get_watchlist_service

    existing_item = _mock_item("000002.SZ", "WHITELIST")
    mock_svc = AsyncMock()
    mock_svc.add = AsyncMock(return_value=existing_item)
    app.dependency_overrides[get_watchlist_service] = lambda: mock_svc
    try:
        # 第一次添加
        r1 = await client.post(
            "/api/v1/watchlist",
            json={"ts_code": "000002.SZ", "list_type": "WHITELIST"},
            headers=_auth_header(),
        )
        # 第二次添加（幂等）
        r2 = await client.post(
            "/api/v1/watchlist",
            json={"ts_code": "000002.SZ", "list_type": "WHITELIST"},
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.pop(get_watchlist_service, None)

    assert r1.status_code == 200
    assert r1.json()["code"] == 0
    assert r2.status_code == 200
    assert r2.json()["code"] == 0


# ---------------------------------------------------------------------------
# WAPI-04: DELETE /api/v1/watchlist/{ts_code} — 正常删除
# ---------------------------------------------------------------------------
async def test_wapi_04_delete_existing(client: AsyncClient) -> None:
    """WAPI-04: DELETE /api/v1/watchlist/000001.SZ?list_type=BLACKLIST → code=0"""
    from quantpilot.api.deps import get_watchlist_service

    mock_svc = AsyncMock()
    mock_svc.remove = AsyncMock(return_value=None)
    app.dependency_overrides[get_watchlist_service] = lambda: mock_svc
    try:
        resp = await client.delete(
            "/api/v1/watchlist/000001.SZ",
            params={"list_type": "BLACKLIST"},
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.pop(get_watchlist_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"] is None


# ---------------------------------------------------------------------------
# WAPI-05: DELETE /api/v1/watchlist/{ts_code} — 不存在时幂等
# ---------------------------------------------------------------------------
async def test_wapi_05_delete_nonexistent_idempotent(client: AsyncClient) -> None:
    """WAPI-05: 删除不存在的条目 → code=0（幂等）"""
    from quantpilot.api.deps import get_watchlist_service

    mock_svc = AsyncMock()
    mock_svc.remove = AsyncMock(return_value=None)   # service 层静默成功
    app.dependency_overrides[get_watchlist_service] = lambda: mock_svc
    try:
        resp = await client.delete(
            "/api/v1/watchlist/NOEXIST.SZ",
            params={"list_type": "BLACKLIST"},
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.pop(get_watchlist_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0

    # 验证 service.remove 被调用（mock 层面，使用关键字参数匹配）
    mock_svc.remove.assert_called_once_with(ts_code="NOEXIST.SZ", list_type="BLACKLIST")


# ---------------------------------------------------------------------------
# 额外：GET /watchlist 无 token → 401
# ---------------------------------------------------------------------------
async def test_wapi_no_token(client: AsyncClient) -> None:
    """GET /api/v1/watchlist（无 token）→ 401"""
    resp = await client.get("/api/v1/watchlist")
    assert resp.status_code == 401

    resp2 = await client.post("/api/v1/watchlist", json={})
    assert resp2.status_code == 401
