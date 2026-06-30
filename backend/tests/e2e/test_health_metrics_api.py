"""E2E-P13-A-01~03 + E2E-P13-B-01: Phase 13 /health/* + /metrics 端点。

依据 docs/design/phases/phase13_production_observability.md §6.3：
- E2E-P13-A-01: GET /api/v1/health/scheduler 无鉴权 → 401
- E2E-P13-A-02: GET /api/v1/health/scheduler 有鉴权 → 200 + jobs 列表结构
- E2E-P13-A-03: GET /api/v1/health/data 鉴权 + 返回结构
- E2E-P13-B-01: GET /metrics 无鉴权 → 200 text/plain，含 quantpilot_pipeline_runs_total
"""
from __future__ import annotations

from httpx import AsyncClient

from quantpilot.core.security import create_token


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_token('access', '1')}"}


async def test_e2e_p13_a_01_health_scheduler_no_auth(client: AsyncClient) -> None:
    """E2E-P13-A-01: 无鉴权 → 401。"""
    resp = await client.get("/api/v1/health/scheduler")
    assert resp.status_code == 401


async def test_e2e_p13_a_02_health_scheduler_with_auth(client: AsyncClient) -> None:
    """E2E-P13-A-02: 鉴权 → 200，返回结构含 running/jobs/total_jobs。

    测试环境 lifespan 不启动 scheduler（无 TUSHARE_TOKEN），健康端点应优雅返回
    running=False + 空 jobs 而非 500。
    """
    resp = await client.get("/api/v1/health/scheduler", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert "running" in data
    assert "jobs" in data
    assert "total_jobs" in data
    assert isinstance(data["jobs"], list)


async def test_e2e_p13_a_03_health_data_with_auth(client: AsyncClient) -> None:
    """E2E-P13-A-03: 鉴权 → 200，返回 data_latency_days + recent_violations + window_days。

    E2E 不接真实 DB，用 dependency_overrides 注入 fake session 让
    `select(func.max(model.trade_date))` 返回 None（无数据），latency 表示为 -1。
    """
    from unittest.mock import AsyncMock

    from quantpilot.api.deps import get_db
    from quantpilot.main import app

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    fake_session = AsyncMock()
    fake_session.execute = AsyncMock(return_value=_FakeResult())

    async def _override_get_db():
        yield fake_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        resp = await client.get("/api/v1/health/data", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert "data_latency_days" in data
        assert "recent_violations" in data
        assert data["window_days"] == 30
        assert isinstance(data["data_latency_days"], dict)
        # mock 下各表 max(trade_date)=None → latency=-1
        assert data["data_latency_days"]["daily_quote"] == -1
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_e2e_p13_b_01_metrics_no_auth_text_plain(client: AsyncClient) -> None:
    """E2E-P13-B-01: GET /metrics 无鉴权 → 200 text/plain，含核心 Counter 名。"""
    # 先触发一次 Counter 以确保 metrics 输出非空
    from quantpilot.core.metrics import PIPELINE_RUNS
    PIPELINE_RUNS.labels(status="success").inc()

    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    text = resp.text
    assert "quantpilot_pipeline_runs_total" in text
    assert "# HELP " in text
    assert "# TYPE " in text


