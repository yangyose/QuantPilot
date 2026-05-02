"""unit/test_stop_loss_warn_job.py: Phase 10 §5.5 止损预警 Job 单元测试。

覆盖：
- 非交易日跳过
- distance_pct ≤ 2% 触发 notify_stop_loss_warn
- distance_pct > 2% 不触发
- distance_pct ≤ 0 不触发（股价已跌破止损）
- 缺少 current_price / BUY signal / stop_loss_price → 跳过
- 多持仓：部分触发 + 异常隔离
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from quantpilot.pipeline.scheduler import _stop_loss_warn_job


class _DummySessionCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def commit(self) -> None:
        return None


def _session_factory() -> _DummySessionCtx:
    return _DummySessionCtx()


def _calendar(is_trade: bool):
    return SimpleNamespace(is_trade_date=lambda _d: is_trade)


def _make_position(ts_code: str, current_price: float | None) -> SimpleNamespace:
    return SimpleNamespace(
        ts_code=ts_code,
        current_price=Decimal(str(current_price)) if current_price is not None else None,
    )


def _make_signal(stop_loss: float | None) -> SimpleNamespace:
    return SimpleNamespace(
        stop_loss_price=Decimal(str(stop_loss)) if stop_loss is not None else None,
    )


def _patch_deps(account_service, signal_service, notifier):
    """返回一组 patch context managers；在 `with` 外联合使用覆盖 job 内部 lazy imports。"""
    return [
        patch(
            "quantpilot.services.account_service.AccountService",
            return_value=account_service,
        ),
        patch(
            "quantpilot.services.signal_service.SignalService",
            return_value=signal_service,
        ),
        patch(
            "quantpilot.services.notification_service.NotificationService",
            return_value=notifier,
        ),
        patch("quantpilot.services.config_service.ConfigService"),
        patch("quantpilot.data.repository.MarketDataRepository"),
    ]


async def _run(account_service, signal_service, notifier, *, is_trade: bool = True) -> None:
    patches = _patch_deps(account_service, signal_service, notifier)
    for p in patches:
        p.start()
    try:
        await _stop_loss_warn_job(_session_factory, _calendar(is_trade), None, None)
    finally:
        for p in patches:
            p.stop()


async def test_stop_loss_warn_skipped_non_trade_date() -> None:
    account_service = AsyncMock()
    account_service.get_all_positions = AsyncMock()
    signal_service = AsyncMock()
    notifier = AsyncMock()
    await _run(account_service, signal_service, notifier, is_trade=False)
    account_service.get_all_positions.assert_not_awaited()


async def test_stop_loss_warn_triggers_within_threshold() -> None:
    """current=10.10, stop_loss=10.00 → distance_pct ≈ 0.0099 ≤ 0.02 → notify。"""
    position = _make_position("000001.SZ", 10.10)
    signal = _make_signal(10.00)

    account_service = AsyncMock()
    account_service.get_all_positions = AsyncMock(return_value=[position])
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=signal)
    notifier = AsyncMock()
    notifier.notify_stop_loss_warn = AsyncMock()

    await _run(account_service, signal_service, notifier)

    notifier.notify_stop_loss_warn.assert_awaited_once()
    kwargs = notifier.notify_stop_loss_warn.await_args.kwargs
    assert kwargs["ts_code"] == "000001.SZ"
    assert kwargs["current_price"] == pytest.approx(10.10)
    assert kwargs["stop_loss_price"] == pytest.approx(10.00)
    assert kwargs["distance_pct"] == pytest.approx(0.0099, abs=1e-4)


async def test_stop_loss_warn_skipped_above_threshold() -> None:
    """current=11.00, stop_loss=10.00 → distance_pct ≈ 0.091 > 0.02 → 不 notify。"""
    position = _make_position("000001.SZ", 11.00)
    signal = _make_signal(10.00)

    account_service = AsyncMock()
    account_service.get_all_positions = AsyncMock(return_value=[position])
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=signal)
    notifier = AsyncMock()

    await _run(account_service, signal_service, notifier)
    notifier.notify_stop_loss_warn.assert_not_awaited()


async def test_stop_loss_warn_skipped_below_stop_loss() -> None:
    """current=9.90 < stop_loss=10.00 → distance_pct < 0 → 不 notify（已跌破，属风险告警范畴）。"""
    position = _make_position("000001.SZ", 9.90)
    signal = _make_signal(10.00)

    account_service = AsyncMock()
    account_service.get_all_positions = AsyncMock(return_value=[position])
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=signal)
    notifier = AsyncMock()

    await _run(account_service, signal_service, notifier)
    notifier.notify_stop_loss_warn.assert_not_awaited()


async def test_stop_loss_warn_skipped_no_current_price() -> None:
    position = _make_position("000001.SZ", None)
    account_service = AsyncMock()
    account_service.get_all_positions = AsyncMock(return_value=[position])
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock()
    notifier = AsyncMock()

    await _run(account_service, signal_service, notifier)
    signal_service.get_last_buy_signal.assert_not_awaited()
    notifier.notify_stop_loss_warn.assert_not_awaited()


async def test_stop_loss_warn_skipped_no_signal() -> None:
    position = _make_position("000001.SZ", 10.10)
    account_service = AsyncMock()
    account_service.get_all_positions = AsyncMock(return_value=[position])
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=None)
    notifier = AsyncMock()

    await _run(account_service, signal_service, notifier)
    notifier.notify_stop_loss_warn.assert_not_awaited()


async def test_stop_loss_warn_skipped_no_stop_loss_price() -> None:
    position = _make_position("000001.SZ", 10.10)
    signal = _make_signal(None)
    account_service = AsyncMock()
    account_service.get_all_positions = AsyncMock(return_value=[position])
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=signal)
    notifier = AsyncMock()

    await _run(account_service, signal_service, notifier)
    notifier.notify_stop_loss_warn.assert_not_awaited()


async def test_stop_loss_warn_exception_isolated_per_position() -> None:
    """一个持仓通知失败不影响其他持仓。"""
    p_ok = _make_position("000001.SZ", 10.10)
    p_fail = _make_position("000002.SZ", 20.10)
    sig_ok = _make_signal(10.00)
    sig_fail = _make_signal(20.00)

    account_service = AsyncMock()
    account_service.get_all_positions = AsyncMock(return_value=[p_fail, p_ok])
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(side_effect=[sig_fail, sig_ok])

    notifier = AsyncMock()
    notifier.notify_stop_loss_warn = AsyncMock(
        side_effect=[RuntimeError("boom"), None]
    )

    # 不应向外抛出异常
    await _run(account_service, signal_service, notifier)
    assert notifier.notify_stop_loss_warn.await_count == 2


async def test_stop_loss_warn_zero_current_price_skipped() -> None:
    position = _make_position("000001.SZ", 0.0)
    signal = _make_signal(10.00)
    account_service = AsyncMock()
    account_service.get_all_positions = AsyncMock(return_value=[position])
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=signal)
    notifier = AsyncMock()

    await _run(account_service, signal_service, notifier)
    notifier.notify_stop_loss_warn.assert_not_awaited()


async def test_stop_loss_warn_empty_positions_no_notify() -> None:
    account_service = AsyncMock()
    account_service.get_all_positions = AsyncMock(return_value=[])
    signal_service = AsyncMock()
    notifier = AsyncMock()

    await _run(account_service, signal_service, notifier)
    notifier.notify_stop_loss_warn.assert_not_awaited()
    account_service.get_all_positions.assert_awaited_once()
