"""Phase 11 §4.2 + §4.4 + §4.5 集成测试。

覆盖（INT-P11-RB-01 ~ INT-P11-RB-06）：
- 01: apply_monthly_rebalance 冷启动（无 IC_daily 数据）→ default_matrix
- 02: apply_monthly_rebalance ICIR 加权（有数据）→ icir source + 权重比例正确
- 03: check_factor_offline_rules R1 (ICIR<0 持续 6 月) → offline
- 04: check_factor_offline_rules R3 (half_life<5) → halve
- 05: check_factor_offline_rules R4 (sample_size<60 连续 3 月) → warn
- 06: get_active_weights 冷启动 + 有历史 + Hysteresis pending_switch
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.factor_ic_repository import (
    FactorICRepository,
    ICAggregateRow,
    ICDailyRow,
    StrategyWeightsRow,
)
from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.services.factor_monitor_service import FactorMonitorService

_STRATEGIES = ("trend", "momentum", "mean_reversion", "value")


# ============================================================
# INT-P11-RB-01：冷启动 fallback
# ============================================================
@pytest.mark.anyio
async def test_int_p11_rb_01_cold_start_falls_back_to_default(
    db_session: AsyncSession,
) -> None:
    """无任何 factor_ic_window_state 数据 → apply_monthly_rebalance 写
    default_matrix 权重。"""
    service = FactorMonitorService(session=db_session, engine=FactorMonitorEngine())
    month_end = date(2025, 5, 30)
    result = await service.apply_monthly_rebalance(db_session, month_end)
    await db_session.flush()

    # 三个 state 均应有 4 行
    for state in ("UPTREND", "DOWNTREND", "OSCILLATION"):
        rows = result[state]
        assert len(rows) == 4
        assert all(r.weights_source == "default_matrix" for r in rows)
        # 权重 sum=1
        total = sum(r.weight_used for r in rows)
        assert abs(total - 1.0) < 1e-9

    # UPTREND default trend=0.40 / momentum=0.25 / mean_reversion=0.15 / value=0.20
    uptrend_map = {r.strategy: r.weight_used for r in result["UPTREND"]}
    assert abs(uptrend_map["trend"] - 0.40) < 1e-9
    assert abs(uptrend_map["value"] - 0.20) < 1e-9


# ============================================================
# INT-P11-RB-02：ICIR 加权（有数据）
# ============================================================
@pytest.mark.anyio
async def test_int_p11_rb_02_icir_weighted_when_data_available(
    db_session: AsyncSession,
) -> None:
    """4 个 strategy 各填 60+ 条 UPTREND IC_daily 行，apply_monthly_rebalance
    应使用 ICIR 加权（source='icir'），weight 按 ICIR 比例。"""
    repo = FactorICRepository()
    month_end = date(2025, 12, 31)
    rng = np.random.default_rng(42)

    # UPTREND 状态下 4 strategy 各 80 条 IC_daily 行
    # 不同 strategy 的 mean 不同：trend 最高 ICIR，value 最低
    means = {"trend": 0.08, "momentum": 0.05, "mean_reversion": 0.03, "value": 0.01}
    rows: list[ICDailyRow] = []
    for strategy, mean_ic in means.items():
        for i in range(80):
            td = month_end - timedelta(days=21 + i * 2)  # 在 [t-272, t-20] 窗口
            ic = float(rng.normal(mean_ic, 0.02))
            rows.append(ICDailyRow(
                strategy=strategy, factor=strategy, state="UPTREND",
                trade_date=td, ic_value=ic, sample_size=200,
            ))
    await repo.upsert_ic_daily(db_session, rows)
    await db_session.flush()

    service = FactorMonitorService(session=db_session, engine=FactorMonitorEngine())
    result = await service.apply_monthly_rebalance(db_session, month_end)
    await db_session.flush()

    # UPTREND 权重 source 应该是 icir
    uptrend = {r.strategy: r for r in result["UPTREND"]}
    assert uptrend["trend"].weights_source == "icir"
    # trend 应权重最高（ICIR 最高），value 应权重最低
    assert uptrend["trend"].weight_used > uptrend["value"].weight_used
    # 权重 sum=1
    total = sum(r.weight_used for r in result["UPTREND"])
    assert abs(total - 1.0) < 1e-9

    # DOWNTREND / OSCILLATION 无数据 → default_matrix
    assert result["DOWNTREND"][0].weights_source == "default_matrix"
    assert result["OSCILLATION"][0].weights_source == "default_matrix"


# ============================================================
# INT-P11-RB-03：check_factor_offline_rules R1 (ICIR<0 持续 6 月)
# ============================================================
@pytest.mark.anyio
async def test_int_p11_rb_03_check_r1_offline(db_session: AsyncSession) -> None:
    """构造 6 行连续 ICIR<0 的聚合行 → check_factor_offline_rules 返回 R1 offline。"""
    repo = FactorICRepository()
    today = date(2025, 12, 31)
    rows = [
        ICAggregateRow(
            strategy="trend", factor="trend", state="UPTREND",
            trade_date=today - timedelta(days=30 * i),
            ic_mean_state=-0.02, ic_std_state=0.01, icir=-2.0,
            sample_size=200, ic_ci_low=-0.04, ic_ci_high=0.0,
            t_stat=-2.5, half_life=20,
        )
        for i in range(6)
    ]
    await repo.upsert_ic_aggregate(db_session, rows)
    await db_session.flush()

    service = FactorMonitorService(session=db_session, engine=FactorMonitorEngine())
    decisions = await service.check_factor_offline_rules(
        db_session, as_of_date=today,
        strategy_factor_states=[("trend", "trend", "UPTREND")],
    )
    d = decisions[("trend", "trend", "UPTREND")]
    assert d["action"] == "offline"
    assert d["rule"] == "R1"


# ============================================================
# INT-P11-RB-04：check_factor_offline_rules R3 (half_life<5)
# ============================================================
@pytest.mark.anyio
async def test_int_p11_rb_04_check_r3_halve(db_session: AsyncSession) -> None:
    """最新一行 half_life=3 < 5 → R3 halve。"""
    repo = FactorICRepository()
    today = date(2025, 12, 31)
    rows = [
        ICAggregateRow(
            strategy="momentum", factor="momentum", state="UPTREND",
            trade_date=today,
            ic_mean_state=0.05, ic_std_state=0.02, icir=2.5,
            sample_size=200, ic_ci_low=0.03, ic_ci_high=0.07,
            t_stat=3.0, half_life=3,    # < 5
        )
    ]
    await repo.upsert_ic_aggregate(db_session, rows)
    await db_session.flush()

    service = FactorMonitorService(session=db_session, engine=FactorMonitorEngine())
    decisions = await service.check_factor_offline_rules(
        db_session, as_of_date=today,
        strategy_factor_states=[("momentum", "momentum", "UPTREND")],
    )
    d = decisions[("momentum", "momentum", "UPTREND")]
    assert d["action"] == "halve"
    assert d["rule"] == "R3"


# ============================================================
# INT-P11-RB-05：check_factor_offline_rules R4 (sample<60 连续 3 月)
# ============================================================
@pytest.mark.anyio
async def test_int_p11_rb_05_check_r4_warn(db_session: AsyncSession) -> None:
    repo = FactorICRepository()
    today = date(2025, 12, 31)
    rows = [
        ICAggregateRow(
            strategy="value", factor="value", state="DOWNTREND",
            trade_date=today - timedelta(days=30 * i),
            ic_mean_state=0.02, ic_std_state=0.01, icir=2.0,
            sample_size=40,    # < 60
            ic_ci_low=0.0, ic_ci_high=0.04,
            t_stat=2.5,
            half_life=20,
        )
        for i in range(3)
    ]
    await repo.upsert_ic_aggregate(db_session, rows)
    await db_session.flush()

    service = FactorMonitorService(session=db_session, engine=FactorMonitorEngine())
    decisions = await service.check_factor_offline_rules(
        db_session, as_of_date=today,
        strategy_factor_states=[("value", "value", "DOWNTREND")],
    )
    d = decisions[("value", "value", "DOWNTREND")]
    assert d["action"] == "warn"
    assert d["rule"] == "R4"


# ============================================================
# INT-P11-RB-06：get_active_weights 三种路径
# ============================================================
@pytest.mark.anyio
async def test_int_p11_rb_06_get_active_weights(db_session: AsyncSession) -> None:
    """
    Path A：无任何历史 → default_matrix
    Path B：已有 icir 权重 → 直接返回
    Path C：pending_switch 状态保留 + order 按 weight 降序
    """
    repo = FactorICRepository()
    service = FactorMonitorService(session=db_session, engine=FactorMonitorEngine())

    # Path A：无历史
    weights_a, src_a, order_a, status_a = await service.get_active_weights(
        db_session, trade_date=date(2025, 1, 1), market_state="UPTREND",
    )
    assert src_a == "default_matrix"
    assert status_a == "stable"
    # UPTREND default order: trend > value > momentum > mean_reversion (按 default weight 降序)
    assert order_a[0] == "trend"  # default weight 最高

    # Path B + C：写入 3 月份 icir 权重，且 hysteresis_status="pending_switch"
    mar = date(2025, 3, 1)
    rows = [
        StrategyWeightsRow(state="UPTREND", strategy="momentum", trade_date=mar,
                            weight_used=0.45, weights_source="icir",
                            icir_inputs={"momentum": 0.20}, hysteresis_status="pending_switch"),
        StrategyWeightsRow(state="UPTREND", strategy="trend", trade_date=mar,
                            weight_used=0.30, weights_source="icir",
                            icir_inputs={"trend": 0.10}, hysteresis_status="pending_switch"),
        StrategyWeightsRow(state="UPTREND", strategy="value", trade_date=mar,
                            weight_used=0.15, weights_source="icir",
                            icir_inputs={"value": 0.05}, hysteresis_status="pending_switch"),
        StrategyWeightsRow(state="UPTREND", strategy="mean_reversion", trade_date=mar,
                            weight_used=0.10, weights_source="icir",
                            icir_inputs={"mean_reversion": 0.03},
                            hysteresis_status="pending_switch"),
    ]
    await repo.upsert_strategy_weights(db_session, rows)
    await db_session.flush()

    weights_b, src_b, order_b, status_b = await service.get_active_weights(
        db_session, trade_date=date(2025, 3, 15), market_state="UPTREND",
    )
    assert src_b == "icir"
    assert status_b == "pending_switch"
    assert abs(weights_b["momentum"] - 0.45) < 1e-9
    # order 按 weight 降序：momentum > trend > value > mean_reversion
    assert order_b == ["momentum", "trend", "value", "mean_reversion"]


# ============================================================
# INT-P11-RB-07：apply_monthly_rebalance 含 R1 因子下线时权重置 0
# ============================================================
@pytest.mark.anyio
async def test_int_p11_rb_07_offline_factor_zeroed_in_rebalance(
    db_session: AsyncSession,
) -> None:
    """构造 trend 在 UPTREND 下连续 6 月 ICIR<0 + 4 个 strategy 都有窗口 IC_daily
    数据 → apply_monthly_rebalance 应将 trend 权重置 0，其它策略权重重新归一化。"""
    repo = FactorICRepository()
    month_end = date(2025, 12, 31)
    rng = np.random.default_rng(7)

    # 1. 各 strategy 60+ 条 IC_daily（让 ICIR 计算可用）
    means = {"trend": -0.05, "momentum": 0.04, "mean_reversion": 0.03, "value": 0.02}
    rows: list[ICDailyRow] = []
    for strategy, mean_ic in means.items():
        for i in range(80):
            td = month_end - timedelta(days=21 + i * 2)
            rows.append(ICDailyRow(
                strategy=strategy, factor=strategy, state="UPTREND",
                trade_date=td, ic_value=float(rng.normal(mean_ic, 0.02)),
                sample_size=200,
            ))
    await repo.upsert_ic_daily(db_session, rows)

    # 2. 预先写入 6 个月连续 ICIR<0 的 trend 聚合行，触发 R1
    aggregate_rows = [
        ICAggregateRow(
            strategy="trend", factor="trend", state="UPTREND",
            trade_date=month_end - timedelta(days=30 * (i + 1)),
            ic_mean_state=-0.02, ic_std_state=0.01, icir=-2.0,
            sample_size=200, ic_ci_low=-0.04, ic_ci_high=0.0,
            t_stat=-2.5, half_life=20,
        )
        for i in range(6)
    ]
    await repo.upsert_ic_aggregate(db_session, aggregate_rows)
    await db_session.flush()

    service = FactorMonitorService(session=db_session, engine=FactorMonitorEngine())
    result = await service.apply_monthly_rebalance(db_session, month_end)
    await db_session.flush()

    uptrend = {r.strategy: r for r in result["UPTREND"]}
    # trend 被 R1 offline → weight=0
    assert uptrend["trend"].weight_used == 0
    # 其它 3 策略权重 sum=1
    total_others = sum(uptrend[s].weight_used for s in ("momentum", "mean_reversion", "value"))
    assert abs(total_others - 1.0) < 1e-9
