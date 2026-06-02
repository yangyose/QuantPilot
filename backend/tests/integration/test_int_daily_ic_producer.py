"""Phase 14 §14-9 日级 IC 生产者 repo 层集成测试 — INT-P14-9-02 + 断点续传往返。

依据 docs/design/phases/phase14_account_integrity.md §11.3.4 / §11.4。

覆盖：
- INT-P14-9-02：daily / aggregate 同 (strategy,factor,state,trade_date) 4-tuple 碰撞
  （P2-2）——先 upsert_ic_daily 再 upsert_ic_aggregate 把该行升级为 row_type='aggregate'
  但残留 ic_value；get_ic_daily_window 增 row_type='daily' 谓词后不再取到该行。
- get_existing_daily_ic_dates：返回区间内已有 row_type='daily' 的 trade_date 集合
  （供 backfill_daily_ic 断点续传跳过已存在日）。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.factor_ic_repository import (
    FactorICRepository,
    ICAggregateRow,
    ICDailyRow,
)

_S = "p14_9_trend"
_F = "p14_9_trend"
_STATE = "UPTREND"


# ============================================================
# INT-P14-9-02：daily/aggregate 4-tuple 碰撞 → row_type 谓词隔离
# ============================================================
async def test_int_p14_9_02_collision_row_type_daily_predicate(
    db_session: AsyncSession,
) -> None:
    """同 4-tuple 先 daily 后 aggregate 升级 → get_ic_daily_window 不再取到该行。"""
    repo = FactorICRepository()
    d = date(2024, 6, 28)

    # 1. 写 daily 行
    await repo.upsert_ic_daily(
        db_session,
        [ICDailyRow(strategy=_S, factor=_F, state=_STATE,
                    trade_date=d, ic_value=0.07, sample_size=2000)],
    )
    await db_session.flush()
    rows = await repo.get_ic_daily_window(
        db_session, strategy=_S, factor=_F, state=_STATE,
        start_date=d, end_date=d,
    )
    assert len(rows) == 1  # daily 行可取到

    # 2. 同 4-tuple 写 aggregate（month_end == 因子值日 d 的碰撞场景）
    await repo.upsert_ic_aggregate(
        db_session,
        [ICAggregateRow(strategy=_S, factor=_F, state=_STATE, trade_date=d,
                        ic_mean_state=0.05, ic_std_state=0.02, icir=2.5,
                        sample_size=60, ic_ci_low=0.01, ic_ci_high=0.09,
                        t_stat=3.1, half_life=None)],
    )
    await db_session.flush()

    # 3. 该行已升级 row_type='aggregate'，残留 ic_value；
    #    get_ic_daily_window 增 row_type='daily' 谓词后不应再取到（P2-2）
    rows_after = await repo.get_ic_daily_window(
        db_session, strategy=_S, factor=_F, state=_STATE,
        start_date=d, end_date=d,
    )
    assert rows_after == []


# ============================================================
# get_existing_daily_ic_dates：断点续传
# ============================================================
async def test_int_p14_9_get_existing_daily_ic_dates(
    db_session: AsyncSession,
) -> None:
    """返回区间内已有 daily 行的 trade_date 集合；区间外 + aggregate 行不计入。"""
    repo = FactorICRepository()
    d1, d2, d3 = date(2024, 3, 1), date(2024, 3, 4), date(2024, 3, 5)
    d_out = date(2024, 2, 1)
    d_agg = date(2024, 3, 6)

    await repo.upsert_ic_daily(db_session, [
        ICDailyRow(strategy=_S, factor=_F, state=_STATE, trade_date=dd,
                   ic_value=0.03, sample_size=1500)
        for dd in (d1, d2, d3, d_out)
    ])
    # d_agg 只有 aggregate 行（无 daily）→ 不应计入
    await repo.upsert_ic_aggregate(db_session, [
        ICAggregateRow(strategy=_S, factor=_F, state=_STATE, trade_date=d_agg,
                       ic_mean_state=0.05, ic_std_state=0.02, icir=2.5,
                       sample_size=60, ic_ci_low=0.01, ic_ci_high=0.09,
                       t_stat=3.1, half_life=None),
    ])
    await db_session.flush()

    existing = await repo.get_existing_daily_ic_dates(
        db_session, start_date=date(2024, 3, 1), end_date=date(2024, 3, 31),
    )
    assert existing == {d1, d2, d3}
    assert d_out not in existing  # 区间外
    assert d_agg not in existing  # aggregate 行不计入
