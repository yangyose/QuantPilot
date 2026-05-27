"""Phase 14 §14-2.3：INT-P14-2-01 / INT-P14-2-02 集成测试。

依据 docs/design/phases/phase14_account_integrity.md §4.3：
- INT-P14-2-01：5 个 month_end 顺次调 `apply_monthly_rebalance` →
  `factor_ic_window_state` 写入 60 aggregate 行（3 state × 4 strategy × 5 月）+
  `strategy_weights_history` 写入 60 行
- INT-P14-2-02：`get_existing_candidate_pool_dates` 返回 set[date] 与查询区间一致

Note：设计 §4.3 把 INT-P14-2-01 描述为"通过脚本入口运行"，但脚本入口 (
`_run_one_month`) 内部新建 AsyncSessionLocal，与 db_session fixture 的事务回滚
模型不兼容（会污染测试 DB）。本测试改为直接调 `apply_monthly_rebalance` 5 次，
等价覆盖脚本的核心契约——脚本的 per-month 循环仅是简单 wrapper。
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.factor_ic_repository import FactorICRepository, ICDailyRow
from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.models.business import (
    CandidatePool,
    FactorICWindowState,
    StrategyWeightsHistory,
)
from quantpilot.services.factor_monitor_service import FactorMonitorService

_MONTH_ENDS = [
    date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31),
    date(2025, 4, 30), date(2025, 5, 30),  # 5/31 是周六，取 5/30 周五
]
_STRATEGIES = ("trend", "momentum", "mean_reversion", "value")
_STATES = ("UPTREND", "DOWNTREND", "OSCILLATION")


async def _seed_daily_ic(
    session: AsyncSession, latest_month_end: date,
) -> None:
    """在 [latest_month_end - 600d, latest_month_end - 20d] 区间逐日 seed IC_daily，
    覆盖 4 strategy × 3 state × ~580 行 = ~6960 daily 行。

    保证每个 month_end（5 个月窗口跨 2025-01 ~ 2025-05）的 ICIR 滚动窗口
    [t - 272d, t - 20d] 内有 ≥60 个 sample（rolling_icir_state state_min_samples）。
    """
    repo = FactorICRepository()
    rng = np.random.default_rng(42)
    means = {"trend": 0.06, "momentum": 0.04, "mean_reversion": 0.03, "value": 0.02}
    span_days = 600
    lag_days = 20
    rows: list[ICDailyRow] = []
    for strategy in _STRATEGIES:
        for state in _STATES:
            for i in range(span_days - lag_days):
                td = latest_month_end - timedelta(days=lag_days + i)
                ic = float(rng.normal(means[strategy], 0.02))
                rows.append(ICDailyRow(
                    strategy=strategy, factor=strategy, state=state,
                    trade_date=td, ic_value=ic, sample_size=200,
                ))
    # 6960 行远低于 asyncpg 32767 占位符限制（6960 × 6 列 = 41760 不安全！）
    # 安全：分批 upsert，每批 2000 行 × 6 列 = 12000 < 32767
    BATCH = 2000
    for i in range(0, len(rows), BATCH):
        await repo.upsert_ic_daily(session, rows[i:i + BATCH])
    await session.flush()


# ============================================================
# INT-P14-2-01：5 个 month_end × 12 行 = 60 行 aggregate
# ============================================================
async def test_int_p14_2_01_5_month_ends_yield_60_aggregate_rows(
    db_session: AsyncSession,
) -> None:
    """5 个 month_end 顺次 apply_monthly_rebalance → factor_ic_window_state
    aggregate 行 = 60（4 strategy × 3 state × 5 月）；strategy_weights_history
    行 = 60（同维度）。"""
    # 1. seed daily IC（足够覆盖每个 month_end 的 ICIR 计算窗口）
    await _seed_daily_ic(db_session, latest_month_end=_MONTH_ENDS[-1])

    service = FactorMonitorService(
        session=db_session, engine=FactorMonitorEngine(),
    )

    # 2. 逐月跑 apply_monthly_rebalance
    for me in _MONTH_ENDS:
        result = await service.apply_monthly_rebalance(db_session, me)
        await db_session.flush()
        # 每月每 state 应有 4 行 strategy_weights
        for state in _STATES:
            assert len(result[state]) == 4

    # 3. 断言 factor_ic_window_state aggregate 行数
    agg_count = (
        await db_session.execute(
            select(func.count()).select_from(FactorICWindowState).where(
                FactorICWindowState.row_type == "aggregate",
                FactorICWindowState.trade_date.in_(_MONTH_ENDS),
            )
        )
    ).scalar() or 0
    assert agg_count == 60, (
        f"expected 60 aggregate rows (4 strategy × 3 state × 5 month), got {agg_count}"
    )

    # 4. 断言 strategy_weights_history 写入 60 行（effective_date = month_end + 1d）
    effective_dates = [me + timedelta(days=1) for me in _MONTH_ENDS]
    sw_count = (
        await db_session.execute(
            select(func.count()).select_from(StrategyWeightsHistory).where(
                StrategyWeightsHistory.trade_date.in_(effective_dates),
            )
        )
    ).scalar() or 0
    assert sw_count == 60, (
        f"expected 60 strategy_weights_history rows, got {sw_count}"
    )


# ============================================================
# INT-P14-2-02：get_existing_candidate_pool_dates 单表查询
# ============================================================
async def test_int_p14_2_02_get_existing_candidate_pool_dates_returns_distinct_set(
    db_session: AsyncSession,
) -> None:
    """种 3 个 trade_date × 2 ts_code candidate_pool 行（每日 2 行；其中一日 in_pool=False
    fade-out），repo.get_existing_candidate_pool_dates 返回 3 个 distinct trade_date 集合。
    """
    repo = MarketDataRepository(db_session)
    trade_dates = [date(2025, 7, 1), date(2025, 7, 2), date(2025, 7, 3)]
    # 用 upsert_candidate_pool 写入：第 1 日 1 in_pool=True；第 2 日 1 fade-out（in_pool=False）；
    # 第 3 日 1 in_pool=True。
    for td in trade_dates:
        await repo.upsert_candidate_pool(
            ts_code="000001.SZ", trade_date=td,
            composite_score=80.0, trend_score=70.0,
            momentum_score=60.0, reversion_score=50.0, value_score=40.0,
            market_state="UPTREND",
            in_pool=(td != date(2025, 7, 2)),  # 2 号是 fade-out
            is_holding=False,
        )
        await repo.upsert_candidate_pool(
            ts_code="000002.SZ", trade_date=td,
            composite_score=75.0, trend_score=65.0,
            momentum_score=55.0, reversion_score=45.0, value_score=35.0,
            market_state="UPTREND", in_pool=True, is_holding=False,
        )
    await db_session.flush()

    existing = await repo.get_existing_candidate_pool_dates(
        date(2025, 7, 1), date(2025, 7, 3),
    )
    assert existing == set(trade_dates), (
        f"expected {set(trade_dates)}, got {existing}"
    )

    # 缩小查询窗口（仅含中间一日 fade-out）：仍返回该 trade_date
    narrow = await repo.get_existing_candidate_pool_dates(
        date(2025, 7, 2), date(2025, 7, 2),
    )
    assert narrow == {date(2025, 7, 2)}

    # 查询窗口完全外侧：返回空集
    empty = await repo.get_existing_candidate_pool_dates(
        date(2025, 8, 1), date(2025, 8, 31),
    )
    assert empty == set()

    # cleanup（避免污染 candidate_pool 表后续测试）：db_session 已配置 rollback fixture，
    # 但本测试中 db_session.flush() 写入仍在事务内，rollback 后会回滚——无需手动 DELETE
    _ = CandidatePool  # 引用避免 unused import
