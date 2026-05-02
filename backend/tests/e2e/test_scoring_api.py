"""SAPI-01~04: 候选股池与评分 API E2E 测试（ASGI，Mock Repository/Service）"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from quantpilot.core.security import create_token
from quantpilot.main import app
from quantpilot.models.business import CandidatePool


def _auth_header() -> dict:
    return {"Authorization": f"Bearer {create_token('access')}"}


def _mock_pool_record(
    ts_code: str = "000001.SZ",
    composite_score: float = 85.3,
    trade_date: date = date(2026, 4, 1),
    in_pool: bool = True,
    is_holding: bool = False,
    market_state: str = "UPTREND",
) -> CandidatePool:
    r = MagicMock(spec=CandidatePool)
    r.ts_code = ts_code
    r.trade_date = trade_date
    r.composite_score = composite_score
    r.trend_score = 90.1
    r.momentum_score = 88.2
    r.reversion_score = 70.5
    r.value_score = 80.0
    r.in_pool = in_pool
    r.is_holding = is_holding
    r.market_state = market_state
    return r


# ---------------------------------------------------------------------------
# SAPI-01: GET /api/v1/market/pool — 正常返回候选股池
# ---------------------------------------------------------------------------
async def test_sapi_01_get_pool_normal(client: AsyncClient) -> None:
    """SAPI-01: GET /api/v1/market/pool → code=0，data.pool 包含 rank/is_holding/is_watchlist"""
    from quantpilot.api.deps import get_repo

    mock_repo = AsyncMock()
    mock_repo.get_latest_quote_date = AsyncMock(return_value=date(2026, 4, 1))
    mock_repo.get_pool = AsyncMock(return_value=[
        _mock_pool_record("000001.SZ", 85.3),
        _mock_pool_record("000002.SZ", 78.1),
    ])
    mock_repo.get_whitelist_codes = AsyncMock(return_value={"000001.SZ"})
    mock_repo.get_stock_info_bulk = AsyncMock(return_value=__import__("pandas").DataFrame(
        {"name": ["平安银行", "万科A"]},
        index=__import__("pandas").Index(["000001.SZ", "000002.SZ"], name="ts_code"),
    ))

    app.dependency_overrides[get_repo] = lambda: mock_repo
    try:
        resp = await client.get("/api/v1/market/pool", headers=_auth_header())
    finally:
        app.dependency_overrides.pop(get_repo, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert "pool" in data
    assert "total" in data
    assert data["total"] >= 0
    if data["pool"]:
        item = data["pool"][0]
        assert "rank" in item
        assert "ts_code" in item
        assert "is_holding" in item
        assert "is_watchlist" in item
        assert "composite_score" in item


# ---------------------------------------------------------------------------
# SAPI-02: GET /api/v1/market/pool — 无 token → 401
# ---------------------------------------------------------------------------
async def test_sapi_02_get_pool_no_token(client: AsyncClient) -> None:
    """SAPI-02: GET /api/v1/market/pool（无 token）→ 401"""
    resp = await client.get("/api/v1/market/pool")
    assert resp.status_code == 401
    assert resp.json()["code"] == 401


# ---------------------------------------------------------------------------
# SAPI-03: GET /api/v1/market/stock/{ts_code}/score — 正常返回
# ---------------------------------------------------------------------------
async def test_sapi_03_get_stock_score(client: AsyncClient) -> None:
    """SAPI-03: GET /api/v1/market/stock/000001.SZ/score → code=0，data.history 为列表"""
    from quantpilot.api.deps import get_repo

    mock_repo = AsyncMock()
    mock_repo.get_stock_scores = AsyncMock(return_value=[
        _mock_pool_record("000001.SZ", 85.3, date(2026, 4, 1)),
        _mock_pool_record("000001.SZ", 82.0, date(2026, 3, 31)),
    ])

    app.dependency_overrides[get_repo] = lambda: mock_repo
    try:
        resp = await client.get(
            "/api/v1/market/stock/000001.SZ/score",
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.pop(get_repo, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["ts_code"] == "000001.SZ"
    assert isinstance(data["history"], list)
    if data["history"]:
        item = data["history"][0]
        assert "trade_date" in item
        assert "composite_score" in item
        assert "market_state" in item


# ---------------------------------------------------------------------------
# SAPI-04: GET /api/v1/market/stock/{ts_code}/score — 无 token → 401
# ---------------------------------------------------------------------------
async def test_sapi_04_get_stock_score_no_token(client: AsyncClient) -> None:
    """SAPI-04: GET /api/v1/market/stock/{ts_code}/score（无 token）→ 401"""
    resp = await client.get("/api/v1/market/stock/000001.SZ/score")
    assert resp.status_code == 401
    assert resp.json()["code"] == 401
