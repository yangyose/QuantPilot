"""DATA-01~04: 数据管理 API E2E 测试（ASGI，Mock DataService）"""
from datetime import date
from unittest.mock import AsyncMock

from httpx import AsyncClient

from quantpilot.core.security import create_token
from quantpilot.main import app
from quantpilot.services.data_service import IngestResult


def _auth_header() -> dict:
    return {"Authorization": f"Bearer {create_token('access')}"}


def _mock_status() -> dict:
    return {
        "latest_quote_date": date(2026, 3, 12),
        "stock_count": 5234,
        "index_codes": ["000001.SH", "000300.SH", "000905.SH", "399006.SZ"],
        "is_up_to_date": True,
        "latest_financial_date": date(2026, 3, 12),
    }


def _mock_ingest_result(trade_date: date = date(2026, 3, 12)) -> IngestResult:
    return IngestResult(
        trade_date=trade_date,
        quote_count=5234,
        financial_count=4987,
        snapshot_version="a" * 64,
        errors=[],
    )


async def test_data_01_get_status_with_token(client: AsyncClient) -> None:
    """DATA-01: GET /api/v1/data/status（有 token）→ 200，code=0，data 含 latest_quote_date"""
    from quantpilot.api.v1.data import get_data_service

    mock_svc = AsyncMock()
    mock_svc.get_status = AsyncMock(return_value=_mock_status())
    app.dependency_overrides[get_data_service] = lambda: mock_svc
    try:
        resp = await client.get("/api/v1/data/status", headers=_auth_header())
    finally:
        app.dependency_overrides.pop(get_data_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert "latest_quote_date" in body["data"]


async def test_data_02_get_status_no_token(client: AsyncClient) -> None:
    """DATA-02: GET /api/v1/data/status（无 token）→ 401"""
    resp = await client.get("/api/v1/data/status")
    assert resp.status_code == 401


async def test_data_03_ingest_daily_with_token(client: AsyncClient) -> None:
    """DATA-03: POST /api/v1/data/ingest/daily（有 token）→ 200，code=0"""
    from quantpilot.api.v1.data import get_data_service

    mock_svc = AsyncMock()
    mock_svc.ingest_daily = AsyncMock(return_value=_mock_ingest_result())
    app.dependency_overrides[get_data_service] = lambda: mock_svc
    try:
        resp = await client.post(
            "/api/v1/data/ingest/daily",
            json={"trade_date": "2026-03-12"},
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.pop(get_data_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["quote_count"] == 5234
    assert "snapshot_version" in body["data"]


async def test_data_04_ingest_daily_non_trade_date(client: AsyncClient) -> None:
    """DATA-04: POST /api/v1/data/ingest/daily（非交易日）→ 400"""
    from quantpilot.api.v1.data import get_data_service

    mock_svc = AsyncMock()
    mock_svc.ingest_daily = AsyncMock(
        side_effect=ValueError("2026-01-01 is not a trading date")
    )
    app.dependency_overrides[get_data_service] = lambda: mock_svc
    try:
        resp = await client.post(
            "/api/v1/data/ingest/daily",
            json={"trade_date": "2026-01-01"},
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.pop(get_data_service, None)

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 400
