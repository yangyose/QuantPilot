"""V1.5-C C0-7：daily 行与 aggregate 行在唯一约束上撞车（INT-C0-05a~h）。

**问题**（2026-08-18 追平期实测挖出）：`factor_ic_window_state` 上存在**全表**唯一
约束 `uq_factor_ic_window_state_skft (strategy, factor, state, trade_date)`——不含
`row_type`。Phase 14 §14-6 加了 partial unique index `... WHERE row_type='aggregate'`
想把两类行分开，但全表约束从未删除，分离因此从未生效。

后果：月末那一天，若当日市场状态恰等于某个已有 aggregate 行的 state，
`upsert_ic_daily` 的 `ON CONFLICT (4 列)` 会命中 aggregate 行，把 daily 的
`ic_value` / `sample_size` 写进去——两行合并成一行，且 `row_type` 保持 'aggregate'。

- 日级 IC 被静默吞掉：`get_ic_daily_window` 按 `row_type='daily'` 过滤（Phase 14
  §14-9 P2-2 只防了读路径，没治根），该日观测对 ICIR 窗口不可见
- aggregate 的 `sample_size` 被 daily 的横截面股票数覆盖 → `check_factor_offline_rules`
  R4（sample_size < 60 连续 3 月）在这些行上失效
- 生产实测 **156 行 / 39 个月末**被污染（2022-05 ~ 2026-03，源自 §14-9 五年回填）

**修复**：唯一键补上 `row_type`（alembic 0025），三处 upsert 的 `index_elements`
同步补列。daily / aggregate / monthly_quality 三类行自此可在同一 4 元组共存。

覆盖：
- INT-C0-05a：先 daily 后 aggregate → 两行共存，各自字段互不污染
- INT-C0-05b：先 aggregate 后 daily → 同上，且 aggregate 的 icir/sample_size 不被改写
- INT-C0-05c：同键存在 aggregate 时，`get_ic_daily_window` 仍能读到 daily 观测
- INT-C0-05d：daily 幂等——同键重复 upsert 仍只有一行 daily，ic_value 被刷新
- INT-C0-05e：aggregate 幂等——同上
- INT-C0-05f：存量拆分（生产形态：aggregate 最后写，日级 sample_size 已丢 → 写 0）
- INT-C0-05g：存量拆分（本地形态：daily 最后写，日级 sample_size 精确保留）
- INT-C0-05h：拆分脚本幂等——重跑不新增行、不改坏已修好的 daily 行
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.factor_ic_repository import (
    FactorICRepository,
    ICAggregateRow,
    ICDailyRow,
)
from quantpilot.models.business import FactorICWindowState
from scripts.repair_ic_row_type_collision import (  # noqa: E402
    _load_polluted,
    split_polluted_rows,
)

_STRATEGY = "momentum"
_FACTOR = "momentum"
_STATE = "OSCILLATION"
_DAY = date(2026, 6, 30)   # 月末：aggregate 与 daily 天然同键的那一天


def _daily_row(ic: float = 0.0421, n: int = 2969) -> ICDailyRow:
    return ICDailyRow(
        strategy=_STRATEGY, factor=_FACTOR, state=_STATE,
        trade_date=_DAY, ic_value=ic, sample_size=n,
    )


def _aggregate_row(icir: float = -0.7759, n: int = 166) -> ICAggregateRow:
    return ICAggregateRow(
        strategy=_STRATEGY, factor=_FACTOR, state=_STATE, trade_date=_DAY,
        ic_mean_state=-0.0182, ic_std_state=0.0235, icir=icir, sample_size=n,
        ic_ci_low=-0.0301, ic_ci_high=-0.0064, t_stat=-9.9987, half_life=None,
    )


async def _rows_at_key(session: AsyncSession) -> list[FactorICWindowState]:
    result = await session.execute(
        select(FactorICWindowState)
        .where(
            FactorICWindowState.strategy == _STRATEGY,
            FactorICWindowState.factor == _FACTOR,
            FactorICWindowState.state == _STATE,
            FactorICWindowState.trade_date == _DAY,
        )
        .order_by(FactorICWindowState.row_type)
    )
    return list(result.scalars().all())


# ============================================================
# INT-C0-05a：先 daily 后 aggregate
# ============================================================


async def test_int_c0_05a_daily_then_aggregate_coexist(db_session: AsyncSession) -> None:
    repo = FactorICRepository()
    await repo.upsert_ic_daily(db_session, [_daily_row()])
    await repo.upsert_ic_aggregate(db_session, [_aggregate_row()])
    await db_session.flush()

    rows = await _rows_at_key(db_session)
    assert len(rows) == 2, f"daily 与 aggregate 必须共存，实际 {len(rows)} 行"
    agg, daily = rows[0], rows[1]
    assert agg.row_type == "aggregate"
    assert daily.row_type == "daily"

    # daily 行保留自己的 ic_value / 横截面样本数
    assert float(daily.ic_value) == 0.0421
    assert daily.sample_size == 2969
    # aggregate 行保留自己的 icir / 窗口观测数，且不该被塞进 daily 的 ic_value
    assert float(agg.icir) == -0.7759
    assert agg.sample_size == 166
    assert agg.ic_value is None, "aggregate 行不应携带日级 ic_value"


# ============================================================
# INT-C0-05b：先 aggregate 后 daily（生产实际发生的顺序）
# ============================================================


async def test_int_c0_05b_aggregate_then_daily_coexist(db_session: AsyncSession) -> None:
    repo = FactorICRepository()
    await repo.upsert_ic_aggregate(db_session, [_aggregate_row()])
    await repo.upsert_ic_daily(db_session, [_daily_row()])
    await db_session.flush()

    rows = await _rows_at_key(db_session)
    assert len(rows) == 2, f"daily 写入不得吞掉 aggregate 行，实际 {len(rows)} 行"
    agg, daily = rows[0], rows[1]
    assert agg.row_type == "aggregate"
    assert daily.row_type == "daily"
    # 这是生产上真实发生的污染：aggregate 的 sample_size 被横截面股票数覆盖
    assert agg.sample_size == 166, "aggregate 的窗口观测数被 daily 覆盖"
    assert float(agg.icir) == -0.7759
    assert agg.ic_value is None
    assert float(daily.ic_value) == 0.0421
    assert daily.sample_size == 2969


# ============================================================
# INT-C0-05c：同键有 aggregate 时，daily 观测仍进 ICIR 窗口
# ============================================================


async def test_int_c0_05c_daily_visible_to_icir_window(db_session: AsyncSession) -> None:
    repo = FactorICRepository()
    await repo.upsert_ic_aggregate(db_session, [_aggregate_row()])
    await repo.upsert_ic_daily(db_session, [_daily_row()])
    await db_session.flush()

    window = await repo.get_ic_daily_window(
        db_session,
        strategy=_STRATEGY, factor=_FACTOR, state=_STATE,
        start_date=date(2026, 6, 1), end_date=date(2026, 7, 1),
    )
    values = [float(r.ic_value) for r in window if r.ic_value is not None]
    assert values == [0.0421], (
        "月末日级观测被 aggregate 行吞掉 → ICIR 窗口每月少一个样本"
    )


# ============================================================
# INT-C0-05d/e：各自幂等（补 row_type 后 ON CONFLICT 仍须命中同类行）
# ============================================================


async def test_int_c0_05d_daily_upsert_idempotent(db_session: AsyncSession) -> None:
    repo = FactorICRepository()
    await repo.upsert_ic_daily(db_session, [_daily_row(ic=0.0421)])
    await repo.upsert_ic_daily(db_session, [_daily_row(ic=0.0999, n=2970)])
    await db_session.flush()

    rows = await _rows_at_key(db_session)
    assert len(rows) == 1
    assert rows[0].row_type == "daily"
    assert float(rows[0].ic_value) == 0.0999, "同键重复 upsert 应覆盖而非新增"
    assert rows[0].sample_size == 2970


async def test_int_c0_05e_aggregate_upsert_idempotent(db_session: AsyncSession) -> None:
    repo = FactorICRepository()
    await repo.upsert_ic_aggregate(db_session, [_aggregate_row(icir=-0.7759)])
    await repo.upsert_ic_aggregate(db_session, [_aggregate_row(icir=-0.5000, n=180)])
    await db_session.flush()

    rows = await _rows_at_key(db_session)
    assert len(rows) == 1
    assert rows[0].row_type == "aggregate"
    assert float(rows[0].icir) == -0.5000
    assert rows[0].sample_size == 180


# ============================================================
# INT-C0-05f/g：存量污染行的拆分修复（scripts/repair_ic_row_type_collision.py）
# ============================================================
#
# 生产上两个方向都存在（谁最后写决定哪半边幸存）：
#   - aggregate 最后写（生产 156 行）：daily.ic_value 残留、daily.sample_size 丢失
#   - daily 最后写（本地 2026-06-30）：daily 两列完好、aggregate.sample_size 被覆盖


async def _insert_merged_row(
    session: AsyncSession, *, ic: float, sample_size: int,
) -> None:
    """构造一条「合并态」行：row_type='aggregate' 却带着日级 ic_value。"""
    await session.execute(text(
        "INSERT INTO factor_ic_window_state "
        "(strategy, factor, state, trade_date, ic_value, icir, ic_mean_state, "
        " ic_std_state, t_stat, sample_size, row_type) "
        "VALUES (:s, :f, :st, :d, :ic, -0.7759, -0.0182, 0.0235, -9.9987, :n, 'aggregate')"
    ), {
        "s": _STRATEGY, "f": _FACTOR, "st": _STATE, "d": _DAY,
        "ic": ic, "n": sample_size,
    })
    await session.flush()


async def test_int_c0_05f_repair_splits_when_daily_sample_size_lost(
    db_session: AsyncSession,
) -> None:
    """生产形态：aggregate 最后写 → sample_size 是窗口观测数（≤252），日级值已丢。"""
    await _insert_merged_row(db_session, ic=0.0421, sample_size=166)

    polluted = await _load_polluted(db_session)
    mine = [p for p in polluted if p.trade_date == _DAY and p.strategy == _STRATEGY]
    assert len(mine) == 1
    assert mine[0].daily_sample_size_known is False, "166 ≤ 300 应判定为 aggregate 的值"

    await split_polluted_rows(db_session, mine)
    await db_session.flush()

    rows = await _rows_at_key(db_session)
    assert len(rows) == 2
    agg, daily = rows[0], rows[1]
    assert agg.row_type == "aggregate"
    assert agg.ic_value is None, "aggregate 行的 ic_value 必须清空"
    assert agg.sample_size == 166, "aggregate 统计列不得被本脚本改动"
    assert float(agg.icir) == -0.7759
    assert daily.row_type == "daily"
    assert float(daily.ic_value) == 0.0421, "日级 ic_value 精确还原"
    assert daily.sample_size == 0, "日级 sample_size 不可还原 → 写 0（【降级说明】）"


async def test_int_c0_05g_repair_preserves_known_daily_sample_size(
    db_session: AsyncSession,
) -> None:
    """本地形态：daily 最后写 → sample_size 是横截面股票数（>300），可精确保留。"""
    await _insert_merged_row(db_session, ic=0.0421, sample_size=2969)

    polluted = await _load_polluted(db_session)
    mine = [p for p in polluted if p.trade_date == _DAY and p.strategy == _STRATEGY]
    assert mine[0].daily_sample_size_known is True

    await split_polluted_rows(db_session, mine)
    await db_session.flush()

    rows = await _rows_at_key(db_session)
    assert len(rows) == 2
    daily = rows[1]
    assert daily.row_type == "daily"
    assert daily.sample_size == 2969, "日级横截面样本数应原样保留"
    assert rows[0].ic_value is None


async def test_int_c0_05h_repair_is_idempotent(db_session: AsyncSession) -> None:
    """重复跑不得产生重复行，也不得把已修好的 daily 行改坏。"""
    await _insert_merged_row(db_session, ic=0.0421, sample_size=2969)
    polluted = await _load_polluted(db_session)
    mine = [p for p in polluted if p.trade_date == _DAY and p.strategy == _STRATEGY]

    await split_polluted_rows(db_session, mine)
    await db_session.flush()
    # 第二轮：指纹已消失（ic_value 被清空）→ 无可修对象
    again = await _load_polluted(db_session)
    assert not [p for p in again if p.trade_date == _DAY and p.strategy == _STRATEGY]

    # 即便强行再拆一次同样的输入，也只是覆盖写，不新增行
    await split_polluted_rows(db_session, mine)
    await db_session.flush()
    assert len(await _rows_at_key(db_session)) == 2
