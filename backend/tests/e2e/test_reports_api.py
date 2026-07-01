"""E2E 测试：报告 /reports（ASGI，Mock ReportService）。"""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from httpx import AsyncClient

from quantpilot.api.deps import get_report_service
from quantpilot.core.security import create_token
from quantpilot.main import app
from quantpilot.models.business import Report


def _auth() -> dict:
    return {"Authorization": f"Bearer {create_token('access', '1')}"}


def _mock_report(
    report_id: int = 1,
    report_type: str = "WEEKLY",
    period_start: date = date(2026, 4, 6),
    period_end: date = date(2026, 4, 11),
) -> Report:
    r = MagicMock(spec=Report)
    r.id = report_id
    r.report_type = report_type
    r.period_start = period_start
    r.period_end = period_end
    r.content = {"period": {"start": str(period_start), "end": str(period_end)}}
    r.summary = "测试周报"
    r.generated_at = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    return r


# ---------------------------------------------------------------------------
# GET /reports
# ---------------------------------------------------------------------------

async def test_rp_01_no_auth(client: AsyncClient) -> None:
    """GET /reports 无鉴权 → 401。"""
    resp = await client.get("/api/v1/reports")
    assert resp.status_code == 401


async def test_rp_02_ok_empty(client: AsyncClient) -> None:
    """GET /reports 有鉴权，无数据 → 200，items=[]，total=0。"""
    mock = AsyncMock()
    mock.get_list = AsyncMock(return_value=([], 0))
    app.dependency_overrides[get_report_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/reports", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["items"] == []
        assert data["total"] == 0
    finally:
        app.dependency_overrides.pop(get_report_service, None)


async def test_rp_03_ok_with_data(client: AsyncClient) -> None:
    """GET /reports 有数据 → 200，items 含 id/report_type/period 字段。"""
    mock = AsyncMock()
    mock.get_list = AsyncMock(return_value=([_mock_report()], 1))
    app.dependency_overrides[get_report_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/reports", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        item = data["items"][0]
        assert item["id"] == 1
        assert item["report_type"] == "WEEKLY"
        assert "period_start" in item
        assert "period_end" in item
        assert "generated_at" in item
    finally:
        app.dependency_overrides.pop(get_report_service, None)


async def test_rp_04_filter_params(client: AsyncClient) -> None:
    """GET /reports?report_type=WEEKLY&limit=5&offset=0 → 参数传递正确。"""
    mock = AsyncMock()
    mock.get_list = AsyncMock(return_value=([], 0))
    app.dependency_overrides[get_report_service] = lambda: mock
    try:
        resp = await client.get(
            "/api/v1/reports",
            params={"report_type": "WEEKLY", "limit": 5, "offset": 0},
            headers=_auth(),
        )
        assert resp.status_code == 200
        mock.get_list.assert_called_once_with(
            account_id=1,
            report_type="WEEKLY",
            start_date=None,
            end_date=None,
            limit=5,
            offset=0,
        )
    finally:
        app.dependency_overrides.pop(get_report_service, None)


# ---------------------------------------------------------------------------
# GET /reports/{report_id}
# ---------------------------------------------------------------------------

async def test_rp_05_detail_no_auth(client: AsyncClient) -> None:
    """GET /reports/1 无鉴权 → 401。"""
    resp = await client.get("/api/v1/reports/1")
    assert resp.status_code == 401


async def test_rp_06_detail_not_found(client: AsyncClient) -> None:
    """GET /reports/999 有鉴权，不存在 → 404。"""
    mock = AsyncMock()
    mock.get_by_id = AsyncMock(return_value=None)
    app.dependency_overrides[get_report_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/reports/999", headers=_auth())
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_report_service, None)


async def test_rp_07_detail_ok(client: AsyncClient) -> None:
    """GET /reports/1 有鉴权，存在 → 200，含完整 content。"""
    mock = AsyncMock()
    mock.get_by_id = AsyncMock(return_value=_mock_report())
    app.dependency_overrides[get_report_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/reports/1", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == 1
        assert "content" in data
    finally:
        app.dependency_overrides.pop(get_report_service, None)


# ---------------------------------------------------------------------------
# POST /reports/generate
# ---------------------------------------------------------------------------

async def test_rp_08_generate_no_auth(client: AsyncClient) -> None:
    """POST /reports/generate 无鉴权 → 401。"""
    resp = await client.post(
        "/api/v1/reports/generate",
        json={"start_date": "2026-04-06", "end_date": "2026-04-11"},
    )
    assert resp.status_code == 401


async def test_rp_09_generate_missing_field(client: AsyncClient) -> None:
    """POST /reports/generate 缺少 end_date → 422。"""
    resp = await client.post(
        "/api/v1/reports/generate",
        json={"start_date": "2026-04-06"},
        headers=_auth(),
    )
    assert resp.status_code == 422


async def test_rp_10_generate_ok(client: AsyncClient) -> None:
    """POST /reports/generate 有鉴权，参数合法 → 200，返回 ReportItem（不含 content）。"""
    mock = AsyncMock()
    mock.generate_custom = AsyncMock(return_value=_mock_report(report_type="CUSTOM"))
    app.dependency_overrides[get_report_service] = lambda: mock
    try:
        resp = await client.post(
            "/api/v1/reports/generate",
            json={"start_date": "2026-04-06", "end_date": "2026-04-11"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["report_type"] == "CUSTOM"
        assert "id" in data
        mock.generate_custom.assert_called_once()
    finally:
        app.dependency_overrides.pop(get_report_service, None)
