"""AttributionRepository：Phase 12 多因子归因历史 CRUD（§3.2.2 + §5.2）。

无状态构造（参考 FactorICRepository 风格）：所有方法显式接收 session。

Phase 14 §14-8 注（R12-P2-3 防御）：``upsert_attribution`` 当前不分批。asyncpg
PostgreSQL 协议单 SQL 占位符上限 32767（见 [[feedback_asyncpg_param_limit]]）。
本 repo 单行 8 列（calc_date/factor/beta/t_stat/residual_std/r_squared/sample_size/
window_days），32767 / 8 ≈ 4095 行单 SQL 安全上限。

V1.0 调用面：
  - AttributionService.run_monthly：单月 4 行（4 strategy_z 因子），完全无虞
  - §14-8 scripts/backfill_attribution_history.py 5y 回填：单月独立调一次
    run_monthly，每次仍 4 行，不会聚合成单 SQL
  - §14-2 5y × 60 month_end × 4 = 1200 行（理论最大），仍未到 4095

未来防御：若批量 panel upsert 引入（如全网整体回算），单次 ≥ 4000 行就需切
``_BATCH_SIZE = 500``（同 ``MarketDataRepository`` 模式：循环 `pg_insert(...).values(batch)`）。
不要假设"未来不会涨"——RM-12 真机验收（2026-05-11）就因 ``data/repository.py`` 3
处 upsert 漏分批被 ≥ 3000 行场景抓到，单元测试 mock 永远绕不开此约束。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.business import AttributionHistory


@dataclass(frozen=True)
class AttributionRow:
    """单条归因记录（一次 run_ols 一个 factor 一行）。"""

    calc_date: date
    factor: str
    beta: float
    t_stat: float | None
    residual_std: float | None
    r_squared: float | None
    sample_size: int
    window_days: int = 20


class AttributionRepository:
    """attribution_history 表 CRUD。"""

    async def upsert_attribution(
        self, session: AsyncSession, rows: list[AttributionRow],
    ) -> int:
        """批量 upsert；ON CONFLICT (calc_date, factor) DO UPDATE 全字段刷写。"""
        if not rows:
            return 0
        values = [
            {
                "calc_date": r.calc_date,
                "factor": r.factor,
                "beta": r.beta,
                "t_stat": r.t_stat,
                "residual_std": r.residual_std,
                "r_squared": r.r_squared,
                "sample_size": r.sample_size,
                "window_days": r.window_days,
            }
            for r in rows
        ]
        stmt = pg_insert(AttributionHistory).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["calc_date", "factor"],
            set_={
                "beta": stmt.excluded.beta,
                "t_stat": stmt.excluded.t_stat,
                "residual_std": stmt.excluded.residual_std,
                "r_squared": stmt.excluded.r_squared,
                "sample_size": stmt.excluded.sample_size,
                "window_days": stmt.excluded.window_days,
            },
        )
        result = await session.execute(stmt)
        return result.rowcount

    async def get_attribution_by_date_range(
        self,
        session: AsyncSession,
        start: date,
        end: date,
        factor: str | None = None,
    ) -> list[AttributionHistory]:
        """按 [start, end] 闭区间查归因记录；可选 factor 过滤；按 calc_date desc 排序。"""
        stmt = select(AttributionHistory).where(
            AttributionHistory.calc_date >= start,
            AttributionHistory.calc_date <= end,
        )
        if factor is not None:
            stmt = stmt.where(AttributionHistory.factor == factor)
        stmt = stmt.order_by(
            AttributionHistory.calc_date.desc(), AttributionHistory.factor.asc(),
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
