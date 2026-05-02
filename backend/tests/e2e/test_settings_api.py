"""E2E 测试：用户配置管理 /settings（ASGI，Mock SettingsService）。"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from quantpilot.api.deps import get_settings_service
from quantpilot.core.security import create_token
from quantpilot.main import app
from quantpilot.models.system import UserConfig, UserConfigHistory


def _auth() -> dict:
    return {"Authorization": f"Bearer {create_token('access')}"}


def _mock_config(
    config_id: int = 1,
    config_key: str = "buy_threshold",
    config_value: dict | None = None,
) -> UserConfig:
    c = MagicMock(spec=UserConfig)
    c.id = config_id
    c.config_key = config_key
    c.config_value = config_value or {"value": 80}
    c.user_level = "L2"
    c.description = None
    c.updated_at = datetime(2026, 4, 10, 12, 0, 0)
    return c


def _mock_history(
    history_id: int = 1,
    config_key: str = "buy_threshold",
    old_value: dict | None = None,
) -> UserConfigHistory:
    h = MagicMock(spec=UserConfigHistory)
    h.id = history_id
    h.config_key = config_key
    h.old_value = old_value
    h.new_value = {"value": 80}
    h.changed_at = datetime(2026, 4, 10, 12, 0, 0)
    h.change_note = None
    return h


# ---------------------------------------------------------------------------
# GET /settings
# ---------------------------------------------------------------------------

async def test_sapi_01_get_settings_no_auth(client: AsyncClient) -> None:
    """GET /settings 无鉴权 → 401。"""
    resp = await client.get("/api/v1/settings")
    assert resp.status_code == 401


async def test_sapi_02_get_settings_ok(client: AsyncClient) -> None:
    """GET /settings 有鉴权 → 200，返回配置列表。"""
    mock = AsyncMock()
    mock.get_settings = AsyncMock(return_value=[_mock_config()])
    app.dependency_overrides[get_settings_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/settings", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["config_key"] == "buy_threshold"
        assert "config_value" in data[0]
    finally:
        app.dependency_overrides.pop(get_settings_service, None)


# ---------------------------------------------------------------------------
# PUT /settings
# ---------------------------------------------------------------------------

async def test_sapi_03_put_settings_no_auth(client: AsyncClient) -> None:
    """PUT /settings 无鉴权 → 401。"""
    resp = await client.put("/api/v1/settings", json={
        "config_key": "signal_params", "config_value": {"buy_threshold": 85},
    })
    assert resp.status_code == 401


async def test_sapi_04_put_settings_ok(client: AsyncClient) -> None:
    """PUT /settings 有鉴权 + 合法 12-key → 200，返回更新后的 UserConfigItem。"""
    mock = AsyncMock()
    mock.upsert_setting = AsyncMock(
        return_value=_mock_config(
            config_key="signal_params", config_value={"buy_threshold": 85}
        )
    )
    app.dependency_overrides[get_settings_service] = lambda: mock
    try:
        resp = await client.put(
            "/api/v1/settings",
            json={"config_key": "signal_params", "config_value": {"buy_threshold": 85}},
            headers=_auth(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert "config_key" in body["data"]
        assert "config_value" in body["data"]
        assert "updated_at" in body["data"]
    finally:
        app.dependency_overrides.pop(get_settings_service, None)


async def test_sapi_04b_put_settings_unknown_key(client: AsyncClient) -> None:
    """PUT /settings 未知 config_key → 400（Phase 10 §6.9 12-key 白名单）。"""
    mock = AsyncMock()
    # upsert_setting 不应被调用，但保留 spec 以便 dependency 注入成功
    mock.upsert_setting = AsyncMock(return_value=_mock_config())
    app.dependency_overrides[get_settings_service] = lambda: mock
    try:
        resp = await client.put(
            "/api/v1/settings",
            json={
                "config_key": "initial_account_config",
                "config_value": {"initial_cash": 100000},
            },
            headers=_auth(),
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "initial_account_config" in body["msg"]
        # 未知 key 必须在 service 层之前被拦截
        mock.upsert_setting.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_settings_service, None)


# ---------------------------------------------------------------------------
# GET /settings/config-history
# ---------------------------------------------------------------------------

async def test_sapi_05_config_history_no_auth(client: AsyncClient) -> None:
    """GET /settings/config-history 无鉴权 → 401。"""
    resp = await client.get("/api/v1/settings/config-history")
    assert resp.status_code == 401


async def test_sapi_06_config_history_ok(client: AsyncClient) -> None:
    """GET /settings/config-history 有鉴权 → 200，含 items/total 分页结构。"""
    mock = AsyncMock()
    mock.get_config_history = AsyncMock(return_value=([_mock_history()], 1))
    app.dependency_overrides[get_settings_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/settings/config-history", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
    finally:
        app.dependency_overrides.pop(get_settings_service, None)


# ---------------------------------------------------------------------------
# POST /settings/config-history/{id}/revert
# ---------------------------------------------------------------------------

async def test_sapi_07_revert_not_found(client: AsyncClient) -> None:
    """POST /settings/config-history/999/revert 不存在 → 404。"""
    mock = AsyncMock()
    mock.revert_config = AsyncMock(side_effect=ValueError("Config history 999 not found"))
    app.dependency_overrides[get_settings_service] = lambda: mock
    try:
        resp = await client.post(
            "/api/v1/settings/config-history/999/revert", headers=_auth()
        )
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_settings_service, None)


async def test_sapi_08_revert_no_old_value(client: AsyncClient) -> None:
    """POST /settings/config-history/{id}/revert old_value=None → 400。"""
    mock = AsyncMock()
    mock.revert_config = AsyncMock(
        side_effect=ValueError("无法回退：历史记录 1 为首次创建，old_value=None")
    )
    app.dependency_overrides[get_settings_service] = lambda: mock
    try:
        resp = await client.post(
            "/api/v1/settings/config-history/1/revert", headers=_auth()
        )
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.pop(get_settings_service, None)


async def test_sapi_09_revert_ok(client: AsyncClient) -> None:
    """POST /settings/config-history/{id}/revert 有鉴权 → 200，返回恢复后的配置。"""
    mock = AsyncMock()
    mock.revert_config = AsyncMock(return_value=_mock_config(config_value={"value": 75}))
    app.dependency_overrides[get_settings_service] = lambda: mock
    try:
        resp = await client.post(
            "/api/v1/settings/config-history/2/revert", headers=_auth()
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0
        assert "config_key" in resp.json()["data"]
    finally:
        app.dependency_overrides.pop(get_settings_service, None)
