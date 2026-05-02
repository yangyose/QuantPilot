"""E2E 测试：首次启动向导 /setup（Phase 10 §6.6）。

覆盖 E2E-SETUP-01~02 + API-79~80：
- 401 无鉴权
- GET /setup/status 初次/已完成两种状态
- POST /setup/complete → completed=true + ISO 时间戳
"""
from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from quantpilot.api.deps import get_setup_service
from quantpilot.core.security import create_token
from quantpilot.main import app


def _auth() -> dict:
    return {"Authorization": f"Bearer {create_token('access')}"}


# ───────────────────── GET /setup/status ─────────────────────

@pytest.mark.anyio
async def test_sapi_setup_01_status_no_auth(client: AsyncClient) -> None:
    """GET /setup/status 无鉴权 → 401。"""
    resp = await client.get("/api/v1/setup/status")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_sapi_setup_02_status_initial(client: AsyncClient) -> None:
    """GET /setup/status 初次 → {completed: false, completed_at: null}。"""
    mock = AsyncMock()
    mock.get_status = AsyncMock(return_value={"completed": False, "completed_at": None})
    app.dependency_overrides[get_setup_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/setup/status", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"] == {"completed": False, "completed_at": None}
    finally:
        app.dependency_overrides.pop(get_setup_service, None)


@pytest.mark.anyio
async def test_sapi_setup_03_status_completed(client: AsyncClient) -> None:
    """GET /setup/status 已完成 → {completed: true, completed_at: iso}。"""
    mock = AsyncMock()
    mock.get_status = AsyncMock(
        return_value={"completed": True, "completed_at": "2026-04-22T03:30:00+00:00"}
    )
    app.dependency_overrides[get_setup_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/setup/status", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["completed"] is True
        assert data["completed_at"] == "2026-04-22T03:30:00+00:00"
    finally:
        app.dependency_overrides.pop(get_setup_service, None)


# ───────────────────── POST /setup/complete ─────────────────────

@pytest.mark.anyio
async def test_sapi_setup_04_complete_no_auth(client: AsyncClient) -> None:
    """POST /setup/complete 无鉴权 → 401。"""
    resp = await client.post("/api/v1/setup/complete")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_sapi_setup_05_complete_ok(client: AsyncClient) -> None:
    """POST /setup/complete → 200 + completed=true + ISO 时间戳。"""
    mock = AsyncMock()
    mock.mark_completed = AsyncMock(
        return_value={"completed": True, "completed_at": "2026-04-22T03:30:00+00:00"}
    )
    app.dependency_overrides[get_setup_service] = lambda: mock
    try:
        resp = await client.post("/api/v1/setup/complete", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["completed"] is True
        # ISO-8601 格式验证（允许 +00:00 或 Z）
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", data["completed_at"])
        mock.mark_completed.assert_awaited_once()
    finally:
        app.dependency_overrides.pop(get_setup_service, None)
