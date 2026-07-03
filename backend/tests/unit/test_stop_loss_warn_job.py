"""unit/test_stop_loss_warn_job.py: 止损预警 Job 单元测试（Phase 10 §5.5 + V1.5-G G-4c/G-4d-3）。

覆盖：
- 非交易日跳过
- distance_pct ≤ 2% 触发 notify_stop_loss_warn（带 account_id）
- distance_pct > 2% 不触发
- distance_pct ≤ 0 不触发（股价已跌破止损）
- 缺少 current_price / BUY signal / stop_loss_price → 跳过
- 多持仓：部分触发 + 异常隔离
- G-4c 多用户：遍历 active 用户账户，各自持仓独立扫描 + 通知带各自 account_id
- G-4d-3：账户回撤 ≥ 阈值 → notify_risk_warn(account_drawdown)；持仓私有 SELL →
  notify_risk_warn(trigger_reason)；均带 account_id
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from quantpilot.core.config_defaults import DEFAULT_RISK_LIMITS
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
    # G-4d-3：cfg.get_risk_limits() 一次性加载回撤阈值 → 须 async mock 返回真实配置
    cfg_mock = MagicMock()
    cfg_mock.get_risk_limits = AsyncMock(return_value=DEFAULT_RISK_LIMITS)
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
        patch("quantpilot.services.config_service.ConfigService", return_value=cfg_mock),
        patch("quantpilot.data.repository.MarketDataRepository"),
    ]


async def _run(
    account_service,
    signal_service,
    notifier,
    *,
    is_trade: bool = True,
    private_signals: list | None = None,
    drawdown: float | None = None,
) -> None:
    # G-4d-3：默认无私有 SELL / 无回撤，既有止损用例不受新分支干扰；
    # 新用例经 private_signals / drawdown 参数注入。
    signal_service.evaluate_private_signals = AsyncMock(return_value=private_signals or [])
    account_service.get_current_drawdown = AsyncMock(return_value=drawdown)

    patches = _patch_deps(account_service, signal_service, notifier)
    for p in patches:
        p.start()
    try:
        await _stop_loss_warn_job(_session_factory, _calendar(is_trade), None, None)
    finally:
        for p in patches:
            p.stop()


def _private_sell(ts_code: str, trigger_reason: str) -> SimpleNamespace:
    """构造 evaluate_private_signals 返回的私有 SELL TradeSignal（够 Job 读取即可）。"""
    return SimpleNamespace(
        ts_code=ts_code, signal_type="SELL", trigger_reason=trigger_reason,
        reason=f"{trigger_reason} 触发",
    )


def _private_buy(ts_code: str, score: float = 85.0) -> SimpleNamespace:
    """构造 evaluate_private_signals 返回的加仓 BUY TradeSignal（G-4d-4）。"""
    return SimpleNamespace(
        ts_code=ts_code, signal_type="BUY", trigger_reason="pct_below_buy",
        reason="加仓条件满足", score=score,
        suggested_price_low=9.9, suggested_price_high=10.2,
    )


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


# ---------------------------------------------------------------------------
# G-4d-3：账户回撤主动告警
# ---------------------------------------------------------------------------
async def test_drawdown_warn_triggers_when_at_threshold() -> None:
    """账户回撤 ≥ max_drawdown_pct → notify_risk_warn(account_drawdown, account_id)。"""
    account_service = _account_service({5: []})
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=None)
    notifier = AsyncMock()
    notifier.notify_risk_warn = AsyncMock()

    # DEFAULT_RISK_LIMITS.max_drawdown_pct 之上
    dd = DEFAULT_RISK_LIMITS.max_drawdown_pct + 0.05
    await _run(account_service, signal_service, notifier, drawdown=dd)

    notifier.notify_risk_warn.assert_awaited_once()
    kwargs = notifier.notify_risk_warn.await_args.kwargs
    assert kwargs["event_type"] == "account_drawdown"
    assert kwargs["account_id"] == 5


async def test_drawdown_warn_skipped_below_threshold() -> None:
    """回撤低于阈值 → 不 notify_risk_warn。"""
    account_service = _account_service({1: []})
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=None)
    notifier = AsyncMock()
    notifier.notify_risk_warn = AsyncMock()

    dd = max(0.0, DEFAULT_RISK_LIMITS.max_drawdown_pct - 0.05)
    await _run(account_service, signal_service, notifier, drawdown=dd)
    notifier.notify_risk_warn.assert_not_awaited()


async def test_drawdown_warn_skipped_when_none() -> None:
    """回撤数据不足（None）→ 不 notify。"""
    account_service = _account_service({1: []})
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=None)
    notifier = AsyncMock()
    notifier.notify_risk_warn = AsyncMock()

    await _run(account_service, signal_service, notifier, drawdown=None)
    notifier.notify_risk_warn.assert_not_awaited()


# ---------------------------------------------------------------------------
# G-4d-3：持仓私有 SELL 主动告警
# ---------------------------------------------------------------------------
async def test_private_sell_notifies_per_trigger() -> None:
    """evaluate_private_signals 返回 hard_stop_loss → notify_risk_warn(trigger, account_id)。"""
    position = _make_position("000001.SZ", 10.00)
    account_service = _account_service({9: [position]})
    signal_service = AsyncMock()
    # 该持仓无 BUY 信号 → 不走止损预警路径，隔离验证私有 SELL 分支
    signal_service.get_last_buy_signal = AsyncMock(return_value=None)
    notifier = AsyncMock()
    notifier.notify_risk_warn = AsyncMock()

    private = [_private_sell("000001.SZ", "hard_stop_loss")]
    await _run(account_service, signal_service, notifier, private_signals=private)

    notifier.notify_risk_warn.assert_awaited_once()
    kwargs = notifier.notify_risk_warn.await_args.kwargs
    assert kwargs["event_type"] == "hard_stop_loss"
    assert kwargs["account_id"] == 9
    assert kwargs["payload"]["ts_code"] == "000001.SZ"


async def test_private_add_buy_notifies_signal_buy() -> None:
    """G-4d-4：加仓 BUY → notifier.notify("SIGNAL_BUY", ..., account_id)（不走 risk_warn）。"""
    position = _make_position("000001.SZ", 10.00)
    account_service = _account_service({3: [position]})
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=None)
    notifier = AsyncMock()
    notifier.notify = AsyncMock()
    notifier.notify_risk_warn = AsyncMock()

    private = [_private_buy("000001.SZ")]
    await _run(account_service, signal_service, notifier, private_signals=private)

    notifier.notify.assert_awaited_once()
    args = notifier.notify.await_args
    assert args.args[0] == "SIGNAL_BUY"
    assert args.kwargs["account_id"] == 3
    assert args.kwargs["payload"]["ts_code"] == "000001.SZ"
    notifier.notify_risk_warn.assert_not_awaited()


async def test_private_mixed_buy_and_sell_routed_separately() -> None:
    """G-4d-4：同账户同时有私有 SELL + 加仓 BUY → 各走各的通知入口。"""
    position = _make_position("000001.SZ", 10.00)
    account_service = _account_service({1: [position]})
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=None)
    notifier = AsyncMock()
    notifier.notify = AsyncMock()
    notifier.notify_risk_warn = AsyncMock()

    private = [
        _private_sell("000001.SZ", "hard_stop_loss"),
        _private_buy("000002.SZ"),
    ]
    await _run(account_service, signal_service, notifier, private_signals=private)

    notifier.notify_risk_warn.assert_awaited_once()
    assert notifier.notify_risk_warn.await_args.kwargs["event_type"] == "hard_stop_loss"
    notifier.notify.assert_awaited_once()
    assert notifier.notify.await_args.args[0] == "SIGNAL_BUY"


async def test_private_sell_notify_exception_isolated() -> None:
    """一条私有 SELL 通知失败不影响其他（异常隔离）。"""
    position = _make_position("000001.SZ", 10.00)
    account_service = _account_service({1: [position]})
    signal_service = AsyncMock()
    signal_service.get_last_buy_signal = AsyncMock(return_value=None)
    notifier = AsyncMock()
    notifier.notify_risk_warn = AsyncMock(side_effect=[RuntimeError("boom"), None])

    private = [
        _private_sell("000001.SZ", "hard_stop_loss"),
        _private_sell("000002.SZ", "mid_term_icir_flip"),
    ]
    # 不应向外抛出
    await _run(account_service, signal_service, notifier, private_signals=private)
    assert notifier.notify_risk_warn.await_count == 2


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
