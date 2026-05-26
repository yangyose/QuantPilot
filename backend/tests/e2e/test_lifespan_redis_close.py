"""E2E-P14-7-06: Phase 14 §14-7 R13-P2-6 lifespan shutdown 必须 redis.aclose()。

防止多 worker 启停 + hot reload 下 Redis connection 泄漏。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from quantpilot.core.config import settings
from quantpilot.main import app


@pytest.fixture
def _no_tushare_lifespan(monkeypatch):
    """禁用 lifespan 中的 Tushare/Scheduler 启动路径，避免泄漏 APScheduler 后台
    job 到后续 e2e 测试（test_pl_06 等用 ASGITransport 不重启 lifespan，
    leftover 的 scheduler job 会干扰其 BG task 的 DB 连接）。"""
    monkeypatch.setattr(settings, "tushare_token", "")
    yield


def test_e2e_p14_7_06_lifespan_calls_redis_aclose_on_shutdown(
    _no_tushare_lifespan,
) -> None:
    """TestClient ctx 退出 → lifespan finally 段必须 await app.state.redis.aclose()。"""
    fake_redis = AsyncMock()
    fake_redis.aclose = AsyncMock()

    with TestClient(app) as tc:
        # lifespan 进入 yield 后替换 redis 为 mock；exit 时走 mock 的 aclose
        app.state.redis = fake_redis
        # 触发一次任意请求保证 ctx 已 enter
        tc.get("/api/v1/health/scheduler")

    fake_redis.aclose.assert_awaited_once()


def test_e2e_p14_7_06b_lifespan_redis_aclose_failure_does_not_block_shutdown(
    _no_tushare_lifespan,
) -> None:
    """redis.aclose 抛异常时 lifespan 不应阻断关闭（best-effort + warn）。"""
    fake_redis = AsyncMock()
    fake_redis.aclose = AsyncMock(side_effect=RuntimeError("boom"))

    # 退出 TestClient ctx 不应抛异常
    with TestClient(app) as tc:
        app.state.redis = fake_redis
        tc.get("/api/v1/health/scheduler")

    fake_redis.aclose.assert_awaited_once()
