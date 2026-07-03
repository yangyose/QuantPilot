"""SAPI-01~06: 信号 API E2E 测试（ASGI，Mock SignalService）。"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

from httpx import AsyncClient

from quantpilot.api.deps import (
    get_lineage_service,
    get_signal_service,
    get_signal_view_service,
)
from quantpilot.core.security import create_token
from quantpilot.main import app
from quantpilot.models.business import Signal


def _auth_header() -> dict:
    return {"Authorization": f"Bearer {create_token('access', '1')}"}


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
    # Phase 11 §9.1：Signal 表新列在 mock 场景显式 None（spec=Signal 已含这些属性，
    # 默认 MagicMock 会让 pydantic 校验失败）
    s.composite_z = None
    s.composite_pct_in_market = None
    s.weights_source = None
    s.trigger_reason = None
    return s


# ---------------------------------------------------------------------------
# SAPI-01: GET /signals（mock 返回空列表）
# ---------------------------------------------------------------------------
async def test_sapi_01_get_signals_empty(client: AsyncClient) -> None:
    """SAPI-01: GET /signals（无 trade_date，库中无信号）→ 200, signals=[], trade_date=None。

    缺省走 get_latest_signals（最新可用信号），无任何信号时返回 ([], None)。
    """
    mock_service = AsyncMock()
    mock_service.get_latest_signals = AsyncMock(return_value=([], None))

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
    assert body["data"]["trade_date"] is None


# ---------------------------------------------------------------------------
# SAPI-02: GET /signals（mock 返回 2 条信号）
# ---------------------------------------------------------------------------
async def test_sapi_02_get_signals_with_data(client: AsyncClient) -> None:
    """SAPI-02: GET /signals（无 trade_date）→ 200，返回最新可用信号 + 解析出的 trade_date。"""
    sigs = [_mock_signal(1, "000001.SZ"), _mock_signal(2, "000002.SZ")]
    mock_service = AsyncMock()
    mock_service.get_latest_signals = AsyncMock(return_value=(sigs, date(2026, 4, 8)))

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
    # 缺省返回最新有信号交易日，trade_date 反映真实信号日期（非字面今天）
    assert data["trade_date"] == "2026-04-08"
    item = data["signals"][0]
    assert "id" in item
    assert "ts_code" in item
    assert "signal_type" in item
    assert "score" in item
    assert "status" in item


async def test_sapi_07_get_signals_explicit_date(client: AsyncClient) -> None:
    """SAPI-07: GET /signals?trade_date=YYYY-MM-DD → 走指定日期路径（get_today_signals）。"""
    sigs = [_mock_signal(1, "000001.SZ", trade_date=date(2026, 4, 8))]
    mock_service = AsyncMock()
    mock_service.get_today_signals = AsyncMock(return_value=sigs)
    # 显式日期不应触发 latest 回退
    mock_service.get_latest_signals = AsyncMock(return_value=([], None))

    app.dependency_overrides[get_signal_service] = lambda: mock_service
    try:
        resp = await client.get(
            "/api/v1/signals",
            params={"trade_date": "2026-04-08"},
            headers=_auth_header(),
        )
    finally:
        app.dependency_overrides.pop(get_signal_service, None)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["trade_date"] == "2026-04-08"
    assert data["total"] == 1
    mock_service.get_today_signals.assert_awaited_once()
    mock_service.get_latest_signals.assert_not_awaited()


# ---------------------------------------------------------------------------
# SAPI-08: GET /signals 经 SignalViewService 按账户叠加 is_holding + suggested_pct
# （V1.5-G G-4d-2 §2 派生语义：管线产共享信号，API 期按当前账户叠加账户维度视图）
# ---------------------------------------------------------------------------
async def test_sapi_08_get_signals_account_overlay(client: AsyncClient) -> None:
    """SAPI-08: GET /signals 组装期调用 SignalViewService.apply_account_overlay，
    响应 dict 携带按账户叠加的 is_holding + suggested_pct。"""
    sigs = [_mock_signal(1, "000001.SZ"), _mock_signal(2, "000002.SZ")]
    mock_service = AsyncMock()
    mock_service.get_latest_signals = AsyncMock(return_value=(sigs, date(2026, 4, 8)))

    async def _fake_overlay(signal_dicts: list, account_id: int) -> None:
        # 模拟：持仓命中 000001.SZ，为其标 is_holding；BUY 叠加 suggested_pct
        for d in signal_dicts:
            d["is_holding"] = d["ts_code"] == "000001.SZ"
            if d["signal_type"] == "BUY":
                d["suggested_pct"] = 0.08

    mock_view = MagicMock()
    mock_view.apply_account_overlay = AsyncMock(side_effect=_fake_overlay)

    app.dependency_overrides[get_signal_service] = lambda: mock_service
    app.dependency_overrides[get_signal_view_service] = lambda: mock_view
    try:
        resp = await client.get("/api/v1/signals", headers=_auth_header())
    finally:
        app.dependency_overrides.pop(get_signal_service, None)
        app.dependency_overrides.pop(get_signal_view_service, None)

    assert resp.status_code == 200
    data = resp.json()["data"]
    by_code = {s["ts_code"]: s for s in data["signals"]}
    assert by_code["000001.SZ"]["is_holding"] is True
    assert by_code["000002.SZ"]["is_holding"] is False
    assert by_code["000001.SZ"]["suggested_pct"] == 0.08
    mock_view.apply_account_overlay.assert_awaited_once()


# ---------------------------------------------------------------------------
# SAPI-09: GET /signals 无 token → 401（守卫在 get_current_user_id）
# ---------------------------------------------------------------------------
async def test_sapi_09_get_signals_requires_auth(client: AsyncClient) -> None:
    """SAPI-09: GET /signals 无 Authorization 头 → 401。"""
    resp = await client.get("/api/v1/signals")
    assert resp.status_code == 401


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
# E2E-P12-A-01: GET /signals/{id}/lineage 返回三层 19 字段（Phase 12 §6.3）
# ---------------------------------------------------------------------------
async def test_e2e_p12_a_01_lineage_full_19_fields(client: AsyncClient) -> None:
    """E2E-P12-A-01: SignalLineageResponse 19 字段齐全（含 L3 factor_orthogonal）。"""
    lineage_data = {
        "signal_id": 12345,
        "trade_date": "2026-05-12",
        "score_snapshot": {
            "ts_code": "600519.SH",
            "composite_score": 99.87,
            "composite_z": 3.85,
            "composite_pct_in_market": 0.0005,
            "market_state": "UPTREND",
            "trigger_reason": "pct_below_buy",
            "trend_score": 1.85,
            "momentum_score": 0.94,
            "reversion_score": -0.21,
            "value_score": 1.12,
            "weights_source": "default_matrix",
            "hysteresis_status": "active",
            "score_breakdown": {"trend": {"score": 1.85}},
            "factor_winsorized": {"trend": {"ma_diff": 0.85}},
            "factor_neutralized": {"trend": {"ma_diff": 0.72}},
            "raw_factors": {"ma_diff": 0.92},
            "factor_orthogonal": {"trend": {"ma_diff_normalized": 0.65}},
            "score_breakdown_raw": {"trend": {"z_raw": 1.85}},
            "score_breakdown_residual": {"trend": {"z_orthogonal_normalized": 0.65}},
        },
        "pipeline_run": {
            "trade_date": "2026-05-12",
            "cp1_at": "2026-05-12T15:30:00+08:00",
            "cp2_at": "2026-05-12T15:35:12+08:00",
            "cp3_at": "2026-05-12T15:37:48+08:00",
            "data_snapshot_version": "abc12345",
        },
    }
    mock_lineage = AsyncMock()
    mock_lineage.get_signal_lineage = AsyncMock(return_value=lineage_data)

    app.dependency_overrides[get_lineage_service] = lambda: mock_lineage
    try:
        resp = await client.get("/api/v1/signals/12345/lineage", headers=_auth_header())
    finally:
        app.dependency_overrides.pop(get_lineage_service, None)

    assert resp.status_code == 200
    data = resp.json()["data"]
    snap = data["score_snapshot"]
    # 19 字段齐全（设计文档 §3.1.3：标识 1 + L1 5 + L2 9 + L3 4）
    expected_fields = {
        "ts_code",
        "composite_score", "composite_z", "composite_pct_in_market",
        "market_state", "trigger_reason",
        "trend_score", "momentum_score", "reversion_score", "value_score",
        "weights_source", "hysteresis_status",
        "score_breakdown", "factor_winsorized", "factor_neutralized",
        "raw_factors", "factor_orthogonal",
        "score_breakdown_raw", "score_breakdown_residual",
    }
    assert set(snap.keys()) == expected_fields
    assert snap["factor_orthogonal"] == {"trend": {"ma_diff_normalized": 0.65}}
    assert data["pipeline_run"]["data_snapshot_version"] == "abc12345"


# ---------------------------------------------------------------------------
# E2E-P12-A-02: GET /signals/{id}/lineage 信号不存在 → 404
# ---------------------------------------------------------------------------
async def test_e2e_p12_a_02_lineage_not_found(client: AsyncClient) -> None:
    """E2E-P12-A-02: 信号 ID 不存在 → 404。"""
    mock_lineage = AsyncMock()
    mock_lineage.get_signal_lineage = AsyncMock(return_value=None)

    app.dependency_overrides[get_lineage_service] = lambda: mock_lineage
    try:
        resp = await client.get("/api/v1/signals/999999/lineage", headers=_auth_header())
    finally:
        app.dependency_overrides.pop(get_lineage_service, None)

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# E2E-P12-A-03: GET /signals/{id}/lineage 非法 ID → 422
# ---------------------------------------------------------------------------
async def test_e2e_p12_a_03_lineage_invalid_id(client: AsyncClient) -> None:
    """E2E-P12-A-03: signal_id 非整型 → 422（FastAPI 路径参数校验）。"""
    resp = await client.get("/api/v1/signals/abc/lineage", headers=_auth_header())
    assert resp.status_code == 422


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
