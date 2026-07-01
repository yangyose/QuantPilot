"""unit/test_stop_loss_warn_job.py: 止损预警 Job 单元测试（Phase 10 §5.5 + V1.5-G G-4c）。

覆盖：
- 非交易日跳过
- distance_pct ≤ 2% 触发 notify_stop_loss_warn（带 account_id）
- distance_pct > 2% 不触发
- distance_pct ≤ 0 不触发（股价已跌破止损）
- 缺少 current_price / BUY signal / stop_loss_price → 跳过
- 多持仓：部分触发 + 异常隔离
- G-4c 多用户：遍历 active 用户账户，各自持仓独立扫描 + 通知带各自 account_id
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


def _make_account(account_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=account_id)


def _make_position(ts_code: str, current_price: float | None) -> SimpleNamespace:
    return SimpleNamespace(
        ts_code=ts_code,
        current_price=Decimal(str(current_price)) if current_price is not None else None,
    )


def _make_signal(stop_loss: float | None) -> SimpleNamespace:
    return SimpleNamespace(
        stop_loss_price=Decimal(str(stop_loss)) if stop_loss is not None else None,
    )


def _account_service(positions_by_account: dict[int, list]) -> AsyncMock:
    """构造 account_service mock：list_active_user_accounts + get_positions(account_id)。"""
    accounts = [_make_account(aid) for aid in positions_by_account]
    svc = AsyncMock()
    svc.list_active_user_accounts = AsyncMock(return_value=accounts)

    async def _get_positions(account_id: int):
        return positions_by_account[account_id]

    svc.get_positions = AsyncMock(side_effect=_get_positions)
    return svc


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
    account_service = _account_service({1: []})
    signal_service = AsyncMock()
    notifier = AsyncMock()
    await _run(account_service, signal_service, notifier, is_trade=False)
    account_service.list_active_user_accounts.assert_not_awaited()


async def test_stop_loss_warn_triggers_within_threshold() -> None:
    """current=10.10, stop_loss=10.00 → distance_pct ≈ 0.0099 ≤ 0.02 → notify（带 account_id）。"""
    position = _make_position("000001.SZ", 10.10)
    signal = _make_signal(10.00)

    account_service = _account_service({7: [position]})
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
    assert kwargs["account_id"] == 7  # G-4c：账户私有通知带归属账户


async def test_stop_loss_warn_skipped_above_threshold() -> None:
    """current=11.00, stop_loss=10.00 → distance_pct ≈ 0.091 > 0.02 → 不 notify。"""
    position = _make_position("000001.SZ", 11.00)
    signal = _make_signal(10.00)

    account_service = _account_service({1: [position]})
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=signal)
    notifier = AsyncMock()

    await _run(account_service, signal_service, notifier)
    notifier.notify_stop_loss_warn.assert_not_awaited()


async def test_stop_loss_warn_skipped_below_stop_loss() -> None:
    """current=9.90 < stop_loss=10.00 → distance_pct < 0 → 不 notify（已跌破，属风险告警范畴）。"""
    position = _make_position("000001.SZ", 9.90)
    signal = _make_signal(10.00)

    account_service = _account_service({1: [position]})
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=signal)
    notifier = AsyncMock()

    await _run(account_service, signal_service, notifier)
    notifier.notify_stop_loss_warn.assert_not_awaited()


async def test_stop_loss_warn_skipped_no_current_price() -> None:
    position = _make_position("000001.SZ", None)
    account_service = _account_service({1: [position]})
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock()
    notifier = AsyncMock()

    await _run(account_service, signal_service, notifier)
    signal_service.get_last_buy_signal.assert_not_awaited()
    notifier.notify_stop_loss_warn.assert_not_awaited()


async def test_stop_loss_warn_skipped_no_signal() -> None:
    position = _make_position("000001.SZ", 10.10)
    account_service = _account_service({1: [position]})
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=None)
    notifier = AsyncMock()

    await _run(account_service, signal_service, notifier)
    notifier.notify_stop_loss_warn.assert_not_awaited()


async def test_stop_loss_warn_skipped_no_stop_loss_price() -> None:
    position = _make_position("000001.SZ", 10.10)
    signal = _make_signal(None)
    account_service = _account_service({1: [position]})
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

    account_service = _account_service({1: [p_fail, p_ok]})
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
    account_service = _account_service({1: [position]})
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=signal)
    notifier = AsyncMock()

    await _run(account_service, signal_service, notifier)
    notifier.notify_stop_loss_warn.assert_not_awaited()


async def test_stop_loss_warn_empty_positions_no_notify() -> None:
    account_service = _account_service({1: []})
    signal_service = AsyncMock()
    notifier = AsyncMock()

    await _run(account_service, signal_service, notifier)
    notifier.notify_stop_loss_warn.assert_not_awaited()
    account_service.list_active_user_accounts.assert_awaited_once()


async def test_stop_loss_warn_multiuser_per_account_isolation() -> None:
    """G-4c：两账户各自持仓独立扫描，通知带各自 account_id。"""
    pos_a = _make_position("000001.SZ", 10.10)  # 账户 1，触发
    pos_b = _make_position("600000.SH", 20.10)  # 账户 2，触发
    account_service = _account_service({1: [pos_a], 2: [pos_b]})

    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(
        side_effect=[_make_signal(10.00), _make_signal(20.00)]
    )
    notifier = AsyncMock()
    notifier.notify_stop_loss_warn = AsyncMock()

    await _run(account_service, signal_service, notifier)

    assert notifier.notify_stop_loss_warn.await_count == 2
    calls = {
        c.kwargs["account_id"]: c.kwargs["ts_code"]
        for c in notifier.notify_stop_loss_warn.await_args_list
    }
    assert calls == {1: "000001.SZ", 2: "600000.SH"}
