"""FactorICRepository：Phase 11 因子 ICIR 监控持久化（§4 / §2.1）。

新表 ``factor_ic_window_state``（IC_daily + ICIR 状态维度）+ 配套
``strategy_weights_history``（每月生效权重审计）的 CRUD 封装。Phase 7
既有 ``factor_ic_history`` 表保留 readonly 不在此 repo 管辖范围内。

约定：
- ``trade_date`` = IC 观察日 t（也即 IC 实现日，对应 ``return_{t-20→t}`` 已完成）
- ``state`` = 因子值日 state_{t-20}（即 t-20 日的市场状态），由调用方在
  upsert 前查 ``market_state_history`` 决定，本 repo 仅做 CRUD
- 窗口约束 ``[trade_date - 272d, trade_date - 20d]`` 由 ``FactorMonitorService``
  在 rolling_icir_state 中应用，本 repo 仅提供基础查询能力
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.models.business import FactorICWindowState, StrategyWeightsHistory


@dataclass(frozen=True)
class ICDailyRow:
    """单条 IC_daily 记录（写入 factor_ic_window_state 单点）。"""

    strategy: str
    factor: str
    state: str            # 因子值日 state（state_{t-20}）
    trade_date: date      # IC 观察日 t（IC 实现日）
    ic_value: float | None
    sample_size: int      # IC_daily 单点的有效样本数（calc_ic 输入 dropna 后行数）


@dataclass(frozen=True)
class ICAggregateRow:
    """月末聚合后的窗口统计（写入 factor_ic_window_state 聚合行）。

    与 ICDailyRow 共用一张表：聚合行的 trade_date = 窗口末日（月末 t），
    其它字段 ic_mean_state / icir / ci / t_stat / half_life 填充，
    ic_value 留 NULL 区分。
    """

    strategy: str
    factor: str
    state: str
    trade_date: date
    ic_mean_state: float | None
    ic_std_state: float | None
    icir: float | None
    sample_size: int
    ic_ci_low: float | None
    ic_ci_high: float | None
    t_stat: float | None
    half_life: int | None


@dataclass(frozen=True)
class StrategyWeightsRow:
    """月度生效权重（写入 strategy_weights_history）。"""

    state: str
    strategy: str
    trade_date: date            # 生效起始日（次月第一个交易日）
    weight_used: float
    weights_source: str         # "icir" / "default_matrix" / "user_override"
    icir_inputs: dict[str, Any] | None
    hysteresis_status: str      # "stable" / "pending_switch"


class FactorICRepository:
    """``factor_ic_window_state`` + ``strategy_weights_history`` 持久化封装。

    本 repo 遵循 v1.1 P1-5 无状态构造原则——所有方法显式接收 ``session``，
    repo 实例本身不持有 session（与 MarketDataRepository 旧风格不同）。
    """

    # ============================================================
    # factor_ic_window_state CRUD
    # ============================================================

    async def upsert_ic_daily(
        self,
        session: AsyncSession,
        rows: list[ICDailyRow],
    ) -> int:
        """批量 upsert IC_daily 单点记录。

        ON CONFLICT (strategy, factor, state, trade_date) DO UPDATE 仅刷新
        ``ic_value`` / ``sample_size`` 列；其它聚合列（ic_mean_state 等）不动。
        """
        if not rows:
            return 0
        # Phase 14 §14-6：写入时显式标记 row_type='daily'（partial unique 在
        # 'aggregate' 上不约束此类行；既有全表 UNIQUE 仍保证 4-tuple 唯一）
        values = [
            {
                "strategy": r.strategy,
                "factor": r.factor,
                "state": r.state,
                "trade_date": r.trade_date,
                "ic_value": r.ic_value,
                "sample_size": r.sample_size,
                "row_type": "daily",
            }
            for r in rows
        ]
        stmt = pg_insert(FactorICWindowState).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["strategy", "factor", "state", "trade_date"],
            set_={
                "ic_value": stmt.excluded.ic_value,
                "sample_size": stmt.excluded.sample_size,
                # 不覆盖 row_type：若同 4-tuple 已存在 aggregate 行（含 ic_value），
                # 保留 'aggregate' 标记，本日级 upsert 仅更新 ic_value/sample_size
            },
        )
        await session.execute(stmt)
        return len(values)

    async def upsert_ic_aggregate(
        self,
        session: AsyncSession,
        rows: list[ICAggregateRow],
    ) -> int:
        """批量 upsert 月末聚合统计行。

        与 ``upsert_ic_daily`` 共用同一张表 + 同一 UNIQUE 约束；聚合行写入时
        会覆盖 ic_value（B2 实施可在月末写日级 + 聚合两类行：用不同
        ``trade_date`` 区分，或者用同一行先 daily 后 aggregate 合并）。
        """
        if not rows:
            return 0
        # Phase 14 §14-6：写入时显式标记 row_type='aggregate'。on_conflict 时
        # 强制 row_type='aggregate'（即使旧行为 'daily'，aggregate 写入升级行类型）。
        values = [
            {
                "strategy": r.strategy,
                "factor": r.factor,
                "state": r.state,
                "trade_date": r.trade_date,
                "ic_mean_state": r.ic_mean_state,
                "ic_std_state": r.ic_std_state,
                "icir": r.icir,
                "sample_size": r.sample_size,
                "ic_ci_low": r.ic_ci_low,
                "ic_ci_high": r.ic_ci_high,
                "t_stat": r.t_stat,
                "half_life": r.half_life,
                "row_type": "aggregate",
            }
            for r in rows
        ]
        stmt = pg_insert(FactorICWindowState).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["strategy", "factor", "state", "trade_date"],
            set_={
                "ic_mean_state": stmt.excluded.ic_mean_state,
                "ic_std_state": stmt.excluded.ic_std_state,
                "icir": stmt.excluded.icir,
                "sample_size": stmt.excluded.sample_size,
                "ic_ci_low": stmt.excluded.ic_ci_low,
                "ic_ci_high": stmt.excluded.ic_ci_high,
                "t_stat": stmt.excluded.t_stat,
                "half_life": stmt.excluded.half_life,
                "row_type": stmt.excluded.row_type,
            },
        )
        await session.execute(stmt)
        return len(values)

    async def get_ic_daily_window(
        self,
        session: AsyncSession,
        strategy: str,
        factor: str,
        state: str,
        start_date: date,
        end_date: date,
    ) -> list[FactorICWindowState]:
        """查询窗口 ``[start_date, end_date]`` 内某 (strategy, factor, state) 的
        IC_daily 单点序列（按 trade_date 升序）。

        - 仅返回 ``ic_value IS NOT NULL`` 的行（过滤掉纯聚合行）
        - 调用方：``FactorMonitorService.rolling_icir_state``
        """
        stmt = (
            select(FactorICWindowState)
            .where(
                FactorICWindowState.strategy == strategy,
                FactorICWindowState.factor == factor,
                FactorICWindowState.state == state,
                FactorICWindowState.trade_date >= start_date,
                FactorICWindowState.trade_date <= end_date,
                FactorICWindowState.ic_value.isnot(None),
            )
            .order_by(FactorICWindowState.trade_date.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_recent_aggregates(
        self,
        session: AsyncSession,
        strategy: str,
        factor: str,
        state: str,
        as_of: date,
        limit: int,
    ) -> list[FactorICWindowState]:
        """取某 (strategy, factor, state) 在 ``trade_date <= as_of`` 范围内最近
        ``limit`` 行**聚合行**（``row_type='aggregate'``），按 trade_date 降序排列。

        Phase 14 §14-6：过滤改用 ``row_type='aggregate'`` 替代 ``icir IS NOT NULL``，
        走 partial unique index uq_factor_ic_window_state_aggregate 的 index-only
        scan（NULL 谓词不能利用索引）。

        供 ``FactorMonitorService.check_factor_offline_rules`` 实施 R1（ICIR<0
        连续 6 月）/ R2（t-stat<1.96 连续 12 月）/ R4（sample_size<60 连续 3 月）
        判定。
        """
        stmt = (
            select(FactorICWindowState)
            .where(
                FactorICWindowState.strategy == strategy,
                FactorICWindowState.factor == factor,
                FactorICWindowState.state == state,
                FactorICWindowState.trade_date <= as_of,
                FactorICWindowState.row_type == "aggregate",
            )
            .order_by(FactorICWindowState.trade_date.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def list_aggregates(
        self,
        session: AsyncSession,
        strategy: str | None = None,
        factor: str | None = None,
        state: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 500,
    ) -> list[FactorICWindowState]:
        """Phase 11 §9.2：GET /factor-quality/ic-history 查询 ICIR 聚合行时序。

        过滤条件均可选；返回聚合行（Phase 14 §14-6：``row_type='aggregate'``
        替代 ``icir IS NOT NULL`` 谓词，走 partial unique index 优化），
        按 trade_date 升序。
        """
        stmt = select(FactorICWindowState).where(
            FactorICWindowState.row_type == "aggregate",
        )
        if strategy:
            stmt = stmt.where(FactorICWindowState.strategy == strategy)
        if factor:
            stmt = stmt.where(FactorICWindowState.factor == factor)
        if state:
            stmt = stmt.where(FactorICWindowState.state == state)
        if start_date:
            stmt = stmt.where(FactorICWindowState.trade_date >= start_date)
        if end_date:
            stmt = stmt.where(FactorICWindowState.trade_date <= end_date)
        stmt = stmt.order_by(FactorICWindowState.trade_date.asc()).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_icir(
        self,
        session: AsyncSession,
        strategy: str,
        factor: str,
        state: str,
        as_of: date,
    ) -> FactorICWindowState | None:
        """取某 (strategy, factor, state) 在 ``trade_date <= as_of`` 范围内
        最近一行**聚合行**（Phase 14 §14-6：``row_type='aggregate'`` 替代
        ``icir IS NOT NULL`` 谓词走 partial unique index），供 B2 Hysteresis
        判定 + 历史回看。"""
        stmt = (
            select(FactorICWindowState)
            .where(
                FactorICWindowState.strategy == strategy,
                FactorICWindowState.factor == factor,
                FactorICWindowState.state == state,
                FactorICWindowState.trade_date <= as_of,
                FactorICWindowState.row_type == "aggregate",
            )
            .order_by(FactorICWindowState.trade_date.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    # ============================================================
    # strategy_weights_history CRUD
    # ============================================================

    async def upsert_strategy_weights(
        self,
        session: AsyncSession,
        rows: list[StrategyWeightsRow],
    ) -> int:
        """批量 upsert 月度生效权重。

        ON CONFLICT (state, strategy, trade_date) DO UPDATE 全字段刷新（同一
        trade_date 多次写入时取最后一次，例如 B2 重算 rebalance 修订情形）。
        """
        if not rows:
            return 0
        values = [
            {
                "state": r.state,
                "strategy": r.strategy,
                "trade_date": r.trade_date,
                "weight_used": r.weight_used,
                "weights_source": r.weights_source,
                "icir_inputs": r.icir_inputs,
                "hysteresis_status": r.hysteresis_status,
            }
            for r in rows
        ]
        stmt = pg_insert(StrategyWeightsHistory).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["state", "strategy", "trade_date"],
            set_={
                "weight_used": stmt.excluded.weight_used,
                "weights_source": stmt.excluded.weights_source,
                "icir_inputs": stmt.excluded.icir_inputs,
                "hysteresis_status": stmt.excluded.hysteresis_status,
            },
        )
        await session.execute(stmt)
        return len(values)

    async def get_latest_strategy_weights(
        self,
        session: AsyncSession,
        state: str,
        as_of: date,
    ) -> list[StrategyWeightsHistory]:
        """取某 ``state`` 在 ``trade_date <= as_of`` 范围内每个 strategy 最近
        一行的权重——供 ``ScoringService.get_active_weights`` 调用。

        实现：PostgreSQL ``DISTINCT ON (strategy) ORDER BY strategy, trade_date DESC``。
        """
        # SQLAlchemy 不直接支持 DISTINCT ON，用 lateral subquery 等价表达
        # 简化：先按 (state) 取全部行，Python 端按 strategy 分组取最近一行
        # 因 strategy 仅 4 个值，行数有限，本地分组无性能问题
        stmt = (
            select(StrategyWeightsHistory)
            .where(
                StrategyWeightsHistory.state == state,
                StrategyWeightsHistory.trade_date <= as_of,
            )
            .order_by(
                StrategyWeightsHistory.strategy.asc(),
                StrategyWeightsHistory.trade_date.desc(),
            )
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        # Python 端去重：每个 strategy 留第一行（trade_date DESC 后即最新）
        seen: set[str] = set()
        out: list[StrategyWeightsHistory] = []
        for row in rows:
            if row.strategy in seen:
                continue
            seen.add(row.strategy)
            out.append(row)
        return out
