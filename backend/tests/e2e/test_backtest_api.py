"""E2E 测试：回测引擎 /backtest（ASGI，Mock BacktestService）。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from quantpilot.api.deps import get_backtest_service
from quantpilot.core.security import create_token
from quantpilot.main import app
from quantpilot.models.system import BacktestTask


def _auth() -> dict:
    return {"Authorization": f"Bearer {create_token('access')}"}


def _mock_task(task_id: str = "test-uuid-1234", status: str = "PENDING") -> BacktestTask:
    t = MagicMock(spec=BacktestTask)
    t.task_id = task_id
    t.status = status
    t.started_at = None
    t.finished_at = None
    t.error_msg = None
    return t


_VALID_BODY = {
    "start_date": "2023-01-01",
    "end_date": "2023-12-31",
    "initial_capital": 1000000.0,
}


# ─── E2E-BT-01 ───

async def test_bt_01_run_no_auth(client: AsyncClient) -> None:
    """E2E-BT-01：POST /backtest/run 无鉴权 → 401。"""
    resp = await client.post("/api/v1/backtest/run", json=_VALID_BODY)
    assert resp.status_code == 401


# ─── E2E-BT-02 ───

async def test_bt_02_run_ok(client: AsyncClient) -> None:
    """E2E-BT-02：POST /backtest/run 有鉴权 + valid body → 200，data.task_id 非空。"""
    from quantpilot.api.deps import get_config_service
    from quantpilot.core.config_defaults import BacktestDefaultsConfig

    mock_svc = AsyncMock()
    mock_svc.create_task = AsyncMock(return_value="some-uuid-5678")

    # Phase 10 §4.4：端点依赖 ConfigService.get_backtest_defaults / get_all_for_snapshot
    mock_cfg = AsyncMock()
    mock_cfg.get_backtest_defaults = AsyncMock(return_value=BacktestDefaultsConfig())
    mock_cfg.get_all_for_snapshot = AsyncMock(return_value={})

    # Phase 10 §4.4 评审 C-02/C-03：路由前置检查只需 calendar，BacktestEngine 在后台任务即时构造
    mock_calendar = MagicMock()
    mock_calendar.get_trade_dates = MagicMock(return_value=["2023-01-03"])
    original_calendar = getattr(app.state, "calendar", None)
    app.state.calendar = mock_calendar

    app.dependency_overrides[get_backtest_service] = lambda: mock_svc
    app.dependency_overrides[get_config_service] = lambda: mock_cfg
    try:
        resp = await client.post("/api/v1/backtest/run", json=_VALID_BODY, headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["task_id"] == "some-uuid-5678"
        assert data["status"] == "PENDING"
    finally:
        app.dependency_overrides.pop(get_backtest_service, None)
        app.dependency_overrides.pop(get_config_service, None)
        app.state.calendar = original_calendar


# ─── E2E-BT-03 ───

async def test_bt_03_status_ok(client: AsyncClient) -> None:
    """E2E-BT-03：GET /backtest/{id}/status 有效 task_id → 200，data.status=PENDING。"""
    mock = AsyncMock()
    mock.get_task = AsyncMock(return_value=_mock_task())
    app.dependency_overrides[get_backtest_service] = lambda: mock
    # 确保 app.state._backtest_progress 存在（status 端点会读取它）
    if not hasattr(app.state, "_backtest_progress"):
        app.state._backtest_progress = {}
    try:
        resp = await client.get("/api/v1/backtest/test-uuid-1234/status", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "PENDING"
        assert data["task_id"] == "test-uuid-1234"
        assert "progress_pct" in data
    finally:
        app.dependency_overrides.pop(get_backtest_service, None)


# ─── E2E-BT-04 ───

async def test_bt_04_status_not_found(client: AsyncClient) -> None:
    """E2E-BT-04：GET /backtest/{id}/status 无效 task_id → 404。"""
    mock = AsyncMock()
    mock.get_task = AsyncMock(return_value=None)
    app.dependency_overrides[get_backtest_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/backtest/nonexistent-uuid/status", headers=_auth())
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_backtest_service, None)


# ─── E2E-BT-05 ───

async def test_bt_05_result_pending(client: AsyncClient) -> None:
    """E2E-BT-05：GET /backtest/{id}/result PENDING 状态 → 409。"""
    mock = AsyncMock()
    mock.get_task = AsyncMock(return_value=_mock_task(status="PENDING"))
    app.dependency_overrides[get_backtest_service] = lambda: mock
    try:
        resp = await client.get("/api/v1/backtest/test-uuid-1234/result", headers=_auth())
        assert resp.status_code == 409
    finally:
        app.dependency_overrides.pop(get_backtest_service, None)


# ─── E2E-BT-06 ───

async def test_bt_06_run_missing_start_date(client: AsyncClient) -> None:
    """E2E-BT-06：POST /backtest/run body 缺 start_date → 422。"""
    resp = await client.post(
        "/api/v1/backtest/run",
        json={"end_date": "2023-12-31", "initial_capital": 1000000.0},
        headers=_auth(),
    )
    assert resp.status_code == 422


# ─── E2E-BT-07 ─── Phase 10 §4.4 partial-overlay + config_snapshot

async def test_bt_07_run_partial_overlay_uses_defaults(client: AsyncClient) -> None:
    """E2E-BT-07：body 未提供成本率 → 从 backtest_defaults 填充；snapshot 传入 create_task。"""
    from quantpilot.api.deps import get_config_service
    from quantpilot.core.config_defaults import BacktestDefaultsConfig

    mock_svc = AsyncMock()
    mock_svc.create_task = AsyncMock(return_value="cfg-uuid-001")

    mock_cfg = AsyncMock()
    mock_cfg.get_backtest_defaults = AsyncMock(
        return_value=BacktestDefaultsConfig(
            commission_rate=0.0005, stamp_tax_rate=0.001, slippage_rate=0.002,
        )
    )
    mock_cfg.get_all_for_snapshot = AsyncMock(return_value={"_snapshot_at": "x"})

    mock_calendar = MagicMock()
    mock_calendar.get_trade_dates = MagicMock(return_value=["2023-01-03"])
    original_calendar = getattr(app.state, "calendar", None)
    app.state.calendar = mock_calendar

    app.dependency_overrides[get_backtest_service] = lambda: mock_svc
    app.dependency_overrides[get_config_service] = lambda: mock_cfg
    try:
        # body 不包含任何 *_rate 字段 → 端点层用 defaults 覆盖
        resp = await client.post(
            "/api/v1/backtest/run",
            json={
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
                "initial_capital": 1000000.0,
            },
            headers=_auth(),
        )
        assert resp.status_code == 200
        mock_cfg.get_backtest_defaults.assert_awaited_once()
        mock_cfg.get_all_for_snapshot.assert_awaited_once()

        # create_task 应接收到从 defaults 填充后的 config + engine_snapshot
        call = mock_svc.create_task.await_args
        config_arg = call.args[0]
        assert config_arg.commission_rate == pytest.approx(0.0005)
        assert config_arg.stamp_tax_rate == pytest.approx(0.001)
        assert config_arg.slippage_rate == pytest.approx(0.002)
        # engine_snapshot 以关键字或第二位置参数传入
        snapshot = call.kwargs.get("engine_snapshot") or (
            call.args[1] if len(call.args) > 1 else None
        )
        assert snapshot == {"_snapshot_at": "x"}
    finally:
        app.dependency_overrides.pop(get_backtest_service, None)
        app.dependency_overrides.pop(get_config_service, None)
        app.state.calendar = original_calendar


# ─── E2E-BT-08 ─── 部分字段提供则只覆盖未提供字段

async def test_bt_08_run_partial_overlay_mixed(client: AsyncClient) -> None:
    """E2E-BT-08：body 显式指定 commission_rate，其余走 defaults。"""
    from quantpilot.api.deps import get_config_service
    from quantpilot.core.config_defaults import BacktestDefaultsConfig

    mock_svc = AsyncMock()
    mock_svc.create_task = AsyncMock(return_value="cfg-uuid-002")

    mock_cfg = AsyncMock()
    mock_cfg.get_backtest_defaults = AsyncMock(
        return_value=BacktestDefaultsConfig(
            commission_rate=0.0005, stamp_tax_rate=0.001, slippage_rate=0.002,
        )
    )
    mock_cfg.get_all_for_snapshot = AsyncMock(return_value={})

    mock_calendar = MagicMock()
    mock_calendar.get_trade_dates = MagicMock(return_value=["2023-01-03"])
    original_calendar = getattr(app.state, "calendar", None)
    app.state.calendar = mock_calendar

    app.dependency_overrides[get_backtest_service] = lambda: mock_svc
    app.dependency_overrides[get_config_service] = lambda: mock_cfg
    try:
        resp = await client.post(
            "/api/v1/backtest/run",
            json={
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
                "initial_capital": 1000000.0,
                "commission_rate": 0.0001,  # 显式覆盖
            },
            headers=_auth(),
        )
        assert resp.status_code == 200
        config_arg = mock_svc.create_task.await_args.args[0]
        # 显式 0.0001 不被 defaults 覆盖
        assert config_arg.commission_rate == pytest.approx(0.0001)
        # 其余走 defaults
        assert config_arg.stamp_tax_rate == pytest.approx(0.001)
        assert config_arg.slippage_rate == pytest.approx(0.002)
    finally:
        app.dependency_overrides.pop(get_backtest_service, None)
        app.dependency_overrides.pop(get_config_service, None)
        app.state.calendar = original_calendar
