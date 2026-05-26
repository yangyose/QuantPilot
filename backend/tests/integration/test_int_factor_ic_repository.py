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

from quantpilot.data.calendar import TradingCalendar
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


# ============================================================
# INT-P14-5-01：rolling_icir_state 严格交易日窗口（跨周末校验）
# ============================================================
async def test_int_p14_5_01_strict_trade_day_window_skips_weekends(
    db_session: AsyncSession,
) -> None:
    """Phase 14 §14-5：注入真 TradingCalendar 后，窗口端点严格按交易日推算
    （跳过周末），与旧路径（日历日近似）的窗口边界不同。

    构造：trade_date t = 2025-12-31（周三）；真 calendar = [t-380d, t+30d]
    范围内的所有工作日（weekday < 5）作为交易日代理。

    断言：
    - 严格交易日：end = t - 20 trade days；start = end - 252 trade days
    - 由于跳过周末，end 应早于 t - 20 calendar days（更早 ~8 天 = 4 周末×2）
    - start 应早于 end - 252 calendar days（更早 ~100 天 = 50 周末×2）
    - 在 [start, end] 窗口内写满 80 条 IC daily（每个交易日一条）→ snapshot 非 None
    - 用旧路径（calendar=None）的窗口 [t-272d, t-20d] 严格更窄 → sample_size 不同
    """
    import numpy as np

    repo = FactorICRepository()
    t = date(2025, 12, 31)  # 周三

    # 构造真 TradingCalendar：t-380 天到 t+30 天范围内的所有工作日
    cal_start = t - timedelta(days=380)
    cal_end = t + timedelta(days=30)
    weekdays = [
        cal_start + timedelta(days=i)
        for i in range((cal_end - cal_start).days + 1)
        if (cal_start + timedelta(days=i)).weekday() < 5
    ]
    calendar = TradingCalendar(weekdays)

    # 验证窗口端点：严格交易日 vs 日历日近似
    strict_end = calendar.get_prev_trade_date(t, n=20)
    strict_start = calendar.get_prev_trade_date(strict_end, n=252)
    legacy_end = t - timedelta(days=20)
    legacy_start = t - timedelta(days=272)

    # 严格 end ≤ 旧 end - 4天（4 周末×2 = 8 天提前，给点宽容）
    assert strict_end < legacy_end
    assert (legacy_end - strict_end).days >= 7
    # 严格 start ≤ 旧 start - 80 天（50 周末×2 = 100 天提前，给点宽容）
    assert strict_start < legacy_start
    assert (legacy_start - strict_start).days >= 80

    # 用独特前缀避免与其它测试冲突
    strategy = "p14_5_strict_trend"
    factor = "p14_5_strict_ma"
    state = "UPTREND"

    # 在严格窗口 [strict_start, strict_end] 内取前 80 条交易日（密集分布）
    # 注意：strict_start 起点开始累计 80 条 → 落到严格窗口左半部分
    # 旧路径窗口 [t-272d, t-20d] 起点偏右 → 拿到的样本数 < 80（验证窗口差异）
    rng = np.random.default_rng(2026)
    in_window_dates = [
        d for d in weekdays if strict_start <= d <= strict_end
    ][:80]
    rows = [
        ICDailyRow(
            strategy=strategy, factor=factor, state=state,
            trade_date=d,
            ic_value=float(rng.normal(0.05, 0.02)),
            sample_size=200,
        )
        for d in in_window_dates
    ]
    await repo.upsert_ic_daily(db_session, rows)
    await db_session.flush()

    # 严格路径（注入 calendar）
    service_strict = FactorMonitorService(
        session=db_session, engine=FactorMonitorEngine(), calendar=calendar,
    )
    snap_strict = await service_strict.rolling_icir_state(
        session=db_session, trade_date=t,
        strategy=strategy, factor=factor, state=state,
    )
    assert snap_strict is not None
    assert snap_strict.sample_size == len(rows)

    # 旧路径（calendar=None）窗口更窄：[t-272d, t-20d] 比严格窗口少覆盖左端 ~80+ 天
    # 写入的最早行在 strict_start 附近，落到 legacy_start 之外 → sample_size 减少
    service_legacy = FactorMonitorService(
        session=db_session, engine=FactorMonitorEngine(),
    )
    snap_legacy = await service_legacy.rolling_icir_state(
        session=db_session, trade_date=t,
        strategy=strategy, factor=factor, state=state,
    )
    # 旧路径只能看到 legacy_start 之后的行 → 严格少于 strict（覆盖性证伪）
    if snap_legacy is not None:
        assert snap_legacy.sample_size < snap_strict.sample_size
