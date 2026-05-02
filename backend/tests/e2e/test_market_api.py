"""MAPI-01~04: 市场状态 API E2E 测试（ASGI，Mock MarketStateService）"""
from datetime import date
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from quantpilot.core.security import create_token
from quantpilot.engine.market_state import MarketStateEnum, MarketStateRecord
from quantpilot.main import app


def _auth_header() -> dict:
    return {"Authorization": f"Bearer {create_token('access')}"}


def _mock_record() -> MarketStateRecord:
    return MarketStateRecord(
        trade_date=date(2026, 3, 26),
        market_state=MarketStateEnum.UPTREND,
        trend_strength=32.5,
        adx_value=32.5,
        ma20=3850.20,
        ma60=3720.40,
        state_changed=False,
        description="上涨趋势：ADX=32.5，均线多头排列（MA20=3850.20 > MA60=3720.40）",
    )


@pytest.mark.anyio
async def test_mapi_01_get_state_with_token(client: AsyncClient) -> None:
    """MAPI-01: GET /api/v1/market/state（有 token）→ 200，code=0，data.current 含 market_state"""
    from quantpilot.api.deps import get_market_state_service

    mock_svc = AsyncMock()
    mock_svc.get_current_state = AsyncMock(return_value=_mock_record())
    app.dependency_overrides[get_market_state_service] = lambda: mock_svc
    try:
        resp = await client.get("/api/v1/market/state", headers=_auth_header())
    finally:
        app.dependency_overrides.pop(get_market_state_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["current"] is not None
    assert "market_state" in body["data"]["current"]
    assert body["data"]["current"]["market_state"] == "UPTREND"


@pytest.mark.anyio
async def test_mapi_02_get_state_no_token(client: AsyncClient) -> None:
    """MAPI-02: GET /api/v1/market/state（无 token）→ 401"""
    resp = await client.get("/api/v1/market/state")
    assert resp.status_code == 401
    assert resp.json()["code"] == 401


@pytest.mark.anyio
async def test_mapi_03_get_state_no_history(client: AsyncClient) -> None:
    """MAPI-03: 无历史记录时 data.current == null"""
    from quantpilot.api.deps import get_market_state_service

    mock_svc = AsyncMock()
    mock_svc.get_current_state = AsyncMock(return_value=None)
    app.dependency_overrides[get_market_state_service] = lambda: mock_svc
    try:
        resp = await client.get("/api/v1/market/state", headers=_auth_header())
    finally:
        app.dependency_overrides.pop(get_market_state_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["current"] is None


@pytest.mark.anyio
async def test_mapi_04_get_state_history(client: AsyncClient) -> None:
    """MAPI-04: GET /api/v1/market/state/history?start=...&end=... → 200，data.items 为列表"""
    from quantpilot.api.deps import get_market_state_service

    mock_svc = AsyncMock()
    mock_svc.get_state_history = AsyncMock(return_value=[_mock_record()])
    app.dependency_overrides[get_market_state_service] = lambda: mock_svc
    try:
        resp = await client.get(
            "/api/v1/market/state/history",
            params={"start": "2026-01-01", "end": "2026-01-31"},
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.pop(get_market_state_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert isinstance(body["data"]["items"], list)
    assert body["data"]["total"] >= 0
