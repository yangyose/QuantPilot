"""unit/test_wxpusher_adapter.py: Phase 10 WxPusherAdapter 单元测试。

覆盖：
- 未配置（token/uid 缺失）→ send() 直接 False
- 首次 200/code=1000 → True，无重试
- 第一次失败、第二次成功 → True，间隔 sleep 一次
- 三次全失败（HTTP 非 200 / code != 1000 / 网络异常）→ False，sleep 两次
"""
from __future__ import annotations

from typing import Any

import pytest

from quantpilot.notification.wxpusher import WxPusherAdapter


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._json = json_body

    def json(self) -> dict[str, Any]:
        return self._json


class _FakeAsyncClient:
    """模拟 httpx.AsyncClient：按预设的 responses 列表依次返回，可 raise 异常。"""

    def __init__(self, responses: list[_FakeResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
        self.calls.append({"url": url, "json": json})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """避免实际 sleep 30s。"""
    async def _instant_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("quantpilot.notification.wxpusher.asyncio.sleep", _instant_sleep)


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[_FakeResponse | Exception],
) -> _FakeAsyncClient:
    fake = _FakeAsyncClient(responses)

    def _factory(*args: object, **kwargs: object) -> _FakeAsyncClient:
        return fake

    monkeypatch.setattr("quantpilot.notification.wxpusher.httpx.AsyncClient", _factory)
    return fake


class TestWxPusherUnconfigured:
    async def test_missing_token_returns_false(self) -> None:
        adapter = WxPusherAdapter(app_token="", uid="UID_x")
        assert adapter.configured is False
        ok = await adapter.send("title", "body")
        assert ok is False

    async def test_missing_uid_returns_false(self) -> None:
        adapter = WxPusherAdapter(app_token="AT_x", uid="")
        assert adapter.configured is False
        ok = await adapter.send("title", "body")
        assert ok is False


class TestWxPusherSuccess:
    async def test_first_attempt_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(
            monkeypatch,
            [_FakeResponse(200, {"code": 1000, "msg": "处理成功"})],
        )
        adapter = WxPusherAdapter(app_token="AT_x", uid="UID_x")
        ok = await adapter.send("买入信号", "评分 85 / 100")
        assert ok is True
        assert len(fake.calls) == 1
        sent = fake.calls[0]["json"]
        assert sent["appToken"] == "AT_x"
        assert sent["uids"] == ["UID_x"]
        assert sent["content"] == "评分 85 / 100"
        assert sent["summary"] == "买入信号"  # ≤20 字符不截断

    async def test_second_attempt_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(
            monkeypatch,
            [
                _FakeResponse(500, {}),
                _FakeResponse(200, {"code": 1000}),
            ],
        )
        adapter = WxPusherAdapter(app_token="AT_x", uid="UID_x")
        ok = await adapter.send("t", "b")
        assert ok is True
        assert len(fake.calls) == 2

    async def test_summary_truncation_to_20_chars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _patch_client(
            monkeypatch,
            [_FakeResponse(200, {"code": 1000})],
        )
        adapter = WxPusherAdapter(app_token="AT_x", uid="UID_x")
        long_title = "x" * 50
        ok = await adapter.send(long_title, "b")
        assert ok is True
        assert fake.calls[0]["json"]["summary"] == "x" * 20


class TestWxPusherFailure:
    async def test_all_attempts_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(
            monkeypatch,
            [_FakeResponse(500, {}) for _ in range(3)],
        )
        adapter = WxPusherAdapter(app_token="AT_x", uid="UID_x")
        ok = await adapter.send("t", "b")
        assert ok is False
        assert len(fake.calls) == 3

    async def test_all_attempts_code_not_1000(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _patch_client(
            monkeypatch,
            [_FakeResponse(200, {"code": 1001, "msg": "鉴权失败"}) for _ in range(3)],
        )
        adapter = WxPusherAdapter(app_token="AT_x", uid="UID_x")
        ok = await adapter.send("t", "b")
        assert ok is False
        assert len(fake.calls) == 3

    async def test_network_exception_then_recovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _patch_client(
            monkeypatch,
            [
                ConnectionError("dns failure"),
                _FakeResponse(200, {"code": 1000}),
            ],
        )
        adapter = WxPusherAdapter(app_token="AT_x", uid="UID_x")
        ok = await adapter.send("t", "b")
        assert ok is True
        assert len(fake.calls) == 2

    async def test_all_network_exceptions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _patch_client(
            monkeypatch,
            [ConnectionError("err") for _ in range(3)],
        )
        adapter = WxPusherAdapter(app_token="AT_x", uid="UID_x")
        ok = await adapter.send("t", "b")
        assert ok is False
        assert len(fake.calls) == 3
