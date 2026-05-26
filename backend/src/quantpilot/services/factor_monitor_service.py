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
import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.core.config_defaults import DEFAULT_STRATEGY_WEIGHTS
from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.factor_ic_repository import (
    FactorICRepository,
    ICAggregateRow,
    StrategyWeightsRow,
)
from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.engine.hysteresis import HysteresisStateMachine
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.models.business import CandidatePool, FactorIcHistory
from quantpilot.models.market import DailyQuote

logger = logging.getLogger(__name__)

# Phase 11 §4.1 滚动窗口配置（默认值，与 SDD v1.4 / config_defaults FactorMonitorConfig 对齐）
_ICIR_WINDOW_DAYS = 252
_ICIR_LAG_DAYS = 20
_ICIR_WARMUP_DAYS = _ICIR_WINDOW_DAYS + _ICIR_LAG_DAYS  # 272
_STATE_MIN_SAMPLES = 60
_BOOTSTRAP_ITERS = 1000
_BOOTSTRAP_SEED = 42

# Phase 11 §4.4 因子下线规则窗口
_R1_NEG_ICIR_MONTHS = 6        # ICIR < 0 持续 6 月 → 权重置 0
_R2_INSIGNIF_MONTHS = 12       # t-stat < 1.96 持续 12 月 → 权重置 0 + 告警
_R2_TSTAT_THRESHOLD = 1.96
_R3_FAST_DECAY_THRESHOLD = 5   # 半衰期 < 5 日 → 权重减半
_R4_SPARSE_MONTHS = 3          # sample_size < 60 连续 3 月 → 告警
_R4_SPARSE_THRESHOLD = 60

_VALID_STATES: tuple[str, ...] = (
    MarketStateEnum.UPTREND,
    MarketStateEnum.DOWNTREND,
    MarketStateEnum.OSCILLATION,
)
_STATE_TO_DEFAULT_ATTR: dict[str, str] = {
    MarketStateEnum.UPTREND: "uptrend",
    MarketStateEnum.DOWNTREND: "downtrend",
    MarketStateEnum.OSCILLATION: "oscillation",
}
_STRATEGY_NAMES: tuple[str, ...] = ("trend", "momentum", "mean_reversion", "value")


def _default_weights_for_state(state: str) -> dict[str, float]:
    """取冷启动 default_matrix 中某 state 的策略权重副本（防止外部修改影响默认值）。"""
    attr = _STATE_TO_DEFAULT_ATTR[state]
    return dict(getattr(DEFAULT_STRATEGY_WEIGHTS, attr))


def _default_order_for_state(state: str) -> list[str]:
    """冷启动正交化顺序：按 default_matrix 权重降序。"""
    weights = _default_weights_for_state(state)
    return sorted(weights, key=lambda s: weights[s], reverse=True)


@dataclass(frozen=True)
class ICIRSnapshot:
    """Phase 11 §4.1 ``rolling_icir_state`` 返回值：state 子集 ICIR 估计快照。

    sample_size < 60 时 ``rolling_icir_state`` 返回 None（触发冷启动 fallback），
    不返回 sample_size 不足的 snapshot——避免下游误用。
    """

    strategy: str
    factor: str
    state: str
    as_of_date: date              # 调用时的 trade_date t（窗口右端为 t-20）
    ic_mean: float                # 窗口内 state 子集 IC 均值
    ic_std: float                 # 窗口内 state 子集 IC 标准差（ddof=1 业界惯例）
    icir: float                   # ic_mean / ic_std
    sample_size: int              # state 子集有效观测数
    ic_ci_low: float              # bootstrap 95% CI 下界
    ic_ci_high: float             # bootstrap 95% CI 上界
    t_stat: float                 # icir × sqrt(sample_size)

# candidate_pool 列名 → (strategy_name, factor_name)
_FACTOR_MAP: dict[str, tuple[str, str]] = {
    "composite_score": ("AllStrategies", "composite_score"),
    "trend_score": ("TrendStrategy", "trend_score"),
    "reversion_score": ("MeanReversionStrategy", "reversion_score"),
    "momentum_score": ("MomentumStrategy", "momentum_score"),
    "value_score": ("ValueStrategy", "value_score"),
}


class FactorMonitorService:
    """Phase 7 + Phase 11 因子监控服务。

    构造方式（v1.1 P1-5 渐进式过渡）：
    - **旧风格**（Phase 7~10 兼容，B1 保留）：``FactorMonitorService(session, engine)``
      存 ``self._session`` 给 ``run_monthly`` / ``get_latest`` / ``get_history``
      等旧方法用
    - **新风格**（Phase 11 B1 起）：新方法（``rolling_icir_state`` 等）显式
      接收 ``session`` 参数 + 用 ``self._repo`` ``FactorICRepository``
    - **B2 完成时**：``run_monthly`` 改写为 ``apply_monthly_rebalance``，
      MonthlyScheduler 切换 dispatch，旧 ``__init__`` 改为无 session 构造
      并把 deps.py / monthly_scheduler.py 调用方一起切换
    """

    def __init__(
        self,
        session: AsyncSession,
        engine: FactorMonitorEngine,
        repo: FactorICRepository | None = None,
        calendar: TradingCalendar | None = None,
    ) -> None:
        self._session = session
        self._engine = engine
        self._repo = repo or FactorICRepository()
        # Phase 14 §14-5：注入 TradingCalendar 让 rolling_icir_state 走严格交易日窗口
        # （SDD §7.4 定义：252 + 20 交易日 = 272 交易日，而非日历日）。
        # calendar=None → 回退到旧路径（日历日近似），仅供旧测试兼容；生产路径
        # （main.py lifespan / MonthlyScheduler / DailyPipeline / deps.py）必须注入。
        self._calendar = calendar

    # ------------------------------------------------------------------ 月末计算

    async def run_monthly(
        self,
        calc_month: date,
        return_window: int = 20,
        notifier: object | None = None,
    ) -> int:
        """计算并存储当月所有策略因子 IC/IR/半衰期。返回写入行数。

        .. deprecated:: Phase 11
            Phase 11 起改用 :meth:`apply_monthly_rebalance`（写新表
            ``factor_ic_window_state`` + ``strategy_weights_history``）；本方法
            仍由 MonthlyScheduler 在 ``run_factor_monitoring`` 路径继续写入旧表
            ``factor_ic_history``，仅作 Phase 7~10 baseline 兼容（5y 真机历史
            数据已在旧表中累积）。Phase 14 决策是否归并到新表后 stop 调用。
            **不要在新代码中调用本方法。**

        Args:
            calc_month:    月末最后一个交易日
            return_window: 前向收益率窗口（交易日，默认 20）
            notifier:      NotificationService 实例（可选，no-op stub 或 None 均可）

        Returns:
            写入 factor_ic_history 的行数（0 表示数据不足，未写入）。
        """
        logger.warning(
            "factor_monitor.run_monthly_deprecated: Phase 11 起改用 apply_monthly_rebalance"
            "（写 factor_ic_window_state + strategy_weights_history）；旧表"
            " factor_ic_history 仅作 Phase 7~10 baseline 兼容继续写入。Phase 14"
            " 决定是否归并 + 停写。calc_month=%s",
            calc_month,
        )
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

    # ============================================================
    # Phase 11 §4.1：rolling_icir_state（state 子集 ICIR 估计）
    # ============================================================

    async def rolling_icir_state(
        self,
        session: AsyncSession,
        trade_date: date,
        strategy: str,
        factor: str,
        state: str,
    ) -> ICIRSnapshot | None:
        """计算 ``[trade_date - 272d, trade_date - 20d]`` 窗口内 state 子集
        （``state_{t-20} == state``）的 ICIR 估计。

        - sample_size < ``_STATE_MIN_SAMPLES`` (60) → 返回 ``None``（触发冷启动 fallback）
        - sample_size ≥ 60 → 返回 ``ICIRSnapshot``（ic_mean / ic_std / icir / CI / t_stat）
        - 窗口固定 ``[t-272, t-20]``（lag 20 跳过未完成 forward returns）
        - state 子集判定使用因子值日 state（``factor_ic_window_state.state`` 字段
          按 upsert 时 ``state_{t-20}`` 写入；详见 ``data/factor_ic_repository.py`` 约定）
        - bootstrap CI 用 ``np.random.default_rng(seed=42)`` 固定复现（1000 次 resample）

        Args:
            session:    显式 session（v1.1 P1-5 新规范）
            trade_date: 调用日 t；窗口右端为 t - 20 日历日
            strategy:   策略名（trend / momentum / mean_reversion / value）
            factor:     策略内因子键
            state:      市场状态（UPTREND / DOWNTREND / OSCILLATION）
        """
        # Phase 14 §14-5：窗口端点改严格交易日（SDD §7.4 定义：lag=20 交易日 +
        # 回看 252 交易日；旧路径用 272/20 日历日 ≈ 188/14 交易日，比规格短约 25%）。
        # 注入 TradingCalendar 后走严格交易日；未注入则回退到旧日历日路径仅供旧测试
        # 兼容（生产路径全部已注入）。
        if self._calendar is not None:
            window_end = self._calendar.get_prev_trade_date(
                trade_date, n=_ICIR_LAG_DAYS,
            )
            window_start = self._calendar.get_prev_trade_date(
                window_end, n=_ICIR_WINDOW_DAYS,
            )
        else:
            # 【降级说明】无 calendar 注入 → 回退到日历日近似窗口（约 188 交易日，
            # 比 SDD §7.4 规格短 25%）。仅供 Phase 11 历史单元测试兼容；生产路径
            # 必须注入 TradingCalendar 走严格交易日。
            logger.warning(
                "rolling_icir_state_calendar_missing strategy=%s factor=%s state=%s "
                "trade_date=%s — falling back to calendar-day window (Phase 14 §14-5)",
                strategy, factor, state, trade_date,
            )
            window_end = trade_date - timedelta(days=_ICIR_LAG_DAYS)
            window_start = trade_date - timedelta(days=_ICIR_WARMUP_DAYS)

        rows = await self._repo.get_ic_daily_window(
            session,
            strategy=strategy,
            factor=factor,
            state=state,
            start_date=window_start,
            end_date=window_end,
        )

        # 抽出非 NULL IC 值
        ic_values: list[float] = []
        for row in rows:
            if row.ic_value is None:
                continue
            ic_values.append(float(row.ic_value))

        sample_size = len(ic_values)
        if sample_size < _STATE_MIN_SAMPLES:
            return None

        arr = np.asarray(ic_values, dtype=float)
        ic_mean = float(arr.mean())
        # 业界 Barra 惯例：样本标准差 ddof=1
        ic_std = float(arr.std(ddof=1))

        if ic_std < 1e-12 or math.isnan(ic_std):
            # 退化（样本完全无方差）：返回 None 让冷启动 fallback
            logger.info(
                "rolling_icir_state degenerate ic_std=0: strategy=%s factor=%s state=%s",
                strategy, factor, state,
            )
            return None

        icir = ic_mean / ic_std
        t_stat = icir * math.sqrt(sample_size)

        # bootstrap 95% CI（固定 seed=42 保证复现性）
        rng = np.random.default_rng(_BOOTSTRAP_SEED)
        boot_means = np.empty(_BOOTSTRAP_ITERS, dtype=float)
        for i in range(_BOOTSTRAP_ITERS):
            resample = rng.choice(arr, size=sample_size, replace=True)
            boot_means[i] = resample.mean()
        ci_low = float(np.percentile(boot_means, 2.5))
        ci_high = float(np.percentile(boot_means, 97.5))

        # Phase 13 §3.1.2 埋点：FACTOR_ICIR Gauge
        from quantpilot.core.metrics import FACTOR_ICIR
        FACTOR_ICIR.labels(strategy=strategy, factor=factor, state=state).set(icir)

        return ICIRSnapshot(
            strategy=strategy,
            factor=factor,
            state=state,
            as_of_date=trade_date,
            ic_mean=ic_mean,
            ic_std=ic_std,
            icir=icir,
            sample_size=sample_size,
            ic_ci_low=ci_low,
            ic_ci_high=ci_high,
            t_stat=t_stat,
        )

    # ============================================================
    # Phase 13 §3.5：因子衰减持续告警（连续 N 月 ICIR < 阈值）
    # ============================================================

    PERSISTENT_DECAY_THRESHOLD = 0.05
    PERSISTENT_DECAY_MONTHS = 3

    async def check_persistent_decay(
        self,
        session: AsyncSession,
        strategy: str,
        factor: str,
        state: str,
        icir_now: float | None,
        notifier=None,  # type: ignore[no-untyped-def]
        as_of: date | None = None,
    ) -> bool:
        """连续 ``PERSISTENT_DECAY_MONTHS`` (默认 3) 月末 icir < ``PERSISTENT_DECAY_THRESHOLD``
        (默认 0.05) → 触发 ``notify_factor_alert("factor_decayed_persistent")``。

        与 Phase 11 ``_maybe_alert`` 单月告警独立：本方法看 ``factor_ic_window_state``
        近 N 行聚合行（``get_recent_aggregates``）；触发阈值同 V1.0 _maybe_alert 使用
        固定常量（P3-1 推迟到 ConfigService）。
        """
        if icir_now is None or icir_now >= self.PERSISTENT_DECAY_THRESHOLD:
            return False
        as_of = as_of or date.today()
        history = await self._repo.get_recent_aggregates(
            session,
            strategy=strategy,
            factor=factor,
            state=state,
            as_of=as_of,
            limit=self.PERSISTENT_DECAY_MONTHS,
        )
        if len(history) < self.PERSISTENT_DECAY_MONTHS:
            return False
        all_below = all(
            h.icir is not None and float(h.icir) < self.PERSISTENT_DECAY_THRESHOLD
            for h in history
        )
        if not all_below:
            return False
        if notifier is not None:
            try:
                await notifier.notify_factor_alert(
                    "factor_decayed_persistent",
                    strategy, factor, ic_mean=icir_now,
                )
            except Exception:
                logger.exception(
                    "persistent_decay_notify_failed: strategy=%s factor=%s state=%s",
                    strategy, factor, state,
                )
        return True

    # ============================================================
    # Phase 11 §4.4：check_factor_offline_rules
    # ============================================================

    async def check_factor_offline_rules(
        self,
        session: AsyncSession,
        as_of_date: date,
        strategy_factor_states: list[tuple[str, str, str]],
    ) -> dict[tuple[str, str, str], dict[str, object]]:
        """按 SDD §7.4 R1~R4 检查每个 (strategy, factor, state) 三元组的
        最近聚合行，决定是否下线 / 减半权重 / 告警。

        Args:
            session: 显式 session
            as_of_date: 检查截止日（取此日期及以前的最近 N 行聚合行）
            strategy_factor_states: 需检查的三元组列表

        Returns:
            ``{(strategy, factor, state): {"action": ..., "rule": ..., "details": ...}}``
            action 取值：
            - ``"offline"`` (R1/R2)：权重置 0
            - ``"halve"`` (R3)：权重减半
            - ``"warn"`` (R4)：告警（不下线）
            - ``"ok"``: 无触发
        """
        out: dict[tuple[str, str, str], dict[str, object]] = {}

        for sfs in strategy_factor_states:
            strategy, factor, state = sfs
            # 取最近 12 行（足够覆盖 R1/R2/R3/R4 各窗口）
            recent = await self._repo.get_recent_aggregates(
                session, strategy=strategy, factor=factor, state=state,
                as_of=as_of_date, limit=_R2_INSIGNIF_MONTHS,
            )

            # R1：ICIR < 0 连续 6 月
            if (
                len(recent) >= _R1_NEG_ICIR_MONTHS
                and all(r.icir is not None and float(r.icir) < 0
                        for r in recent[:_R1_NEG_ICIR_MONTHS])
            ):
                out[sfs] = {
                    "action": "offline",
                    "rule": "R1",
                    "details": f"ICIR<0 持续 {_R1_NEG_ICIR_MONTHS} 月",
                }
                continue

            # R2：t-stat < 1.96 连续 12 月
            if (
                len(recent) >= _R2_INSIGNIF_MONTHS
                and all(r.t_stat is not None
                        and abs(float(r.t_stat)) < _R2_TSTAT_THRESHOLD
                        for r in recent[:_R2_INSIGNIF_MONTHS])
            ):
                out[sfs] = {
                    "action": "offline",
                    "rule": "R2",
                    "details": f"t-stat<{_R2_TSTAT_THRESHOLD} 持续 {_R2_INSIGNIF_MONTHS} 月",
                }
                continue

            # R3：半衰期 < 5 日（最新一行即可）
            if recent and recent[0].half_life is not None \
                    and int(recent[0].half_life) < _R3_FAST_DECAY_THRESHOLD:
                out[sfs] = {
                    "action": "halve",
                    "rule": "R3",
                    "details": f"half_life={recent[0].half_life} < {_R3_FAST_DECAY_THRESHOLD}",
                }
                continue

            # R4：sample_size < 60 连续 3 月（告警，不下线）
            if (
                len(recent) >= _R4_SPARSE_MONTHS
                and all(int(r.sample_size) < _R4_SPARSE_THRESHOLD
                        for r in recent[:_R4_SPARSE_MONTHS])
            ):
                out[sfs] = {
                    "action": "warn",
                    "rule": "R4",
                    "details": f"sample_size<{_R4_SPARSE_THRESHOLD} 连续 {_R4_SPARSE_MONTHS} 月",
                }
                continue

            out[sfs] = {"action": "ok", "rule": None, "details": ""}

        return out

    # ============================================================
    # Phase 11 §4.5：get_active_weights（运行时实时取生效权重）
    # ============================================================

    async def get_active_weights(
        self,
        session: AsyncSession,
        trade_date: date,
        market_state: str,
    ) -> tuple[dict[str, float], str, list[str], str]:
        """实时取某 state 在 ``trade_date <= ...`` 范围内最近一次生效的权重。

        Returns:
            ``(weights, weights_source, orthogonalize_order, hysteresis_status)``：
            - ``weights``: ``{strategy: weight}``，sum=1
            - ``weights_source``: ``"icir"`` / ``"default_matrix"`` / ``"user_override"``
            - ``orthogonalize_order``: 正交化顺序（按 weight 降序）
            - ``hysteresis_status``: ``"stable"`` / ``"pending_switch"``

        冷启动 / 任一异常 → 返回 default_matrix。
        """
        if market_state not in _VALID_STATES:
            raise ValueError(f"invalid market_state: {market_state}")

        rows = await self._repo.get_latest_strategy_weights(
            session, state=market_state, as_of=trade_date,
        )

        if not rows:
            # 冷启动：strategy_weights_history 无数据 → 用 default_matrix
            weights = _default_weights_for_state(market_state)
            return weights, "default_matrix", _default_order_for_state(market_state), "stable"

        # 已有历史：组装 weights / source / order / status
        weights = {r.strategy: float(r.weight_used) for r in rows}
        # 补齐缺失策略（兜底用 0；正常应 4 个都有）
        for s in _STRATEGY_NAMES:
            weights.setdefault(s, 0.0)
        source = rows[0].weights_source       # 同 state 同月所有 strategy 同 source
        status = rows[0].hysteresis_status
        order = sorted(weights, key=lambda s: weights[s], reverse=True)
        return weights, source, order, status

    # ============================================================
    # Phase 11 §4.2：apply_monthly_rebalance（月末 rebalance Job）
    # ============================================================

    async def apply_monthly_rebalance(
        self,
        session: AsyncSession,
        month_end_date: date,
        notifier: object | None = None,
    ) -> dict[str, list[StrategyWeightsRow]]:
        """每月最后一个交易日收盘后调用：

        1. 对每个 state，遍历 4 个 strategy（V1.0 简化：strategy=factor 名）调用
           ``rolling_icir_state`` 取窗口内 ICIR snapshot
        2. 写聚合行 ``factor_ic_window_state``（含 CI / t-stat）
        3. 按 ICIR 降序得到 this_month_order，调 HysteresisStateMachine 判定
           effective_order / new_status
        4. 调 check_factor_offline_rules 应用 R1~R4 → 决定权重调整
        5. ICIR 加权或冷启动 fallback → 写 strategy_weights_history（生效日
           = month_end_date + 1 日，作为次月首日近似）

        【B2 简化】V1.0 strategy 内只取一个"代表因子"（=strategy 名本身），等同于
        策略级 ICIR。完整策略内多因子 ICIR 拆分（如 trend 的 ma_alignment /
        macd_state / breakout）由 P11-A2 ScoringService 写入 score_breakdown_raw
        新列后，下一轮 rebalance 自动覆盖。

        Returns:
            ``{state: [StrategyWeightsRow, ...]}``：写入 strategy_weights_history 的行
        """
        effective_date = month_end_date + timedelta(days=1)
        out: dict[str, list[StrategyWeightsRow]] = {}

        for state in _VALID_STATES:
            # 1. 取每个 strategy 的 ICIR snapshot（V1.0 简化：factor=strategy）
            snapshots: dict[str, ICIRSnapshot] = {}
            aggregate_rows: list[ICAggregateRow] = []
            for strategy in _STRATEGY_NAMES:
                snap = await self.rolling_icir_state(
                    session, trade_date=month_end_date,
                    strategy=strategy, factor=strategy, state=state,
                )
                if snap is None:
                    continue
                snapshots[strategy] = snap
                aggregate_rows.append(ICAggregateRow(
                    strategy=strategy, factor=strategy, state=state,
                    trade_date=month_end_date,
                    ic_mean_state=snap.ic_mean,
                    ic_std_state=snap.ic_std,
                    icir=snap.icir,
                    sample_size=snap.sample_size,
                    ic_ci_low=snap.ic_ci_low,
                    ic_ci_high=snap.ic_ci_high,
                    t_stat=snap.t_stat,
                    half_life=None,    # B2 暂不实现；FactorMonitorEngine.calc_half_life 在 A2 接入
                ))
            # 2. 写聚合行（必须在 check_persistent_decay 之前 flush，
            # 否则 get_recent_aggregates 看不到当月新行）
            if aggregate_rows:
                await self._repo.upsert_ic_aggregate(session, aggregate_rows)
                await session.flush()

            # R13-P1-2：持续告警检查——对每个 (state, strategy) 当月 ICIR < 0.05
            # 且历史连续 3 月都 < 0.05 → 触发 factor_decayed_persistent。
            # 与单月 _maybe_alert 独立；二者 24h 内通过 _is_duplicate 按 payload
            # 区分（alert_type=factor_decayed vs factor_decayed_persistent）。
            # notifier=None 时 check_persistent_decay 内部不 await notifier（直接
            # 返回 bool），不抛异常 → MonthlyScheduler 注入 NotificationService。
            # Phase 14 §14-7 R13-P2-3：累积命中持续告警的 (strategy, factor, state)
            # 三元组，后续单月告警路径（offline_decisions R1/R2/R3 触发的告警）
            # 通过该集合跳过同月重复告警（同月用户体感 2 条 → 1 条）。
            persistent_decay_hits: set[tuple[str, str, str]] = set()
            for strategy, snap in snapshots.items():
                try:
                    persistent_hit = await self.check_persistent_decay(
                        session=session,
                        strategy=strategy, factor=strategy, state=state,
                        icir_now=float(snap.icir),
                        notifier=notifier,
                        as_of=month_end_date,
                    )
                    if persistent_hit:
                        persistent_decay_hits.add((strategy, strategy, state))
                except Exception:
                    logger.exception(
                        "check_persistent_decay_failed state=%s strategy=%s",
                        state, strategy,
                    )

            # 3. Hysteresis 判定
            this_order = sorted(snapshots, key=lambda s: snapshots[s].icir, reverse=True)
            prev_rows = await self._repo.get_latest_strategy_weights(
                session, state=state, as_of=month_end_date,
            )
            if prev_rows:
                # 按上月 weight 降序得到 prev_order
                prev_order = sorted(
                    (r.strategy for r in prev_rows),
                    key=lambda s: next(
                        (float(r.weight_used) for r in prev_rows if r.strategy == s),
                        0.0,
                    ),
                    reverse=True,
                )
                last_status = prev_rows[0].hysteresis_status
            else:
                prev_order = None
                last_status = "stable"

            hsm = HysteresisStateMachine()
            if not this_order:
                # 无任何 ICIR 数据 → 冷启动
                effective_order = _default_order_for_state(state)
                new_status = "stable"
            else:
                # 补齐 this_order 中缺失的 strategy（用默认顺序兜底）
                full_this_order = list(this_order)
                for s in _default_order_for_state(state):
                    if s not in full_this_order:
                        full_this_order.append(s)
                effective_order, new_status = hsm.evaluate(
                    prev_month_order=prev_order,
                    this_month_order=full_this_order,
                    last_status=last_status,
                )

            # 4. 因子下线规则
            sfs_list = [(s, s, state) for s in _STRATEGY_NAMES]
            offline_decisions = await self.check_factor_offline_rules(
                session, as_of_date=month_end_date,
                strategy_factor_states=sfs_list,
            )

            # Phase 14 §14-7 R13-P2-3：R1/R2/R3 单月告警（best-effort）。
            # 命中持续告警的 (strategy, factor, state) 跳过同月单月告警，避免用户
            # 24h 内收到 factor_decayed_persistent + factor_decayed_<rule> 两条
            # 同源通知（_is_duplicate 按 alert_type 区分会让两条都发）。
            if notifier is not None:
                for sfs, decision in offline_decisions.items():
                    action = decision.get("action")
                    if action in ("offline", "halve", "warn"):
                        strategy_n, factor_n, state_n = sfs
                        if sfs in persistent_decay_hits:
                            logger.info(
                                "single_month_alert_suppressed_by_persistent "
                                "strategy=%s factor=%s state=%s rule=%s",
                                strategy_n, factor_n, state_n,
                                decision.get("rule"),
                            )
                            continue
                        try:
                            await notifier.notify_factor_alert(
                                f"factor_decayed_{decision.get('rule', 'rule')}",
                                strategy_n, factor_n,
                            )
                        except Exception:
                            logger.exception(
                                "single_month_alert_failed strategy=%s factor=%s "
                                "state=%s", strategy_n, factor_n, state_n,
                            )

            # 5. 决策权重：ICIR 加权 + 应用下线规则；冷启动 fallback
            positive_icirs = {
                s: max(snapshots[s].icir, 0.0) if s in snapshots else 0.0
                for s in _STRATEGY_NAMES
            }
            # 应用下线规则
            adjustments: dict[str, float] = {}  # multiplier per strategy
            for s in _STRATEGY_NAMES:
                decision = offline_decisions.get((s, s, state), {"action": "ok"})
                action = decision["action"]
                if action == "offline":
                    adjustments[s] = 0.0
                elif action == "halve":
                    adjustments[s] = 0.5
                else:
                    adjustments[s] = 1.0
            positive_icirs = {s: positive_icirs[s] * adjustments[s] for s in _STRATEGY_NAMES}

            total = sum(positive_icirs.values())
            icir_inputs_dict: dict[str, object] | None
            if not snapshots or total <= 0:
                weights = _default_weights_for_state(state)
                source = "default_matrix"
                icir_inputs_dict = None
            else:
                weights = {s: positive_icirs[s] / total for s in _STRATEGY_NAMES}
                source = "icir"
                icir_inputs_dict = {
                    s: {
                        "icir": float(snapshots[s].icir),
                        "sample_size": int(snapshots[s].sample_size),
                        "t_stat": float(snapshots[s].t_stat),
                        "ci_low": float(snapshots[s].ic_ci_low),
                        "ci_high": float(snapshots[s].ic_ci_high),
                        "offline_action": str(
                            offline_decisions.get((s, s, state), {"action": "ok"})["action"]
                        ),
                    }
                    for s in snapshots
                }

            # 6. 写 strategy_weights_history（next month effective_date）
            rows: list[StrategyWeightsRow] = [
                StrategyWeightsRow(
                    state=state, strategy=s,
                    trade_date=effective_date,
                    weight_used=weights[s],
                    weights_source=source,
                    icir_inputs=icir_inputs_dict if source == "icir" else None,
                    hysteresis_status=new_status,
                )
                for s in _STRATEGY_NAMES
            ]
            await self._repo.upsert_strategy_weights(session, rows)
            out[state] = rows

            logger.info(
                "icir_rebalance state=%s source=%s effective_order=%s status=%s",
                state, source, effective_order, new_status,
            )

        return out
