"""E2E-P13-D-01: Phase 13 WS /pipeline/progress 端点测试。

测试场景：
- redis=None 时连接后立即收到 error 帧并关闭（降级路径）
- redis 可用 + publish 一条消息后客户端能收到（mock 渠道）

依据 docs/design/phases/phase13_production_observability.md §3.7.1 + §6.2。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from starlette.testclient import TestClient

from quantpilot.main import app


def test_e2e_p13_d_01a_ws_redis_none_returns_error() -> None:
    """redis=None → 客户端收到 error 帧后连接关闭。

    Phase 14 §14-7 R13-P2-5：error 帧统一为 REST 格式 {code, data, msg}。
    """
    with TestClient(app) as tc:
        original_redis = getattr(app.state, "redis", None)
        app.state.redis = None
        try:
            with tc.websocket_connect("/api/v1/pipeline/progress") as ws:
                payload = ws.receive_json()
                # Phase 14 §14-7 R13-P2-5：统一 {code, data, msg} schema
                assert payload.get("code") == 503
                assert payload.get("data") is None
                assert "Redis" in payload.get("msg", "")
        finally:
            app.state.redis = original_redis


def test_e2e_p13_d_01b_ws_redis_subscribe_streams_message() -> None:
    """redis pubsub 可用 → 推一条消息能被客户端 receive。"""

    pubsub = SimpleNamespace()
    queue: asyncio.Queue = asyncio.Queue()

    async def _subscribe(channel):
        return None

    async def _unsubscribe(channel):
        return None

    async def _aclose():
        return None

    async def _listen():
        # 首条 system message 通常 type=subscribe，模拟一条 message
        await queue.put({
            "type": "message",
            "data": json.dumps({
                "trade_date": "2026-05-22", "step": "CP1",
                "status": "started", "progress_pct": 5,
            }),
        })
        while True:
            msg = await queue.get()
            yield msg

    pubsub.subscribe = _subscribe
    pubsub.unsubscribe = _unsubscribe
    pubsub.aclose = _aclose
    pubsub.listen = _listen

    fake_redis = SimpleNamespace()
    fake_redis.pubsub = lambda: pubsub

    with TestClient(app) as tc:
        original_redis = getattr(app.state, "redis", None)
        app.state.redis = fake_redis
        try:
            with tc.websocket_connect("/api/v1/pipeline/progress") as ws:
                msg = ws.receive_text()
                payload = json.loads(msg)
                assert payload["step"] == "CP1"
                assert payload["progress_pct"] == 5
        finally:
            app.state.redis = original_redis
