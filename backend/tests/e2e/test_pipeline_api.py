"""E2E 测试：流水线 /pipeline（ASGI，Mock DB/Calendar）。"""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from httpx import AsyncClient

from quantpilot.api.deps import get_db
from quantpilot.core.security import create_token
from quantpilot.main import app


def _auth() -> dict:
    return {"Authorization": f"Bearer {create_token('access', '1')}"}


def _mock_run(
    trade_date: date = date(2026, 4, 10),
    status: str = "SUCCESS",
) -> MagicMock:
    r = MagicMock()
    r.id = 1
    r.trade_date = trade_date
    r.status = status
    r.started_at = datetime(2026, 4, 10, 9, 30, tzinfo=timezone.utc)
    r.finished_at = datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc)
    r.signal_count = 5
    r.cp1_data_ready = True
    r.cp1_at = datetime(2026, 4, 10, 9, 35, tzinfo=timezone.utc)
    r.data_snapshot_version = "20260410T093000Z"
    r.cp2_scoring_done = True
    r.cp2_at = datetime(2026, 4, 10, 9, 45, tzinfo=timezone.utc)
    r.cp3_signals_done = True
    r.cp3_at = datetime(2026, 4, 10, 9, 55, tzinfo=timezone.utc)
    r.error_msg = None
    return r


# ---------------------------------------------------------------------------
# GET /pipeline/status
# ---------------------------------------------------------------------------

async def test_pl_01_status_no_auth(client: AsyncClient) -> None:
    """GET /pipeline/status 无鉴权 → 401。"""
    resp = await client.get("/api/v1/pipeline/status")
    assert resp.status_code == 401


async def test_pl_02_status_no_data(client: AsyncClient) -> None:
    """GET /pipeline/status 有鉴权，无记录 → 200，data=null。"""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        resp = await client.get("/api/v1/pipeline/status", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["data"] is None
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_pl_03_status_with_data(client: AsyncClient) -> None:
    """GET /pipeline/status 有鉴权，有记录 → 200，含结构化字段。"""
    run = _mock_run()
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: run))

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        resp = await client.get("/api/v1/pipeline/status", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data is not None
        assert data["trade_date"] == "2026-04-10"
        assert data["status"] == "SUCCESS"
        assert data["cp1_data_ready"] is True
        assert data["signal_count"] == 5
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# POST /pipeline/trigger
# ---------------------------------------------------------------------------

async def test_pl_04_trigger_no_auth(client: AsyncClient) -> None:
    """POST /pipeline/trigger 无鉴权 → 401。"""
    resp = await client.post(
        "/api/v1/pipeline/trigger",
        json={"trade_date": "2026-04-10"},
    )
    assert resp.status_code == 401


async def test_pl_05_trigger_non_trade_day(client: AsyncClient) -> None:
    """POST /pipeline/trigger 非交易日 → 400。"""
    # Mock calendar（is_trade_date → False）
    mock_calendar = MagicMock()
    mock_calendar.is_trade_date = MagicMock(return_value=False)
    app.state.calendar = mock_calendar

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        resp = await client.post(
            "/api/v1/pipeline/trigger",
            json={"trade_date": "2026-04-12"},  # 周日（非交易日）
            headers=_auth(),
        )
        assert resp.status_code == 400
        assert "非交易日" in resp.json()["msg"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.calendar = None


async def test_pl_06_trigger_ok(client: AsyncClient) -> None:
    """POST /pipeline/trigger 交易日 → 200，返回 PipelineRunItem。"""
    mock_calendar = MagicMock()
    mock_calendar.is_trade_date = MagicMock(return_value=True)
    app.state.calendar = mock_calendar

    run = _mock_run(status="RUNNING")
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: run))
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        resp = await client.post(
            "/api/v1/pipeline/trigger",
            json={"trade_date": "2026-04-10"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["trade_date"] == "2026-04-10"
        assert "cp1_data_ready" in data
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.calendar = None


async def test_pl_07_trigger_injects_redis_for_progress_publish(
    client: AsyncClient, monkeypatch,
) -> None:
    """R13-P0-1 回归：POST /pipeline/trigger 构造 DailyPipeline 时必须把
    app.state.redis 传入，否则 _publish_progress 永远走 logger.debug 降级
    （前端 PipelineProgressCard 对手动触发场景无进度推送）。

    本测试拦截 DailyPipeline 构造函数，断言 redis kwarg 被注入。
    """
    mock_calendar = MagicMock()
    mock_calendar.is_trade_date = MagicMock(return_value=True)
    app.state.calendar = mock_calendar

    sentinel_redis = MagicMock(name="redis_sentinel")
    sentinel_adapter = MagicMock(name="adapter_sentinel")
    sentinel_wx = MagicMock(name="wxpusher_sentinel")
    original_redis = getattr(app.state, "redis", None)
    original_adapter = getattr(app.state, "adapter", None)
    original_wx = getattr(app.state, "wxpusher", None)
    app.state.redis = sentinel_redis
    app.state.adapter = sentinel_adapter
    app.state.wxpusher = sentinel_wx

    captured = {}

    class _FakePipeline:
        def __init__(self, *args, **kwargs):
            captured["kwargs"] = kwargs

        async def run(self, trade_date):
            return None

    monkeypatch.setattr(
        "quantpilot.pipeline.daily_pipeline.DailyPipeline", _FakePipeline,
    )

    run = _mock_run(status="RUNNING")
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: run))
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()

    app.dependency_overrides[get_db] = lambda: mock_session
    try:
        resp = await client.post(
            "/api/v1/pipeline/trigger",
            json={"trade_date": "2026-04-10"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        # Background task 在 ASGITransport 内同步执行，captured["kwargs"] 必然有值
        assert "kwargs" in captured, "DailyPipeline 构造函数未被调用"
        assert captured["kwargs"].get("redis") is sentinel_redis, (
            "POST /pipeline/trigger 必须把 app.state.redis 注入 DailyPipeline"
        )
        assert captured["kwargs"].get("notification_channel") is sentinel_wx, (
            "POST /pipeline/trigger 必须把 app.state.wxpusher 注入 DailyPipeline"
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.state.calendar = None
        app.state.redis = original_redis
        app.state.adapter = original_adapter
        app.state.wxpusher = original_wx
