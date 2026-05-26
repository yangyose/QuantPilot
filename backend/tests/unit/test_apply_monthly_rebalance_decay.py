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


# ============================================================
# Phase 14 §14-7 R13-P2-3：持续告警命中时同月单月 R-rule 告警被抑制
# ============================================================


async def _make_rebalance_svc(
    snap_icir: float,
    persistent_hit_states: set[str] | None = None,
    offline_action: str = "offline",
    offline_rule: str = "R1",
) -> tuple[FactorMonitorService, AsyncMock, AsyncMock, AsyncMock]:
    """构造 svc + fake_session + notifier + check_persistent_decay mock。

    persistent_hit_states：哪些 state 的 check_persistent_decay 应返回 True
    （None → 全 False）。其它 state 都返回 False。
    offline_action：check_factor_offline_rules mock 返回的 action 值
    （"offline" / "halve" / "warn" / "ok"）；除 "ok" 外都会触发单月告警。
    """
    repo = SimpleNamespace()
    repo.upsert_ic_aggregate = AsyncMock()
    repo.get_latest_strategy_weights = AsyncMock(return_value=[])
    repo.upsert_strategy_weights = AsyncMock()

    svc = FactorMonitorService(session=None, engine=None, repo=repo)
    svc.rolling_icir_state = AsyncMock(return_value=_snap(snap_icir))

    # check_factor_offline_rules：所有 (strategy, factor, state) 返回 offline_action
    def _offline_decision(sfs_list):
        return {sfs: {"action": offline_action, "rule": offline_rule,
                       "details": "mock"} for sfs in sfs_list}

    async def _mock_offline(session, *, as_of_date, strategy_factor_states):
        return _offline_decision(strategy_factor_states)

    svc.check_factor_offline_rules = _mock_offline

    # check_persistent_decay：按 state 决定是否返回 True
    def _persistent_side_effect(*args, **kwargs):
        state = kwargs.get("state")
        return (
            persistent_hit_states is not None and state in persistent_hit_states
        )

    svc.check_persistent_decay = AsyncMock(side_effect=_persistent_side_effect)

    fake_session = AsyncMock()
    fake_session.flush = AsyncMock()
    notifier = AsyncMock()
    notifier.notify_factor_alert = AsyncMock()
    return svc, fake_session, notifier, svc.check_persistent_decay


async def test_ut_p14_7_3a_persistent_hit_suppresses_single_month_alert() -> None:
    """所有 state 都命中持续告警 → 单月 R-rule 告警 0 条（全部被抑制）。"""
    svc, fake_session, notifier, _ = await _make_rebalance_svc(
        snap_icir=0.03,
        persistent_hit_states={"UPTREND", "DOWNTREND", "OSCILLATION"},
        offline_action="offline",
    )
    await svc.apply_monthly_rebalance(
        fake_session, month_end_date=date(2026, 4, 30), notifier=notifier,
    )
    # 所有 (strategy, factor, state) 都被持续告警命中 → 单月 R 告警全部跳过
    notifier.notify_factor_alert.assert_not_awaited()


async def test_ut_p14_7_3b_no_persistent_hit_fires_single_month_alerts() -> None:
    """持续告警未命中 → 单月 R-rule 告警按 strategy × state 全部触发（3×4=12 条）。"""
    svc, fake_session, notifier, _ = await _make_rebalance_svc(
        snap_icir=0.08,                   # 高 ICIR，check_persistent_decay 不会命中
        persistent_hit_states=set(),      # 全部未命中
        offline_action="halve",
        offline_rule="R3",
    )
    await svc.apply_monthly_rebalance(
        fake_session, month_end_date=date(2026, 4, 30), notifier=notifier,
    )
    # 12 个 (strategy, factor, state) 三元组 × action="halve" → 12 条单月告警
    assert notifier.notify_factor_alert.await_count == 12
    # 告警 alert_type 含 R-rule 名
    args, _ = notifier.notify_factor_alert.await_args_list[0]
    assert args[0] == "factor_decayed_R3"


async def test_ut_p14_7_3c_action_ok_does_not_alert() -> None:
    """action=ok（无 R-rule 触发）→ 不发单月告警。"""
    svc, fake_session, notifier, _ = await _make_rebalance_svc(
        snap_icir=0.08,
        persistent_hit_states=set(),
        offline_action="ok",
    )
    await svc.apply_monthly_rebalance(
        fake_session, month_end_date=date(2026, 4, 30), notifier=notifier,
    )
    notifier.notify_factor_alert.assert_not_awaited()


async def test_ut_p14_7_3d_partial_persistent_hit_only_suppresses_matched() -> None:
    """只 UPTREND state 命中持续告警 → UPTREND 4 条单月告警被抑制，
    其余 2 state × 4 strategy = 8 条仍触发。"""
    svc, fake_session, notifier, _ = await _make_rebalance_svc(
        snap_icir=0.04,
        persistent_hit_states={"UPTREND"},
        offline_action="offline",
        offline_rule="R1",
    )
    await svc.apply_monthly_rebalance(
        fake_session, month_end_date=date(2026, 4, 30), notifier=notifier,
    )
    assert notifier.notify_factor_alert.await_count == 8
