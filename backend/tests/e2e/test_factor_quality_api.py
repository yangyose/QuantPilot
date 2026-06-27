"""E2E 测试：因子质量 /factor-quality（ASGI，Mock FactorMonitorService）。"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

from httpx import AsyncClient

from quantpilot.api.deps import get_factor_monitor_service
from quantpilot.core.security import create_token
from quantpilot.main import app
from quantpilot.models.business import FactorICWindowState


def _auth() -> dict:
    return {"Authorization": f"Bearer {create_token('access')}"}


def _mock_ic_record(
    strategy: str = "TrendStrategy",
    factor: str = "trend_score",
    ic: float = 0.12,
    alert: str | None = None,
) -> FactorICWindowState:
    """Phase 15 §15-7：service.get_latest/get_history 现返回 FactorICWindowState
    monthly_quality 行（NEW 列名）；API 经 from_window_state 映射为旧响应字段名。"""
    r = MagicMock(spec=FactorICWindowState)
    r.id = 1
    r.trade_date = date(2026, 3, 31)
    r.strategy = strategy
    r.factor = factor
    r.ic_value = ic
    r.ic_mean_state = 0.09
    r.ic_std_state = 0.03
    r.icir = 3.0
    r.half_life = 15
    r.alert_status = alert
    return r


# ---------------------------------------------------------------------------
# GET /factor-quality
# ---------------------------------------------------------------------------

async def test_fq_01_no_auth(client: AsyncClient) -> None:
    """GET /factor-quality 无鉴权 → 401。"""
    resp = await client.get("/api/v1/factor-quality")
    assert resp.status_code == 401


async def test_fq_02_ok_empty(client: AsyncClient) -> None:
    """GET /factor-quality 有鉴权，无数据 → 200，items=[]。"""
    mock = AsyncMock()
    mock.get_latest = AsyncMock(return_value=[])
    app.dependency_overrides[get_factor_monitor_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/factor-quality", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["items"] == []
    finally:
        app.dependency_overrides.pop(get_factor_monitor_service, None)


async def test_fq_03_ok_with_data(client: AsyncClient) -> None:
    """GET /factor-quality 有数据 → 200，items 含 calc_month/strategy/factor/ic 字段。"""
    mock = AsyncMock()
    mock.get_latest = AsyncMock(return_value=[_mock_ic_record()])
    app.dependency_overrides[get_factor_monitor_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/factor-quality", headers=_auth())
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) == 1
        item = items[0]
        assert item["calc_month"] == "2026-03-31"
        assert item["strategy_name"] == "TrendStrategy"
        assert item["factor_name"] == "trend_score"
        assert "ic_value" in item
        assert "ir_3m" in item
        assert "half_life_days" in item
        assert "alert_status" in item
    finally:
        app.dependency_overrides.pop(get_factor_monitor_service, None)


async def test_fq_04_filter_by_strategy(client: AsyncClient) -> None:
    """GET /factor-quality?strategy_name=X → service.get_latest 被传入 strategy_name。"""
    mock = AsyncMock()
    mock.get_latest = AsyncMock(return_value=[_mock_ic_record()])
    app.dependency_overrides[get_factor_monitor_service] = lambda: mock
    try:
        resp = await client.get(
            "/api/v1/factor-quality",
            params={"strategy_name": "TrendStrategy"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        mock.get_latest.assert_called_once_with(strategy_name="TrendStrategy")
    finally:
        app.dependency_overrides.pop(get_factor_monitor_service, None)


# ---------------------------------------------------------------------------
# GET /factor-quality/history
# ---------------------------------------------------------------------------

async def test_fq_05_history_no_auth(client: AsyncClient) -> None:
    """GET /factor-quality/history 无鉴权 → 401。"""
    resp = await client.get("/api/v1/factor-quality/history")
    assert resp.status_code == 401


async def test_fq_06_history_ok(client: AsyncClient) -> None:
    """GET /factor-quality/history 有鉴权 → 200，含 items/total 分页结构。"""
    mock = AsyncMock()
    mock.get_history = AsyncMock(return_value=([], 0))
    app.dependency_overrides[get_factor_monitor_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/factor-quality/history", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
    finally:
        app.dependency_overrides.pop(get_factor_monitor_service, None)


async def test_fq_07_history_with_filters(client: AsyncClient) -> None:
    """GET /factor-quality/history?strategy_name=X&factor_name=Y&limit=5 → 参数传递正确。"""
    mock = AsyncMock()
    mock.get_history = AsyncMock(return_value=([_mock_ic_record()], 1))
    app.dependency_overrides[get_factor_monitor_service] = lambda: mock
    try:
        resp = await client.get(
            "/api/v1/factor-quality/history",
            params={"strategy_name": "TrendStrategy", "factor_name": "trend_score", "limit": 5},
            headers=_auth(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert len(data["items"]) == 1
        mock.get_history.assert_called_once_with(
            strategy_name="TrendStrategy", factor_name="trend_score", limit=5
        )
    finally:
        app.dependency_overrides.pop(get_factor_monitor_service, None)


# ---------------------------------------------------------------------------
# Phase 11 §9.2：GET /factor-quality/ic-history + /current-weights
# ---------------------------------------------------------------------------


async def test_fq_08_ic_history_no_auth(client: AsyncClient) -> None:
    """GET /factor-quality/ic-history 无鉴权 → 401。"""
    resp = await client.get("/api/v1/factor-quality/ic-history")
    assert resp.status_code == 401


async def test_fq_09_ic_history_empty_ok(client: AsyncClient) -> None:
    """GET /factor-quality/ic-history 鉴权后返回 200 + items 数组（空 DB → 空列表）。"""
    from quantpilot.core.database import get_db
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)
    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        resp = await client.get("/api/v1/factor-quality/ic-history", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert isinstance(body["data"]["items"], list)
        assert body["data"]["total"] == len(body["data"]["items"])
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_fq_10_current_weights_no_auth(client: AsyncClient) -> None:
    """GET /factor-quality/current-weights 无鉴权 → 401。"""
    resp = await client.get("/api/v1/factor-quality/current-weights")
    assert resp.status_code == 401


async def test_fq_11_current_weights_cold_start(client: AsyncClient) -> None:
    """冷启动（strategy_weights_history 空表）→ 200 + 12 行 default_matrix。"""
    from quantpilot.core.database import get_db
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)
    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        resp = await client.get("/api/v1/factor-quality/current-weights", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        items = body["data"]["items"]
        assert isinstance(items, list)
        # 3 state × 4 strategy = 12 行（冷启动 fallback 保证 12）
        assert len(items) == 12
        # 全部 default_matrix（无历史）
        sources = {it["weights_source"] for it in items}
        assert sources == {"default_matrix"}
        # 4 strategy 全部覆盖
        strategies = {it["strategy"] for it in items}
        assert strategies == {"trend", "momentum", "mean_reversion", "value"}
        # 3 state 全部覆盖
        states = {it["state"] for it in items}
        assert states == {"UPTREND", "DOWNTREND", "OSCILLATION"}
    finally:
        app.dependency_overrides.pop(get_db, None)
