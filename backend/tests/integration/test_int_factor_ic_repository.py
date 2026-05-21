"""Phase 11 §4.1 FactorICRepository + rolling_icir_state 集成测试。

覆盖（INT-P11-IC-01 ~ INT-P11-IC-05）：
- 01: upsert_ic_daily → get_ic_daily_window 往返
- 02: upsert_ic_aggregate → get_latest_icir
- 03: rolling_icir_state 真实窗口查询 + 数学验证
- 04: state 子集隔离（不同 state 的 IC 不混入）
- 05: strategy_weights upsert + get_latest_strategy_weights DISTINCT ON

集成测试在独立测试 DB 跑（DATABASE_URL 指向 quantpilot-test-db @ port 5433）。
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.factor_ic_repository import (
    FactorICRepository,
    ICAggregateRow,
    ICDailyRow,
    StrategyWeightsRow,
)
from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.services.factor_monitor_service import FactorMonitorService

# 独特前缀避免与其它测试冲突
_STRATEGY = "phase11_test_trend"
_FACTOR = "phase11_test_ma"
_STATE = "UPTREND"


# ============================================================
# INT-P11-IC-01：upsert + 窗口查询往返
# ============================================================
async def test_int_p11_ic_01_upsert_daily_and_query_window(
    db_session: AsyncSession,
) -> None:
    repo = FactorICRepository()
    base = date(2025, 6, 1)
    rows = [
        ICDailyRow(
            strategy=_STRATEGY,
            factor=_FACTOR,
            state=_STATE,
            trade_date=base + timedelta(days=i),
            ic_value=0.05 + i * 0.001,
            sample_size=200,
        )
        for i in range(10)
    ]
    inserted = await repo.upsert_ic_daily(db_session, rows)
    assert inserted == 10

    # 查询全窗口
    fetched = await repo.get_ic_daily_window(
        db_session,
        strategy=_STRATEGY,
        factor=_FACTOR,
        state=_STATE,
        start_date=base,
        end_date=base + timedelta(days=9),
    )
    assert len(fetched) == 10
    # 升序排列
    assert fetched[0].trade_date < fetched[-1].trade_date


# ============================================================
# INT-P11-IC-02：聚合行 upsert + 最新一行查询
# ============================================================
async def test_int_p11_ic_02_upsert_aggregate_and_get_latest(
    db_session: AsyncSession,
) -> None:
    repo = FactorICRepository()
    base = date(2025, 12, 31)
    rows = [
        ICAggregateRow(
            strategy=_STRATEGY,
            factor=_FACTOR,
            state=_STATE,
            trade_date=base - timedelta(days=30 * i),  # 过去 3 个月末
            ic_mean_state=0.06 - i * 0.01,
            ic_std_state=0.02,
            icir=(0.06 - i * 0.01) / 0.02,
            sample_size=180,
            ic_ci_low=0.03,
            ic_ci_high=0.09,
            t_stat=2.5 - i * 0.5,
            half_life=12,
        )
        for i in range(3)
    ]
    await repo.upsert_ic_aggregate(db_session, rows)

    latest = await repo.get_latest_icir(
        db_session,
        strategy=_STRATEGY,
        factor=_FACTOR,
        state=_STATE,
        as_of=base,
    )
    assert latest is not None
    # 最新行应是 base（i=0）的 icir=3.0
    assert latest.trade_date == base
    assert abs(float(latest.icir) - 3.0) < 0.01


# ============================================================
# INT-P11-IC-03：rolling_icir_state 端到端
# ============================================================
async def test_int_p11_ic_03_rolling_icir_state_end_to_end(
    db_session: AsyncSession,
) -> None:
    """构造 100 天 IC_daily（在 [t-272, t-20] 窗口内）→ 调
    rolling_icir_state → 返回 ICIRSnapshot 数学正确。
    """
    import numpy as np

    repo = FactorICRepository()
    t = date(2025, 12, 31)
    # 在窗口 [t-272, t-20] 内分布 100 个数据点（每 2 天一个）
    rng = np.random.default_rng(42)
    rows: list[ICDailyRow] = []
    for i in range(100):
        td = t - timedelta(days=20 + 2 * i)  # td 从 t-20 倒推 200 天
        if td < t - timedelta(days=272):
            break
        ic = float(rng.normal(0.05, 0.02))
        rows.append(ICDailyRow(
            strategy=_STRATEGY,
            factor=_FACTOR,
            state=_STATE,
            trade_date=td,
            ic_value=ic,
            sample_size=200,
        ))
    await repo.upsert_ic_daily(db_session, rows)
    await db_session.flush()

    service = FactorMonitorService(session=db_session, engine=FactorMonitorEngine())
    snapshot = await service.rolling_icir_state(
        session=db_session,
        trade_date=t,
        strategy=_STRATEGY,
        factor=_FACTOR,
        state=_STATE,
    )
    assert snapshot is not None
    assert snapshot.sample_size == len(rows)  # 全部进入窗口
    # ic_mean 接近 0.05
    assert abs(snapshot.ic_mean - 0.05) < 0.01
    # icir = ic_mean / ic_std ≈ 2.5
    assert 1.5 < snapshot.icir < 3.5
    # CI 包含 ic_mean
    assert snapshot.ic_ci_low < snapshot.ic_mean < snapshot.ic_ci_high


# ============================================================
# INT-P11-IC-04：state 子集隔离
# ============================================================
async def test_int_p11_ic_04_state_isolation(db_session: AsyncSession) -> None:
    """UPTREND 写 100 条（有方差）+ DOWNTREND 写 50 条；rolling_icir_state(UPTREND)
    应只看到 UPTREND 的 100 条（不混入 DOWNTREND），且 ic_mean 接近 UPTREND 的均值。
    DOWNTREND 因样本不足应返回 None。"""
    import numpy as np

    repo = FactorICRepository()
    t = date(2025, 12, 31)
    rng_up = np.random.default_rng(101)
    rng_down = np.random.default_rng(202)

    rows_up = [
        ICDailyRow(
            strategy=_STRATEGY, factor=_FACTOR, state="UPTREND",
            trade_date=t - timedelta(days=21 + i),
            ic_value=float(rng_up.normal(0.08, 0.02)),  # UPTREND 均值 0.08，有方差
            sample_size=200,
        )
        for i in range(100)
    ]
    rows_down = [
        ICDailyRow(
            strategy=_STRATEGY, factor=_FACTOR, state="DOWNTREND",
            trade_date=t - timedelta(days=21 + i),
            ic_value=float(rng_down.normal(-0.05, 0.02)),  # DOWNTREND 均值负
            sample_size=200,
        )
        for i in range(50)
    ]
    await repo.upsert_ic_daily(db_session, rows_up + rows_down)
    await db_session.flush()

    service = FactorMonitorService(session=db_session, engine=FactorMonitorEngine())
    snap_up = await service.rolling_icir_state(
        session=db_session, trade_date=t,
        strategy=_STRATEGY, factor=_FACTOR, state="UPTREND",
    )
    assert snap_up is not None, "UPTREND 100 条有方差应返回非 None"
    assert snap_up.sample_size == 100
    # UPTREND ic_mean 接近 0.08（不混入 DOWNTREND 的 -0.05）
    assert abs(snap_up.ic_mean - 0.08) < 0.02

    snap_down = await service.rolling_icir_state(
        session=db_session, trade_date=t,
        strategy=_STRATEGY, factor=_FACTOR, state="DOWNTREND",
    )
    # DOWNTREND 只有 50 行 < 60 → None
    assert snap_down is None


# ============================================================
# INT-P11-IC-05：strategy_weights upsert + DISTINCT ON
# ============================================================
async def test_int_p11_ic_05_strategy_weights_upsert_and_distinct(
    db_session: AsyncSession,
) -> None:
    repo = FactorICRepository()
    state = "UPTREND"
    feb = date(2025, 2, 1)
    mar = date(2025, 3, 1)
    rows = [
        # 2 月权重（旧）
        StrategyWeightsRow(state=state, strategy="trend", trade_date=feb,
                            weight_used=0.40, weights_source="default_matrix",
                            icir_inputs=None, hysteresis_status="stable"),
        StrategyWeightsRow(state=state, strategy="momentum", trade_date=feb,
                            weight_used=0.25, weights_source="default_matrix",
                            icir_inputs=None, hysteresis_status="stable"),
        # 3 月权重（新）
        StrategyWeightsRow(state=state, strategy="trend", trade_date=mar,
                            weight_used=0.45, weights_source="icir",
                            icir_inputs={"trend": 0.18}, hysteresis_status="stable"),
        StrategyWeightsRow(state=state, strategy="momentum", trade_date=mar,
                            weight_used=0.20, weights_source="icir",
                            icir_inputs={"momentum": 0.08}, hysteresis_status="stable"),
    ]
    await repo.upsert_strategy_weights(db_session, rows)
    await db_session.flush()

    # 查询 as_of=2025-03-15 应返回 3 月的两行（DISTINCT ON 取最新）
    latest = await repo.get_latest_strategy_weights(
        db_session, state=state, as_of=date(2025, 3, 15),
    )
    weights_map = {r.strategy: r for r in latest}
    assert weights_map["trend"].trade_date == mar
    assert weights_map["trend"].weights_source == "icir"
    assert abs(float(weights_map["trend"].weight_used) - 0.45) < 1e-9
    assert weights_map["momentum"].trade_date == mar

    # 查询 as_of=2025-02-15 应返回 2 月的两行
    earlier = await repo.get_latest_strategy_weights(
        db_session, state=state, as_of=date(2025, 2, 15),
    )
    earlier_map = {r.strategy: r for r in earlier}
    assert earlier_map["trend"].trade_date == feb
    assert earlier_map["trend"].weights_source == "default_matrix"
