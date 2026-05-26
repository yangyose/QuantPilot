"""Phase 14 §14-6 INT-P14-6-01 / INT-P14-6-02：factor_ic_window_state.row_type
共表拆分方案 A 集成测试。

依据 docs/design/phases/phase14_account_integrity.md §8.3。

覆盖：
- INT-P14-6-01：upsert_ic_daily 写 row_type='daily'；upsert_ic_aggregate 写
  row_type='aggregate'；aggregate 升级 daily 行（同 4-tuple）→ row_type 变 aggregate
- INT-P14-6-02：partial unique index uq_factor_ic_window_state_aggregate 在
  row_type='aggregate' 上强制 4-tuple 唯一（upsert 路径走 on_conflict_do_update，
  不抛 IntegrityError，但行数仍为 1）；daily 行不受 partial 约束
- INT-P14-6-03：get_recent_aggregates / list_aggregates / get_latest_icir
  改用 row_type='aggregate' 过滤，正确返回新 daily 行不混入
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.factor_ic_repository import (
    FactorICRepository,
    ICAggregateRow,
    ICDailyRow,
)
from quantpilot.models.business import FactorICWindowState

_STRATEGY = "p14_6_trend"
_FACTOR = "p14_6_macd"
_STATE = "UPTREND"


async def _count_rows(
    session: AsyncSession,
    *,
    strategy: str = _STRATEGY,
    factor: str = _FACTOR,
    row_type: str | None = None,
) -> int:
    stmt = select(func.count()).select_from(FactorICWindowState).where(
        FactorICWindowState.strategy == strategy,
        FactorICWindowState.factor == factor,
    )
    if row_type is not None:
        stmt = stmt.where(FactorICWindowState.row_type == row_type)
    return (await session.execute(stmt)).scalar() or 0


# ============================================================
# INT-P14-6-01：daily / aggregate 写入分别标 row_type
# ============================================================
async def test_int_p14_6_01_daily_upsert_writes_row_type_daily(
    db_session: AsyncSession,
) -> None:
    """upsert_ic_daily 写入 row_type='daily'。"""
    repo = FactorICRepository()
    daily_rows = [
        ICDailyRow(
            strategy=_STRATEGY, factor=_FACTOR, state=_STATE,
            trade_date=date(2025, 6, 1) + timedelta(days=i),
            ic_value=0.04 + i * 0.001, sample_size=200,
        )
        for i in range(5)
    ]
    inserted = await repo.upsert_ic_daily(db_session, daily_rows)
    await db_session.flush()
    assert inserted == 5

    # 全部 row_type='daily'
    assert await _count_rows(db_session, row_type="daily") == 5
    assert await _count_rows(db_session, row_type="aggregate") == 0


async def test_int_p14_6_01b_aggregate_upsert_writes_row_type_aggregate(
    db_session: AsyncSession,
) -> None:
    """upsert_ic_aggregate 写入 row_type='aggregate'。"""
    repo = FactorICRepository()
    agg_rows = [
        ICAggregateRow(
            strategy=_STRATEGY, factor=_FACTOR, state=_STATE,
            trade_date=date(2025, 6, 30),
            ic_mean_state=0.05, ic_std_state=0.02, icir=2.5,
            sample_size=80, ic_ci_low=-0.01, ic_ci_high=0.11,
            t_stat=22.3, half_life=10,
        ),
    ]
    inserted = await repo.upsert_ic_aggregate(db_session, agg_rows)
    await db_session.flush()
    assert inserted == 1

    assert await _count_rows(db_session, row_type="aggregate") == 1
    assert await _count_rows(db_session, row_type="daily") == 0


async def test_int_p14_6_01c_aggregate_upgrades_existing_daily(
    db_session: AsyncSession,
) -> None:
    """同 4-tuple 先写 daily 后写 aggregate → 单行升级到 row_type='aggregate'。

    这是月末 batch 路径的典型行为：当天先写 daily IC 值，再批量回算 aggregate。
    """
    repo = FactorICRepository()
    t = date(2025, 7, 15)
    # 1. 先写 daily 行
    await repo.upsert_ic_daily(db_session, [ICDailyRow(
        strategy=_STRATEGY, factor=_FACTOR, state=_STATE,
        trade_date=t, ic_value=0.06, sample_size=200,
    )])
    await db_session.flush()
    assert await _count_rows(db_session, row_type="daily") == 1
    assert await _count_rows(db_session, row_type="aggregate") == 0

    # 2. 再写 aggregate 行（同 4-tuple）→ on_conflict_do_update 升级 row_type
    await repo.upsert_ic_aggregate(db_session, [ICAggregateRow(
        strategy=_STRATEGY, factor=_FACTOR, state=_STATE,
        trade_date=t, ic_mean_state=0.05, ic_std_state=0.02, icir=2.5,
        sample_size=80, ic_ci_low=-0.01, ic_ci_high=0.11,
        t_stat=22.3, half_life=10,
    )])
    await db_session.flush()
    # 总行数仍为 1（同 4-tuple 受 UNIQUE 约束）
    assert await _count_rows(db_session) == 1
    # row_type 已升级为 aggregate
    assert await _count_rows(db_session, row_type="aggregate") == 1
    assert await _count_rows(db_session, row_type="daily") == 0


# ============================================================
# INT-P14-6-02：partial unique index 行为 + 全表 UNIQUE 行为
# ============================================================
async def test_int_p14_6_02_aggregate_partial_unique_prevents_duplicate(
    db_session: AsyncSession,
) -> None:
    """同 4-tuple 重复 upsert aggregate → 走 on_conflict_do_update，
    不抛 IntegrityError，但行数始终为 1（partial unique + 全表 UNIQUE 共同保护）。"""
    repo = FactorICRepository()
    t = date(2025, 8, 1)
    agg = ICAggregateRow(
        strategy=_STRATEGY, factor=_FACTOR, state=_STATE,
        trade_date=t, ic_mean_state=0.05, ic_std_state=0.02, icir=2.5,
        sample_size=80, ic_ci_low=-0.01, ic_ci_high=0.11,
        t_stat=22.3, half_life=10,
    )
    await repo.upsert_ic_aggregate(db_session, [agg])
    await db_session.flush()
    assert await _count_rows(db_session, row_type="aggregate") == 1

    # 第二次同 4-tuple upsert → 仍 1 行（覆盖 ic_mean_state 等）
    agg2 = ICAggregateRow(
        strategy=_STRATEGY, factor=_FACTOR, state=_STATE,
        trade_date=t, ic_mean_state=0.07, ic_std_state=0.03, icir=2.0,
        sample_size=90, ic_ci_low=0.0, ic_ci_high=0.14,
        t_stat=20.0, half_life=12,
    )
    await repo.upsert_ic_aggregate(db_session, [agg2])
    await db_session.flush()
    assert await _count_rows(db_session, row_type="aggregate") == 1

    # 行内容是 agg2 覆盖后的值
    row = (
        await db_session.execute(
            select(FactorICWindowState).where(
                FactorICWindowState.strategy == _STRATEGY,
                FactorICWindowState.factor == _FACTOR,
                FactorICWindowState.trade_date == t,
            )
        )
    ).scalar_one()
    assert float(row.ic_mean_state) == 0.07
    assert int(row.sample_size) == 90


async def test_int_p14_6_02b_daily_rows_distinct_trade_dates_coexist(
    db_session: AsyncSession,
) -> None:
    """daily 行不同 trade_date 自由共存（partial 不约束 daily）。"""
    repo = FactorICRepository()
    rows = [
        ICDailyRow(
            strategy=_STRATEGY, factor=_FACTOR, state=_STATE,
            trade_date=date(2025, 8, 1) + timedelta(days=i),
            ic_value=0.05, sample_size=200,
        )
        for i in range(10)
    ]
    await repo.upsert_ic_daily(db_session, rows)
    await db_session.flush()
    assert await _count_rows(db_session, row_type="daily") == 10


# ============================================================
# INT-P14-6-03：查询过滤改 row_type='aggregate' 正确性
# ============================================================
async def test_int_p14_6_03_get_recent_aggregates_filters_row_type(
    db_session: AsyncSession,
) -> None:
    """种 5 daily + 3 aggregate 行 → get_recent_aggregates 只返回 3 aggregate 行。

    Phase 14 §14-6：过滤改 row_type='aggregate' 替代 icir IS NOT NULL；
    若 daily 行的 ic_value 与 aggregate 行的 icir 在历史数据中混叠（如 0010
    升级前），此测试保证查询不再误读 daily 行。
    """
    repo = FactorICRepository()
    # 5 daily 行（trade_date 在前）
    daily_rows = [
        ICDailyRow(
            strategy=_STRATEGY, factor=_FACTOR, state=_STATE,
            trade_date=date(2025, 9, 1) + timedelta(days=i),
            ic_value=0.04 + i * 0.001, sample_size=200,
        )
        for i in range(5)
    ]
    await repo.upsert_ic_daily(db_session, daily_rows)
    # 3 aggregate 行（trade_date 在后，不与 daily 冲突）
    agg_rows = [
        ICAggregateRow(
            strategy=_STRATEGY, factor=_FACTOR, state=_STATE,
            trade_date=date(2025, 10, 1) + timedelta(days=i),
            ic_mean_state=0.05, ic_std_state=0.02, icir=2.5,
            sample_size=80, ic_ci_low=-0.01, ic_ci_high=0.11,
            t_stat=22.3, half_life=10,
        )
        for i in range(3)
    ]
    await repo.upsert_ic_aggregate(db_session, agg_rows)
    await db_session.flush()

    recent = await repo.get_recent_aggregates(
        db_session, strategy=_STRATEGY, factor=_FACTOR, state=_STATE,
        as_of=date(2025, 12, 31), limit=10,
    )
    assert len(recent) == 3
    for row in recent:
        assert row.row_type == "aggregate"
        assert row.icir is not None
