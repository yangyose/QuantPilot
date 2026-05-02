"""SAPI-01~06: 信号 API E2E 测试（ASGI，Mock SignalService）。"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from quantpilot.api.deps import get_lineage_service, get_signal_service
from quantpilot.core.security import create_token
from quantpilot.main import app
from quantpilot.models.business import Signal


def _auth_header() -> dict:
    return {"Authorization": f"Bearer {create_token('access')}"}


def _mock_signal(
    signal_id: int = 1,
    ts_code: str = "000001.SZ",
    signal_type: str = "BUY",
    trade_date: date = date(2026, 4, 8),
    status: str = "NEW",
) -> Signal:
    s = MagicMock(spec=Signal)
    s.id = signal_id
    s.ts_code = ts_code
    s.signal_type = signal_type
    s.trade_date = trade_date
    s.score = 88.5
    s.suggested_pct = 0.10
    s.suggested_price_low = 9.90
    s.suggested_price_high = 10.20
    s.stop_loss_price = 9.57
    s.signal_strength = "MODERATE"
    s.liquidity_note = None
    s.t1_warning = "A股T+1制度：买入当日不可卖出"
    s.reason = "综合评分 88.5"
    s.status = status
    s.created_at = datetime(2026, 4, 8, 9, 30, 0)
    return s


# ---------------------------------------------------------------------------
# SAPI-01: GET /signals（mock 返回空列表）
# ---------------------------------------------------------------------------
async def test_sapi_01_get_signals_empty(client: AsyncClient) -> None:
    """SAPI-01: GET /signals → 200, data.signals=[]"""
    mock_service = AsyncMock()
    mock_service.get_today_signals = AsyncMock(return_value=[])

    app.dependency_overrides[get_signal_service] = lambda: mock_service
    try:
        resp = await client.get("/api/v1/signals", headers=_auth_header())
    finally:
        app.dependency_overrides.pop(get_signal_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["signals"] == []
    assert body["data"]["total"] == 0


# ---------------------------------------------------------------------------
# SAPI-02: GET /signals（mock 返回 2 条信号）
# ---------------------------------------------------------------------------
async def test_sapi_02_get_signals_with_data(client: AsyncClient) -> None:
    """SAPI-02: GET /signals → 200, data.signals 长度=2，字段完整"""
    sigs = [_mock_signal(1, "000001.SZ"), _mock_signal(2, "000002.SZ")]
    mock_service = AsyncMock()
    mock_service.get_today_signals = AsyncMock(return_value=sigs)

    app.dependency_overrides[get_signal_service] = lambda: mock_service
    try:
        resp = await client.get("/api/v1/signals", headers=_auth_header())
    finally:
        app.dependency_overrides.pop(get_signal_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["total"] == 2
    assert len(data["signals"]) == 2
    item = data["signals"][0]
    assert "id" in item
    assert "ts_code" in item
    assert "signal_type" in item
    assert "score" in item
    assert "status" in item


# ---------------------------------------------------------------------------
# SAPI-03: PATCH /signals/1/status，status=VIEWED → 200
# ---------------------------------------------------------------------------
async def test_sapi_03_update_status_viewed(client: AsyncClient) -> None:
    """SAPI-03: PATCH /signals/1/status，status=VIEWED → 200，状态更新"""
    updated = _mock_signal(1, status="VIEWED")
    mock_service = AsyncMock()
    mock_service.update_status = AsyncMock(return_value=updated)

    app.dependency_overrides[get_signal_service] = lambda: mock_service
    try:
        resp = await client.patch(
            "/api/v1/signals/1/status",
            json={"status": "VIEWED"},
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.pop(get_signal_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "VIEWED"


# ---------------------------------------------------------------------------
# SAPI-04: PATCH /signals/1/status，status=INVALID → 422
# ---------------------------------------------------------------------------
async def test_sapi_04_update_status_invalid(client: AsyncClient) -> None:
    """SAPI-04: status=INVALID 不在允许集合 → 422，errors 字段存在"""
    mock_service = AsyncMock()
    app.dependency_overrides[get_signal_service] = lambda: mock_service
    try:
        resp = await client.patch(
            "/api/v1/signals/1/status",
            json={"status": "INVALID"},
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.pop(get_signal_service, None)

    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 422
    assert "errors" in body


# ---------------------------------------------------------------------------
# SAPI-05: GET /signals/1/lineage（Phase 7 重构：由 LineageService 提供）
# ---------------------------------------------------------------------------
async def test_sapi_05_get_lineage(client: AsyncClient) -> None:
    """SAPI-05: GET /signals/1/lineage → 200，返回 signal_id/score_snapshot/pipeline_run 结构。

    Phase 7 D-12a：改由 LineageService 注入，响应结构更新为
    {signal_id, trade_date, score_snapshot, pipeline_run}。
    """
    lineage_data = {
        "signal_id": 1,
        "trade_date": "2026-04-08",
        "score_snapshot": {
            "ts_code": "000001.SZ",
            "composite_score": 88.5,
            "market_state": "UPTREND",
            "score_breakdown": {"trend": {"score": 90.0, "weight": 0.4}},
        },
        "pipeline_run": None,
    }

    mock_lineage = AsyncMock()
    mock_lineage.get_signal_lineage = AsyncMock(return_value=lineage_data)

    app.dependency_overrides[get_lineage_service] = lambda: mock_lineage
    try:
        resp = await client.get("/api/v1/signals/1/lineage", headers=_auth_header())
    finally:
        app.dependency_overrides.pop(get_lineage_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["signal_id"] == 1
    assert "score_snapshot" in data
    assert data["score_snapshot"]["composite_score"] == 88.5
    assert "pipeline_run" in data


# ---------------------------------------------------------------------------
# SAPI-06: GET /signals/history，带 ts_code 过滤
# ---------------------------------------------------------------------------
async def test_sapi_06_signal_history_with_filter(client: AsyncClient) -> None:
    """SAPI-06: GET /signals/history?ts_code=000001.SZ → 200，结果按过滤条件正确"""
    sigs = [_mock_signal(1, "000001.SZ"), _mock_signal(3, "000001.SZ")]
    mock_service = AsyncMock()
    mock_service.get_signal_history = AsyncMock(return_value=sigs)

    app.dependency_overrides[get_signal_service] = lambda: mock_service
    try:
        resp = await client.get(
            "/api/v1/signals/history",
            params={"ts_code": "000001.SZ"},
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.pop(get_signal_service, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert len(data["signals"]) == 2
    for item in data["signals"]:
        assert item["ts_code"] == "000001.SZ"
