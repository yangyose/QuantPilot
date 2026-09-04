"""INT-FPS-01~05：`factor_panel_stat` 写库方（V1.5-K K-6）。

## 缺口

K-6 的建表（ORM + alembic 0026）与计算（`engine/diagnostics/factor_ic.py`）都已入仓，
但**没有任何写库方**——算出来的 `PanelStatPoint` 落不了地。这正是
CLAUDE.md §4.11「接了但没生效」的前兆形态：机制齐了，链条断在最后一环。

## 为什么必须分批（本文件最重要的一条）

`factor_panel_stat` 共 **11 个业务列**，asyncpg 二进制协议的 16-bit 占位符上限是
**32767** → 单条 SQL 最多 `32767 / 11 = 2978` 行。而面板重跑一次要写
497 交易日 × 4 策略 × 多因子 × 2 stage × 4 horizon × 2 metric，轻易上十万行。

⚠️ **这个缺陷合成小数据测不出来**：写 100 行、写 1000 行都能过，只有 **≥ 2979 行**
才触发。CLAUDE.md §4.1 明确记着「需 ≥ 3000 行场景才抓得到」，故本文件专门用
3200 行钉它——不是为了压测，是因为少于这个数就是假绿。
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.diagnostics.factor_ic import PanelStatPoint
from quantpilot.models.business import FactorPanelStat

_RUN = "int-fps-test"
_DAY = date(2026, 5, 12)


def _point(i: int, **kw) -> PanelStatPoint:
    base = dict(
        strategy="momentum", factor=f"f{i:04d}", stage="raw", state="ALL",
        horizon=20, metric="ic", value=0.01 * (i % 7), sample_size=100 + i,
    )
    base.update(kw)
    return PanelStatPoint(**base)  # type: ignore[arg-type]


async def _count(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(FactorPanelStat)
                .where(FactorPanelStat.panel_run == _RUN)
            )
        ).scalar_one()
    )


async def _cleanup(session: AsyncSession) -> None:
    for row in (
        await session.execute(
            select(FactorPanelStat).where(FactorPanelStat.panel_run == _RUN)
        )
    ).scalars().all():
        await session.delete(row)
    await session.flush()


# ============================================================
# INT-FPS-01：≥ 2979 行必须写成功（分批守卫）
# ============================================================
async def test_int_fps_01_writes_beyond_placeholder_limit(
    db_session: AsyncSession,
) -> None:
    """3200 行 × 11 列 = 35,200 占位符 > 32767 → 不分批必崩。

    ⚠️ 把行数调到 2000 这条照样绿——**少于 2979 就是假绿**。
    改这个数字前先读本文件 docstring。
    """
    repo = MarketDataRepository(db_session)
    points = [_point(i) for i in range(3200)]
    try:
        n = await repo.upsert_factor_panel_stat_bulk(_RUN, _DAY, points)
        assert n == 3200
        assert await _count(db_session) == 3200
    finally:
        await _cleanup(db_session)


# ============================================================
# INT-FPS-02：九元组唯一键 → 同键重写覆盖而非报错/重复
# ============================================================
async def test_int_fps_02_upsert_overwrites_same_key(
    db_session: AsyncSession,
) -> None:
    """整批重跑要能覆盖上一次同批次同键的值，否则重跑就撞唯一键。"""
    repo = MarketDataRepository(db_session)
    try:
        await repo.upsert_factor_panel_stat_bulk(_RUN, _DAY, [_point(1, value=0.5)])
        await repo.upsert_factor_panel_stat_bulk(_RUN, _DAY, [_point(1, value=-0.9)])
        assert await _count(db_session) == 1
        row = (
            await db_session.execute(
                select(FactorPanelStat).where(FactorPanelStat.panel_run == _RUN)
            )
        ).scalar_one()
        assert float(row.value) == pytest.approx(-0.9)
    finally:
        await _cleanup(db_session)


# ============================================================
# INT-FPS-03：九个维度任一不同即为不同行
# ============================================================
async def test_int_fps_03_all_nine_dimensions_discriminate(
    db_session: AsyncSession,
) -> None:
    """少一个维度参与唯一键，就会有合法的不同行互相覆盖——静默丢数据。

    逐个维度各造一行「只有该维度不同」的点，全部写进去后行数必须等于点数。
    """
    repo = MarketDataRepository(db_session)
    variants = [
        _point(1),
        _point(1, strategy="value"),
        _point(1, factor="other"),
        _point(1, stage="z"),
        _point(1, state="UPTREND"),
        _point(1, horizon=5),
        _point(1, metric="valid_ratio"),
        _point(1, bucket=3),
    ]
    try:
        n = await repo.upsert_factor_panel_stat_bulk(_RUN, _DAY, variants)
        assert n == len(variants)
        assert await _count(db_session) == len(variants), "有维度未参与唯一键"
    finally:
        await _cleanup(db_session)


# ============================================================
# INT-FPS-04：panel_run / trade_date 由写库方统一填
# ============================================================
async def test_int_fps_04_batch_dimensions_are_stamped(
    db_session: AsyncSession,
) -> None:
    """`PanelStatPoint` 刻意不带这两个字段（属批次维度，不属单点计算）。

    若写库方忘了填，它们会是 NULL 而列是 NOT NULL → 直接崩；
    但更糟的情况是填错日期，故这里显式核对值。
    """
    repo = MarketDataRepository(db_session)
    try:
        await repo.upsert_factor_panel_stat_bulk(_RUN, _DAY, [_point(1)])
        row = (
            await db_session.execute(
                select(FactorPanelStat).where(FactorPanelStat.panel_run == _RUN)
            )
        ).scalar_one()
        assert row.panel_run == _RUN
        assert row.trade_date == _DAY
        assert row.bucket == -1, "默认 bucket 应为 -1（不适用），不是 NULL"
    finally:
        await _cleanup(db_session)


# ============================================================
# INT-FPS-05：value 可为 None（指标算不出），不得当成 0
# ============================================================
async def test_int_fps_05_none_value_persists_as_null(
    db_session: AsyncSession,
) -> None:
    """NaN/None 必须落成 NULL 而非 0——0 意味着「算出来就是 0」。

    与 §4.1 那条「NaN 经 to_dict 会变 float('nan')、asyncpg 原样写成 NUMERIC 'NaN'
    （≠ NULL）」是同一族陷阱：下游 IS NOT NULL 会误判。
    """
    repo = MarketDataRepository(db_session)
    try:
        await repo.upsert_factor_panel_stat_bulk(
            _RUN, _DAY,
            [_point(1, value=None), _point(2, value=float("nan"))],
        )
        rows = (
            await db_session.execute(
                select(FactorPanelStat).where(FactorPanelStat.panel_run == _RUN)
            )
        ).scalars().all()
        assert len(rows) == 2
        assert all(r.value is None for r in rows), (
            f"None/NaN 必须落成 NULL，实得 {[r.value for r in rows]}"
        )
    finally:
        await _cleanup(db_session)


# ============================================================
# INT-FPS-06：空输入不发 SQL
# ============================================================
async def test_int_fps_06_empty_is_noop(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    assert await repo.upsert_factor_panel_stat_bulk(_RUN, _DAY, []) == 0
