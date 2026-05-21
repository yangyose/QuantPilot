"""UT-P13-B-03~04: Phase 13 FactorMonitorService.check_persistent_decay 单元测试。

依据 docs/design/phases/phase13_production_observability.md §3.5 + §6.1：
- UT-P13-B-03: 连续 3 月 icir < 0.05 → 触发 notify_factor_alert
- UT-P13-B-04: 不触发场景（< 3 月 / 单月 < / 中间月反弹 / icir_now >= 阈值）
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from quantpilot.services.factor_monitor_service import FactorMonitorService


def _svc_with_history(rows: list) -> tuple[FactorMonitorService, AsyncMock]:
    repo = AsyncMock()
    repo.get_recent_aggregates = AsyncMock(return_value=rows)
    svc = FactorMonitorService(session=None, engine=None, repo=repo)
    return svc, repo


async def test_ut_p13_b_03_triggers_when_three_months_all_below() -> None:
    """UT-P13-B-03: 历史 3 月 icir 均 < 0.05 + icir_now < 0.05 → 触发告警。"""
    history = [
        SimpleNamespace(icir=0.02),
        SimpleNamespace(icir=0.03),
        SimpleNamespace(icir=0.01),
    ]
    svc, _ = _svc_with_history(history)
    notifier = AsyncMock()
    notifier.notify_factor_alert = AsyncMock()

    triggered = await svc.check_persistent_decay(
        session=None,  # type: ignore[arg-type]
        strategy="trend", factor="macd_hist", state="UPTREND",
        icir_now=0.04, notifier=notifier, as_of=date(2026, 5, 31),
    )
    assert triggered is True
    notifier.notify_factor_alert.assert_awaited_once()
    args = notifier.notify_factor_alert.await_args.args
    assert args[0] == "factor_decayed_persistent"


async def test_ut_p13_b_04_does_not_trigger_when_conditions_unmet() -> None:
    """UT-P13-B-04: 4 个不触发场景全部 assert false。"""
    # (1) icir_now >= 阈值 → 立即返回 False，不查 history
    svc, repo = _svc_with_history([])
    notifier = AsyncMock()
    notifier.notify_factor_alert = AsyncMock()
    triggered = await svc.check_persistent_decay(
        session=None,  # type: ignore[arg-type]
        strategy="t", factor="f", state="UPTREND",
        icir_now=0.10, notifier=notifier,  # 0.10 >= 0.05
    )
    assert triggered is False
    notifier.notify_factor_alert.assert_not_awaited()
    repo.get_recent_aggregates.assert_not_awaited()

    # (2) 历史不足 3 月（仅 2 行）→ False
    svc, _ = _svc_with_history([SimpleNamespace(icir=0.02), SimpleNamespace(icir=0.01)])
    notifier = AsyncMock()
    notifier.notify_factor_alert = AsyncMock()
    triggered = await svc.check_persistent_decay(
        session=None,  # type: ignore[arg-type]
        strategy="t", factor="f", state="UPTREND",
        icir_now=0.04, notifier=notifier,
    )
    assert triggered is False
    notifier.notify_factor_alert.assert_not_awaited()

    # (3) 中间月反弹（3 行但中间一行 ≥ 阈值）→ False
    svc, _ = _svc_with_history([
        SimpleNamespace(icir=0.02),
        SimpleNamespace(icir=0.08),  # 反弹
        SimpleNamespace(icir=0.01),
    ])
    notifier = AsyncMock()
    notifier.notify_factor_alert = AsyncMock()
    triggered = await svc.check_persistent_decay(
        session=None,  # type: ignore[arg-type]
        strategy="t", factor="f", state="UPTREND",
        icir_now=0.04, notifier=notifier,
    )
    assert triggered is False
    notifier.notify_factor_alert.assert_not_awaited()

    # (4) icir_now is None → False
    svc, _ = _svc_with_history([
        SimpleNamespace(icir=0.02),
        SimpleNamespace(icir=0.01),
        SimpleNamespace(icir=0.03),
    ])
    notifier = AsyncMock()
    notifier.notify_factor_alert = AsyncMock()
    triggered = await svc.check_persistent_decay(
        session=None,  # type: ignore[arg-type]
        strategy="t", factor="f", state="UPTREND",
        icir_now=None, notifier=notifier,
    )
    assert triggered is False
    notifier.notify_factor_alert.assert_not_awaited()
