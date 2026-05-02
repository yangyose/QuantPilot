"""FactorMonitorService：因子 IC 计算、存储与告警（Phase 7）。

数据流（run_monthly）：
1. 从 candidate_pool 取 calc_month 当日五列策略评分（作为因子值）
2. 从 daily_quote 取 calc_month 和 calc_month+return_window 日收盘价，计算前向收益率
3. 逐（strategy, factor）调用 FactorMonitorEngine.calc_ic()
4. 从 factor_ic_history 取历史 IC 序列，计算 IC_mean/IC_std/IR/half_life
5. upsert factor_ic_history（ON CONFLICT DO UPDATE）
6. 告警通知（NotificationService no-op stub）

【降级说明】IC 因子值来源为 candidate_pool 策略评分（trend/reversion/momentum/value/composite），
非 signal_score_snapshot.raw_factors 单因子值。
原因：raw_factors 仅覆盖生成信号的少量股票，候选池评分覆盖更广（in_pool 全量）。
恢复条件：若需个别因子 IC（如 adx_value），从 signal_score_snapshot.raw_factors 解析后另行计算。
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.models.business import CandidatePool, FactorIcHistory
from quantpilot.models.market import DailyQuote

logger = logging.getLogger(__name__)

# candidate_pool 列名 → (strategy_name, factor_name)
_FACTOR_MAP: dict[str, tuple[str, str]] = {
    "composite_score": ("AllStrategies", "composite_score"),
    "trend_score": ("TrendStrategy", "trend_score"),
    "reversion_score": ("MeanReversionStrategy", "reversion_score"),
    "momentum_score": ("MomentumStrategy", "momentum_score"),
    "value_score": ("ValueStrategy", "value_score"),
}


class FactorMonitorService:
    def __init__(self, session: AsyncSession, engine: FactorMonitorEngine) -> None:
        self._session = session
        self._engine = engine

    # ------------------------------------------------------------------ 月末计算

    async def run_monthly(
        self,
        calc_month: date,
        return_window: int = 20,
        notifier: object | None = None,
    ) -> int:
        """计算并存储当月所有策略因子 IC/IR/半衰期。返回写入行数。

        Args:
            calc_month:    月末最后一个交易日
            return_window: 前向收益率窗口（交易日，默认 20）
            notifier:      NotificationService 实例（可选，no-op stub 或 None 均可）

        Returns:
            写入 factor_ic_history 的行数（0 表示数据不足，未写入）。
        """
        import pandas as pd

        # 1. 取 calc_month 当日候选池
        pool_rows = await self._session.execute(
            select(
                CandidatePool.ts_code,
                CandidatePool.composite_score,
                CandidatePool.trend_score,
                CandidatePool.reversion_score,
                CandidatePool.momentum_score,
                CandidatePool.value_score,
            ).where(CandidatePool.trade_date == calc_month)
        )
        pool_data = pool_rows.all()
        if not pool_data:
            logger.info("factor_monitor_skip: no candidate_pool data for %s", calc_month)
            return 0

        pool_df = pd.DataFrame(
            pool_data,
            columns=["ts_code", "composite_score", "trend_score",
                     "reversion_score", "momentum_score", "value_score"],
        ).set_index("ts_code")
        # NUMERIC 列转 float
        for col in pool_df.columns:
            pool_df[col] = pool_df[col].astype(float)

        ts_codes = list(pool_df.index)

        # 2. 取 calc_month 当日及 return_window 之后最近可用日的收盘价
        #    用 DISTINCT ON 取 >= calc_month 最近的两个不同 trade_date
        forward_returns = await self._calc_forward_returns(ts_codes, calc_month, return_window)
        if forward_returns.empty:
            logger.info("factor_monitor_skip: no forward return data for %s", calc_month)
            return 0

        # 3. 逐因子计算 IC，upsert factor_ic_history
        written = 0
        for col, (strategy_name, factor_name) in _FACTOR_MAP.items():
            if col not in pool_df.columns:
                continue
            factor_values = pool_df[col].dropna()
            aligned = factor_values.align(forward_returns, join="inner")
            f_series, r_series = aligned

            ic = self._engine.calc_ic(f_series, r_series)
            if ic is None:
                logger.debug(
                    "factor_ic_skip: %s.%s samples insufficient", strategy_name, factor_name
                )
                continue

            # 4. 取历史 IC 序列（最近 12 个月）
            hist_rows = await self._session.execute(
                select(FactorIcHistory.ic_value)
                .where(
                    FactorIcHistory.strategy_name == strategy_name,
                    FactorIcHistory.factor_name == factor_name,
                    FactorIcHistory.return_window == return_window,
                )
                .order_by(FactorIcHistory.calc_month.desc())
                .limit(12)
            )
            past_ics: list[float] = [
                float(row.ic_value) for row in hist_rows if row.ic_value is not None
            ]
            if ic is not None:
                past_ics = [ic] + past_ics  # 本月 IC 加入序列（时间降序）

            # 滚动窗口取最近 3 个月（序列已降序，取前 3 再反转）
            recent_3 = list(reversed(past_ics[:3]))
            all_ics = list(reversed(past_ics))  # 升序供 IC_IR/half_life

            ic_mean, ic_std, ir = self._engine.calc_ic_ir(all_ics, window=3)
            half_life = self._engine.calc_half_life(all_ics)
            alert = self._engine.detect_alert(ic_mean, ir, half_life, recent_3)

            # 5. upsert
            stmt = (
                pg_insert(FactorIcHistory)
                .values(
                    calc_month=calc_month,
                    strategy_name=strategy_name,
                    factor_name=factor_name,
                    ic_value=ic,
                    ic_mean_3m=ic_mean,
                    ic_std_3m=ic_std,
                    ir_3m=ir,
                    half_life_days=half_life,
                    return_window=return_window,
                    alert_status=alert,
                )
                .on_conflict_do_update(
                    constraint="uq_ic_history_month_strategy_factor_window",
                    set_={
                        "ic_value": ic,
                        "ic_mean_3m": ic_mean,
                        "ic_std_3m": ic_std,
                        "ir_3m": ir,
                        "half_life_days": half_life,
                        "alert_status": alert,
                    },
                )
            )
            await self._session.execute(stmt)
            written += 1

            # 6. 告警通知（best-effort，Phase 10 §7.3：接入 NotificationService/WxPusher）
            if alert and notifier is not None:
                try:
                    await notifier.notify_factor_alert(
                        alert, strategy_name, factor_name, ic_mean=ic_mean,
                    )
                except Exception:
                    logger.warning(
                        "factor_alert_notify_failed strategy=%s factor=%s",
                        strategy_name, factor_name, exc_info=True,
                    )

        await self._session.flush()
        return written

    async def _calc_forward_returns(
        self,
        ts_codes: list[str],
        base_date: date,
        window: int,
    ) -> "pd.Series":  # noqa: F821
        """计算各股 base_date 至 base_date+window 交易日的简单收益率。

        对每只股票取 start_price（base_date close）和 end_price（约 window 日后 close），
        返回 Series(index=ts_code, values=forward_return)。
        """
        from datetime import timedelta

        import pandas as pd

        if not ts_codes:
            return pd.Series(dtype=float)

        # base_date 当日价格
        base_rows = await self._session.execute(
            select(DailyQuote.ts_code, DailyQuote.close).where(
                DailyQuote.ts_code.in_(ts_codes),
                DailyQuote.trade_date == base_date,
            )
        )
        base_prices = {row.ts_code: float(row.close) for row in base_rows if row.close}

        if not base_prices:
            return pd.Series(dtype=float)

        # end_date 估算（window 交易日 ≈ window * 1.4~1.5 日历天），取该窗口内最近的 close。
        # CLAUDE.md §6：交易日数换算日历天 = int(days * 1.5)；下界用 1.4 留出合理搜索区间。
        approx_start = base_date + timedelta(days=int(window * 1.4))
        approx_end = base_date + timedelta(days=int(window * 1.5))
        end_stmt = (
            select(DailyQuote.ts_code, DailyQuote.close)
            .distinct(DailyQuote.ts_code)
            .where(
                DailyQuote.ts_code.in_(list(base_prices.keys())),
                DailyQuote.trade_date >= approx_start,
                DailyQuote.trade_date <= approx_end,
            )
            .order_by(DailyQuote.ts_code, DailyQuote.trade_date)
        )
        end_rows = await self._session.execute(end_stmt)
        end_prices = {row.ts_code: float(row.close) for row in end_rows if row.close}

        returns: dict[str, float] = {}
        for code, start in base_prices.items():
            end = end_prices.get(code)
            if end and start > 0:
                returns[code] = (end - start) / start

        return pd.Series(returns)

    # ------------------------------------------------------------------ 查询

    async def get_latest(
        self,
        strategy_name: str | None = None,
    ) -> list[FactorIcHistory]:
        """取每个（strategy, factor）最新一条记录。"""
        # 子查询：每组最大 calc_month
        subq = (
            select(
                FactorIcHistory.strategy_name,
                FactorIcHistory.factor_name,
                FactorIcHistory.return_window,
                func.max(FactorIcHistory.calc_month).label("max_month"),
            )
            .group_by(
                FactorIcHistory.strategy_name,
                FactorIcHistory.factor_name,
                FactorIcHistory.return_window,
            )
        )
        if strategy_name:
            subq = subq.where(FactorIcHistory.strategy_name == strategy_name)
        subq = subq.subquery()

        stmt = select(FactorIcHistory).join(
            subq,
            (FactorIcHistory.strategy_name == subq.c.strategy_name)
            & (FactorIcHistory.factor_name == subq.c.factor_name)
            & (FactorIcHistory.return_window == subq.c.return_window)
            & (FactorIcHistory.calc_month == subq.c.max_month),
        ).order_by(FactorIcHistory.strategy_name, FactorIcHistory.factor_name)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_history(
        self,
        strategy_name: str | None = None,
        factor_name: str | None = None,
        limit: int = 12,
    ) -> tuple[list[FactorIcHistory], int]:
        """取历史 IC 趋势（按 calc_month DESC）。返回 (records, total_count)。"""
        stmt = select(FactorIcHistory)
        if strategy_name:
            stmt = stmt.where(FactorIcHistory.strategy_name == strategy_name)
        if factor_name:
            stmt = stmt.where(FactorIcHistory.factor_name == factor_name)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total: int = (await self._session.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(FactorIcHistory.calc_month.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total
