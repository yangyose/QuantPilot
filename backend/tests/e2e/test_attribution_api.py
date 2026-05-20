"""E2E-P12-B-01~04: /attribution/* API 端到端测试（Phase 12 P12-B5）。

依据 phase12_factor_lineage.md §6.3：
- 01: GET /attribution/history 200 + items 数组结构
- 02: start_date > end_date → 422
- 03: GET /attribution/summary 200 + 4 因子 cum_beta
- 04: 全部端点未鉴权 → 401
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

from httpx import AsyncClient

from quantpilot.api.deps import get_attribution_service
from quantpilot.core.security import create_token
from quantpilot.main import app
from quantpilot.services.attribution_service import AttributionSummary

_STRATEGIES = ["trend", "momentum", "mean_reversion", "value"]


def _auth_header() -> dict:
    return {"Authorization": f"Bearer {create_token('access')}"}


def _mock_attribution_row(factor: str, beta: float) -> MagicMock:
    row = MagicMock()
    row.calc_date = date(2026, 4, 30)
    row.factor = factor
    row.beta = beta
    row.t_stat = 1.85
    row.residual_std = 0.012
    row.r_squared = 0.058
    row.sample_size = 1500
    row.window_days = 20
    row.created_at = datetime(2026, 5, 1, 0, 0, 0)
    return row


# ---------------------------------------------------------------------------
# E2E-P12-B-01: GET /attribution/history 200 + items 数组
# ---------------------------------------------------------------------------
async def test_e2e_p12_b_01_history_200(client: AsyncClient) -> None:
    mock_rows = [_mock_attribution_row(s, 0.01 * (i + 1)) for i, s in enumerate(_STRATEGIES)]
    mock_svc = AsyncMock()
    mock_svc.get_history = AsyncMock(return_value=mock_rows)

    app.dependency_overrides[get_attribution_service] = lambda: mock_svc
    try:
        resp = await client.get(
            "/api/v1/attribution/history",
            params={"start_date": "2026-01-01", "end_date": "2026-04-30"},
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.pop(get_attribution_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["total"] == 4
    assert len(data["items"]) == 4
    item = data["items"][0]
    assert "calc_date" in item
    assert "factor" in item
    assert "beta" in item
    assert "sample_size" in item
    assert "window_days" in item


# ---------------------------------------------------------------------------
# E2E-P12-B-02: start_date > end_date → 422
# ---------------------------------------------------------------------------
async def test_e2e_p12_b_02_history_invalid_range_422(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/v1/attribution/history",
        params={"start_date": "2026-01-01", "end_date": "2025-01-01"},
        headers=_auth_header(),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 422
    assert "errors" in body


# ---------------------------------------------------------------------------
# E2E-P12-B-03: GET /attribution/summary 200 + 4 因子 cum_beta
# ---------------------------------------------------------------------------
async def test_e2e_p12_b_03_summary_200(client: AsyncClient) -> None:
    summary = AttributionSummary(
        start=date(2026, 1, 1),
        end=date(2026, 4, 30),
        cum_beta={s: 0.04 + 0.01 * i for i, s in enumerate(_STRATEGIES)},
        avg_r_squared=0.055,
        total_sample=6000,
        months=4,
    )
    mock_svc = AsyncMock()
    mock_svc.get_summary = AsyncMock(return_value=summary)

    app.dependency_overrides[get_attribution_service] = lambda: mock_svc
    try:
        resp = await client.get(
            "/api/v1/attribution/summary",
            params={"start_date": "2026-01-01", "end_date": "2026-04-30"},
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.pop(get_attribution_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert set(data["cum_beta"].keys()) == set(_STRATEGIES)
    assert data["months"] == 4
    assert data["total_sample"] == 6000


# ---------------------------------------------------------------------------
# E2E-P12-B-04: 未鉴权 → 401
# ---------------------------------------------------------------------------
async def test_e2e_p12_b_04_unauthenticated_401(client: AsyncClient) -> None:
    """history + summary 都需 JWT Bearer，未带 Authorization 头 → 401。"""
    for path in ("/api/v1/attribution/history", "/api/v1/attribution/summary"):
        resp = await client.get(
            path,
            params={"start_date": "2026-01-01", "end_date": "2026-04-30"},
        )
        assert resp.status_code == 401, f"{path} expected 401 got {resp.status_code}"
