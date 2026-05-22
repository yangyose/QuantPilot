"""Phase 13 §3.7.2: DailyPipeline._publish_progress 单元测试。

UT-P13-D-03a: redis=None 时降级 logger.debug，不抛异常
UT-P13-D-03b: redis 存在时调 publish；publish 抛异常被吞掉
UT-P13-D-03c: payload 含 trade_date / step / status / progress_pct 4 字段
"""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from quantpilot.pipeline.daily_pipeline import DailyPipeline


def _make_pipeline(redis=None) -> DailyPipeline:
    return DailyPipeline(
        session_factory=SimpleNamespace(),
        adapter=SimpleNamespace(),
        validator=SimpleNamespace(),
        calendar=SimpleNamespace(),
        redis=redis,
    )


async def test_ut_p13_d_03a_redis_none_does_not_raise() -> None:
    pipeline = _make_pipeline(redis=None)
    await pipeline._publish_progress(
        date(2026, 5, 22), "CP1", "started", 5,
    )


async def test_ut_p13_d_03b_redis_publish_exception_swallowed() -> None:
    redis = SimpleNamespace()
    redis.publish = AsyncMock(side_effect=ConnectionError("redis down"))
    pipeline = _make_pipeline(redis=redis)
    await pipeline._publish_progress(date(2026, 5, 22), "CP2", "completed", 50)
    redis.publish.assert_awaited_once()


async def test_ut_p13_d_03c_payload_structure() -> None:
    captured = {}
    redis = SimpleNamespace()

    async def _publish(channel, msg):
        captured["channel"] = channel
        captured["msg"] = msg

    redis.publish = _publish
    pipeline = _make_pipeline(redis=redis)
    await pipeline._publish_progress(date(2026, 5, 22), "CP3", "started", 55)
    assert captured["channel"] == "quantpilot:pipeline:progress"
    payload = json.loads(captured["msg"])
    assert payload == {
        "trade_date": "2026-05-22",
        "step": "CP3",
        "status": "started",
        "progress_pct": 55,
    }
