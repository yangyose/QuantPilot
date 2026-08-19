"""Phase 14 §14-9 日级 IC 生产者 repo 层集成测试 — INT-P14-9-02 + 断点续传往返。

依据 docs/design/phases/phase14_account_integrity.md §11.3.4 / §11.4。

覆盖：
- INT-P14-9-02：daily / aggregate 同 (strategy,factor,state,trade_date) 4-tuple
  ——V1.5-C C0-7（alembic 0025）把 `row_type` 补进唯一键后二者共存，日级观测不再
  被 aggregate 写入吞掉；`get_ic_daily_window` 的 `row_type='daily'` 谓词仍须
  把纯 aggregate 行挡在窗口外。
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
async def test_int_p14_9_02_daily_survives_aggregate_at_same_key(
    db_session: AsyncSession,
) -> None:
    """同 4-tuple 先 daily 后 aggregate → daily 观测仍可取到；纯 aggregate 行则不取。

    **语义变更（V1.5-C C0-7 / alembic 0025）**：本用例原先断言「aggregate 写入后
    该日 daily 观测取不到了」——那是缺陷的表现，§14-9 P2-2 当时只给
    `get_ic_daily_window` 加了 `row_type='daily'` 谓词加固读路径，没治根。
    唯一键补上 `row_type` 后两行共存，日级观测不再丢失。
    `row_type='daily'` 谓词本身仍必须生效（下半段用纯 aggregate 行钉死）。
    """
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

    # 3. 两行共存 → daily 观测仍在窗口内（0025 前这里会是空列表）
    rows_after = await repo.get_ic_daily_window(
        db_session, strategy=_S, factor=_F, state=_STATE,
        start_date=d, end_date=d,
    )
    assert len(rows_after) == 1, "月末日级观测不得被 aggregate 行吞掉"
    assert float(rows_after[0].ic_value) == 0.07

    # 4. row_type='daily' 谓词仍须生效：另一日只写 aggregate → 不进窗口
    d_agg_only = date(2024, 5, 31)
    await repo.upsert_ic_aggregate(
        db_session,
        [ICAggregateRow(strategy=_S, factor=_F, state=_STATE, trade_date=d_agg_only,
                        ic_mean_state=0.05, ic_std_state=0.02, icir=2.5,
                        sample_size=60, ic_ci_low=0.01, ic_ci_high=0.09,
                        t_stat=3.1, half_life=None)],
    )
    await db_session.flush()
    agg_only = await repo.get_ic_daily_window(
        db_session, strategy=_S, factor=_F, state=_STATE,
        start_date=d_agg_only, end_date=d_agg_only,
    )
    assert agg_only == [], "纯 aggregate 行不得混进日级 IC 窗口"


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
