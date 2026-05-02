"""unit/test_setup_service.py: Phase 10 SetupService 单元测试。

覆盖：
- 初次查询（行不存在）→ completed=False
- value 为 NULL 空串 → completed=False
- JSON 解析失败 → completed=False + WARN
- mark_completed → JSON 序列化正确
"""
from __future__ import annotations

from typing import Any

import pytest

from quantpilot.models.system import SystemConfig
from quantpilot.services.setup_service import SETUP_KEY, SetupService


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    def __init__(self) -> None:
        self._execute_results: list[Any] = []
        self.executed_stmts: list[Any] = []
        self.flush_count = 0

    def queue(self, value: Any) -> None:
        self._execute_results.append(value)

    async def execute(self, stmt: Any) -> _FakeResult:
        self.executed_stmts.append(stmt)
        if self._execute_results:
            return _FakeResult(self._execute_results.pop(0))
        return _FakeResult(None)

    async def flush(self) -> None:
        self.flush_count += 1


def _make_row(value: str | None) -> SystemConfig:
    row = SystemConfig(key=SETUP_KEY, value=value)
    return row


async def test_get_status_missing_row() -> None:
    session = _FakeSession()
    svc = SetupService(session=session)  # type: ignore[arg-type]
    result = await svc.get_status()
    assert result == {"completed": False, "completed_at": None}


async def test_get_status_empty_value() -> None:
    session = _FakeSession()
    session.queue(_make_row(""))
    svc = SetupService(session=session)  # type: ignore[arg-type]
    result = await svc.get_status()
    assert result == {"completed": False, "completed_at": None}


async def test_get_status_invalid_json(caplog: pytest.LogCaptureFixture) -> None:
    session = _FakeSession()
    session.queue(_make_row("not-json"))
    svc = SetupService(session=session)  # type: ignore[arg-type]
    result = await svc.get_status()
    assert result == {"completed": False, "completed_at": None}
    assert any("setup_status_invalid_json" in r.message for r in caplog.records)


async def test_get_status_valid_completed() -> None:
    session = _FakeSession()
    session.queue(
        _make_row('{"completed": true, "completed_at": "2026-04-22T10:00:00+00:00"}')
    )
    svc = SetupService(session=session)  # type: ignore[arg-type]
    result = await svc.get_status()
    assert result == {
        "completed": True,
        "completed_at": "2026-04-22T10:00:00+00:00",
    }


async def test_mark_completed_returns_iso_timestamp() -> None:
    session = _FakeSession()
    svc = SetupService(session=session)  # type: ignore[arg-type]
    result = await svc.mark_completed()
    assert result["completed"] is True
    assert isinstance(result["completed_at"], str)
    # ISO-8601 with timezone
    assert "T" in result["completed_at"]
    assert result["completed_at"].endswith("+00:00")
    assert session.flush_count == 1
