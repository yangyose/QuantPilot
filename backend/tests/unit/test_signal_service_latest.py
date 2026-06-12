"""SignalService.get_latest_signals 单元测试。

修复"今日信号"死锚日历今天的缺陷：信号是收盘后每日一次产出，缺省查字面今天
在盘中/周末/节假日必然为空。缺省应回退到"最新有信号的交易日"（与原始规格
spec_v0.1「首页展示最新信号列表」一致）。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

from quantpilot.services.signal_service import SignalService


def _service_with_repo(repo: AsyncMock) -> SignalService:
    svc = SignalService.__new__(SignalService)
    svc._repo = repo  # type: ignore[attr-defined]
    return svc


async def test_get_latest_signals_returns_latest_date_and_signals() -> None:
    """有信号时：解析到最新交易日并返回该日信号 + 日期。"""
    repo = AsyncMock()
    repo.get_latest_signal_date = AsyncMock(return_value=date(2026, 6, 11))
    repo.get_signals_by_date = AsyncMock(return_value=["s1", "s2", "s3"])

    svc = _service_with_repo(repo)
    signals, resolved = await svc.get_latest_signals()

    assert resolved == date(2026, 6, 11)
    assert signals == ["s1", "s2", "s3"]
    repo.get_signals_by_date.assert_awaited_once_with(date(2026, 6, 11), None, None)


async def test_get_latest_signals_passes_filters() -> None:
    """signal_type / status 过滤透传到 repo。"""
    repo = AsyncMock()
    repo.get_latest_signal_date = AsyncMock(return_value=date(2026, 6, 11))
    repo.get_signals_by_date = AsyncMock(return_value=["buy1"])

    svc = _service_with_repo(repo)
    signals, resolved = await svc.get_latest_signals(signal_type="BUY", status="NEW")

    assert signals == ["buy1"]
    repo.get_signals_by_date.assert_awaited_once_with(date(2026, 6, 11), "BUY", "NEW")


async def test_get_latest_signals_no_signals_returns_empty_and_none() -> None:
    """库中无任何信号时：返回 ([], None)，不去查 get_signals_by_date。"""
    repo = AsyncMock()
    repo.get_latest_signal_date = AsyncMock(return_value=None)
    repo.get_signals_by_date = AsyncMock(return_value=[])

    svc = _service_with_repo(repo)
    signals, resolved = await svc.get_latest_signals()

    assert signals == []
    assert resolved is None
    repo.get_signals_by_date.assert_not_awaited()
