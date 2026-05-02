"""E2E 测试：绩效归因 /performance（ASGI，Mock PerformanceService）。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from quantpilot.api.deps import get_performance_service
from quantpilot.core.security import create_token
from quantpilot.main import app


def _auth() -> dict:
    return {"Authorization": f"Bearer {create_token('access')}"}


def _mock_summary() -> dict:
    return {
        "cumulative_return": 0.15,
        "annualized_return": 0.12,
        "max_drawdown": 0.08,
        "sharpe_ratio": 1.5,
        "win_rate": 0.6,
        "profit_loss_ratio": 2.0,
        "benchmark_return": 0.05,
    }


# ─── E2E-PF-01 ───

async def test_pf_01_summary_no_auth(client: AsyncClient) -> None:
    """E2E-PF-01：GET /performance/summary 无鉴权 → 401。"""
    resp = await client.get("/api/v1/performance/summary")
    assert resp.status_code == 401


# ─── E2E-PF-02 ───

async def test_pf_02_summary_no_data(client: AsyncClient) -> None:
    """E2E-PF-02：GET /performance/summary 有鉴权，无账户数据 → 200，data=null。"""
    mock = AsyncMock()
    mock.get_summary = AsyncMock(return_value=None)
    app.dependency_overrides[get_performance_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/performance/summary", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["data"] is None
    finally:
        app.dependency_overrides.pop(get_performance_service, None)


# ─── E2E-PF-03 ───

async def test_pf_03_history_ok(client: AsyncClient) -> None:
    """E2E-PF-03：GET /performance/history 有鉴权 → 200，data.nav_series 为 list。"""
    mock = AsyncMock()
    mock.get_history = AsyncMock(return_value={
        "nav_series": [{"date": "2026-01-03", "nav": 1.0}],
        "benchmark_series": [],
    })
    app.dependency_overrides[get_performance_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/performance/history", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data["nav_series"], list)
    finally:
        app.dependency_overrides.pop(get_performance_service, None)


# ─── E2E-PF-04 ───

async def test_pf_04_attribution_missing_params(client: AsyncClient) -> None:
    """E2E-PF-04：GET /performance/attribution 缺 period_start/period_end → 422。"""
    resp = await client.get("/api/v1/performance/attribution", headers=_auth())
    assert resp.status_code == 422


# ─── E2E-PF-05 ───

async def test_pf_05_attribution_ok(client: AsyncClient) -> None:
    """E2E-PF-05：GET /performance/attribution 有鉴权 + 参数 → 200，data 含各维度归因。"""
    mock = AsyncMock()
    mock.get_attribution = AsyncMock(
        return_value={"by_stock": [], "by_industry": [], "by_strategy": []}
    )
    app.dependency_overrides[get_performance_service] = lambda: mock
    try:
        resp = await client.get(
            "/api/v1/performance/attribution",
            params={"period_start": "2026-01-01", "period_end": "2026-03-31"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "by_stock" in data
        assert "by_industry" in data
        assert "by_strategy" in data
    finally:
        app.dependency_overrides.pop(get_performance_service, None)


# ─── E2E-PF-06 ───

async def test_pf_06_behavior_ok(client: AsyncClient) -> None:
    """E2E-PF-06：GET /performance/behavior 有鉴权 → 200，data 含 signal_compliance_rate。"""
    mock = AsyncMock()
    mock.get_behavioral_analysis = AsyncMock(return_value={
        "avg_holding_days": 12.5,
        "monthly_trade_count": 3.2,
        "signal_compliance_rate": 0.75,
        "stop_loss_execution_rate": None,
        "chase_up_rate": None,
        "pnl_distribution": [],
    })
    app.dependency_overrides[get_performance_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/performance/behavior", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "signal_compliance_rate" in data
    finally:
        app.dependency_overrides.pop(get_performance_service, None)


# ─── 额外：summary 有数据时 cumulative_return 字段存在 ───

async def test_pf_summary_with_data(client: AsyncClient) -> None:
    """GET /performance/summary 有数据 → 200，data 含 cumulative_return 字段。"""
    mock = AsyncMock()
    mock.get_summary = AsyncMock(return_value=_mock_summary())
    app.dependency_overrides[get_performance_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/performance/summary", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "cumulative_return" in data
        assert data["cumulative_return"] == pytest.approx(0.15)
    finally:
        app.dependency_overrides.pop(get_performance_service, None)
