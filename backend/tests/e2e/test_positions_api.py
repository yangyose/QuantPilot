"""E2E 测试：持仓管理 /positions（ASGI，Mock AccountService）。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from httpx import AsyncClient

from quantpilot.api.deps import get_account_service
from quantpilot.core.security import create_token
from quantpilot.main import app
from quantpilot.models.account import Position


def _auth() -> dict:
    return {"Authorization": f"Bearer {create_token('access')}"}


def _mock_position(
    position_id: int = 1,
    account_id: int = 1,
    ts_code: str = "000001.SZ",
    shares: int = 1000,
    phase: str = "BUILD",
) -> Position:
    p = MagicMock(spec=Position)
    p.id = position_id
    p.account_id = account_id
    p.ts_code = ts_code
    p.shares = shares
    p.cost_price = 10.0
    p.current_price = 11.0
    p.market_value = 11000.0
    p.pnl_pct = 0.1
    p.open_date = None
    p.phase = phase
    return p


# ---------------------------------------------------------------------------
# GET /positions
# ---------------------------------------------------------------------------

async def test_papi_01_get_positions_no_auth(client: AsyncClient) -> None:
    """GET /positions 无鉴权 → 401。"""
    resp = await client.get("/api/v1/positions", params={"account_id": 1})
    assert resp.status_code == 401


async def test_papi_02_get_positions_empty(client: AsyncClient) -> None:
    """GET /positions 有鉴权 → 200，空列表。"""
    mock = AsyncMock()
    mock.get_positions = AsyncMock(return_value=[])
    app.dependency_overrides[get_account_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/positions", params={"account_id": 1}, headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"] == []
    finally:
        app.dependency_overrides.pop(get_account_service, None)


async def test_papi_03_get_positions_with_data(client: AsyncClient) -> None:
    """GET /positions 有鉴权 → 200，返回持仓列表结构。"""
    mock = AsyncMock()
    mock.get_positions = AsyncMock(return_value=[_mock_position()])
    app.dependency_overrides[get_account_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/positions", params={"account_id": 1}, headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) == 1
        item = data[0]
        assert item["ts_code"] == "000001.SZ"
        assert item["shares"] == 1000
        assert item["phase"] == "BUILD"
    finally:
        app.dependency_overrides.pop(get_account_service, None)


# ---------------------------------------------------------------------------
# POST /positions —— 已废除（持仓=成交流水派生视图，手工录入会破坏一致性，
# 2026-06-24）。建仓走 POST /account/trades 开仓 BUY。
# ---------------------------------------------------------------------------

async def test_papi_04_create_position_removed(client: AsyncClient) -> None:
    """POST /positions 端点已下线 → 405（路由不存在该方法）。"""
    resp = await client.post(
        "/api/v1/positions",
        json={"account_id": 1, "ts_code": "000001.SZ", "shares": 1000, "cost_price": 10.0},
        headers=_auth(),
    )
    assert resp.status_code == 405


# ---------------------------------------------------------------------------
# PATCH /positions/{id}
# ---------------------------------------------------------------------------

async def test_papi_07_patch_position_no_auth(client: AsyncClient) -> None:
    """PATCH /positions/{id} 无鉴权 → 401。"""
    resp = await client.patch("/api/v1/positions/1", json={"phase": "HOLD"})
    assert resp.status_code == 401


async def test_papi_08_patch_position_ok(client: AsyncClient) -> None:
    """PATCH /positions/{id} 有鉴权 → 200，返回更新后结构。"""
    updated = _mock_position(phase="HOLD")
    mock = AsyncMock()
    mock.update_position = AsyncMock(return_value=updated)
    app.dependency_overrides[get_account_service] = lambda: mock
    try:
        resp = await client.patch(
            "/api/v1/positions/1", json={"phase": "HOLD"}, headers=_auth()
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["phase"] == "HOLD"
    finally:
        app.dependency_overrides.pop(get_account_service, None)


async def test_papi_09_patch_position_invalid_phase(client: AsyncClient) -> None:
    """PATCH /positions/{id} phase 非法值 → 422（Pydantic Literal 校验）。"""
    mock = AsyncMock()
    app.dependency_overrides[get_account_service] = lambda: mock
    try:
        resp = await client.patch(
            "/api/v1/positions/1", json={"phase": "INVALID"}, headers=_auth()
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "errors" in body or "detail" in body
    finally:
        app.dependency_overrides.pop(get_account_service, None)


async def test_papi_10_patch_position_not_found(client: AsyncClient) -> None:
    """PATCH /positions/{id} 不存在 → 404。"""
    mock = AsyncMock()
    mock.update_position = AsyncMock(side_effect=ValueError("Position 999 not found"))
    app.dependency_overrides[get_account_service] = lambda: mock
    try:
        resp = await client.patch(
            "/api/v1/positions/999", json={"phase": "HOLD"}, headers=_auth()
        )
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_account_service, None)
