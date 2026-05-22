"""R13-P1-2 回归：apply_monthly_rebalance 必须接入 check_persistent_decay。

避免「方法定义但无调用点」的接入孤儿 → 持续 3 月衰减永远不会告警。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from quantpilot.services.factor_monitor_service import (
    FactorMonitorService,
    ICIRSnapshot,
)


def _snap(icir: float, strategy: str = "trend", state: str = "UPTREND") -> ICIRSnapshot:
    return ICIRSnapshot(
        strategy=strategy, factor=strategy, state=state,
        as_of_date=date(2026, 4, 30),
        ic_mean=0.04, ic_std=0.5, icir=icir, sample_size=80,
        ic_ci_low=-0.1, ic_ci_high=0.18, t_stat=0.7,
    )


async def test_ut_r13_p1_2_apply_monthly_rebalance_calls_check_persistent_decay() -> None:
    """apply_monthly_rebalance 内部必须为每个 (state, strategy) 调一次
    check_persistent_decay，并把 notifier 透传下去。"""
    repo = SimpleNamespace()
    repo.upsert_ic_aggregate = AsyncMock()
    repo.get_latest_strategy_weights = AsyncMock(return_value=[])
    repo.upsert_strategy_weights = AsyncMock()

    svc = FactorMonitorService(session=None, engine=None, repo=repo)
    svc.rolling_icir_state = AsyncMock(return_value=_snap(0.03))
    svc.check_factor_offline_rules = AsyncMock(return_value={})
    svc.check_persistent_decay = AsyncMock(return_value=False)

    fake_session = AsyncMock()
    fake_session.flush = AsyncMock()
    notifier = AsyncMock()

    await svc.apply_monthly_rebalance(
        fake_session, month_end_date=date(2026, 4, 30), notifier=notifier,
    )

    # 3 个 state × 4 个 strategy = 12 次调用
    assert svc.check_persistent_decay.await_count == 12, (
        f"应调 12 次（3 state × 4 strategy），实际 {svc.check_persistent_decay.await_count}"
    )
    # 任取一次断言 notifier 被透传
    first_call = svc.check_persistent_decay.await_args_list[0]
    assert first_call.kwargs.get("notifier") is notifier, (
        "notifier 必须透传给 check_persistent_decay"
    )
    assert first_call.kwargs.get("icir_now") == 0.03
    assert first_call.kwargs.get("as_of") == date(2026, 4, 30)


async def test_ut_r13_p1_2b_apply_monthly_rebalance_swallows_decay_check_exception() -> None:
    """check_persistent_decay 抛异常不应阻断 rebalance 主流程（best-effort）。"""
    repo = SimpleNamespace()
    repo.upsert_ic_aggregate = AsyncMock()
    repo.get_latest_strategy_weights = AsyncMock(return_value=[])
    repo.upsert_strategy_weights = AsyncMock()

    svc = FactorMonitorService(session=None, engine=None, repo=repo)
    svc.rolling_icir_state = AsyncMock(return_value=_snap(0.04))
    svc.check_factor_offline_rules = AsyncMock(return_value={})
    svc.check_persistent_decay = AsyncMock(side_effect=RuntimeError("boom"))

    fake_session = AsyncMock()
    fake_session.flush = AsyncMock()

    # 不应抛异常 + strategy_weights 仍被写入
    result = await svc.apply_monthly_rebalance(
        fake_session, month_end_date=date(2026, 4, 30), notifier=AsyncMock(),
    )
    assert len(result) == 3  # 3 个 state 全部完成
    assert repo.upsert_strategy_weights.await_count == 3
