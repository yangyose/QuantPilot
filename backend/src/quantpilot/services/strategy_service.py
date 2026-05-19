"""ScoringService：日度评分编排（Phase 4 + Phase 11 §3.4 改造）。"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.pool import CandidatePoolManager
from quantpilot.engine.scorer import CompositeScore, Scorer
from quantpilot.engine.strategies.base import BaseStrategy, MarketSnapshot
from quantpilot.engine.universe import UniverseFilter

if TYPE_CHECKING:
    from quantpilot.services.factor_monitor_service import FactorMonitorService

logger = logging.getLogger(__name__)

# 沪深300 作为基准指数
_BENCHMARK_INDEX = "000300.SH"
# 后复权价格窗口：近 180 日历天 ≈ 120 交易日（覆盖 MomentumStrategy 6M 窗口）
_PRICE_WINDOW_DAYS = 180
# PE/PB 历史窗口：近 5 年
_PE_PB_HISTORY_YEARS = 5

# Phase 11 §3.4 默认正交化顺序：按 default_matrix 当前 state 权重降序动态生成
# （评审 R12-P2-6 修订：原 _DEFAULT_ORDER 硬编码 ["trend", "momentum",
# "mean_reversion", "value"] 在 DOWNTREND 状态下让 value 权重 0.70 被放最后，
# 与 Gram-Schmidt "高 ICIR/权重策略先正交化"的原则相反。生产 DailyPipeline 总
# 注入 FactorMonitorService 走 ICIR 排序，此 fallback 仅供脚本 / 单测路径使用。）


class ScoringService:
    """Service 层，负责编排 IO 和 Engine 调用。"""

    def __init__(
        self,
        repo: MarketDataRepository,
        universe_filter: UniverseFilter,
        strategies: list[BaseStrategy],
        scorer: Scorer,
        pool_manager: CandidatePoolManager,
        calendar: TradingCalendar,
        factor_monitor: FactorMonitorService | None = None,
    ) -> None:
        self._repo = repo
        self._universe_filter = universe_filter
        self._strategies = strategies
        self._scorer = scorer
        self._pool_manager = pool_manager
        self._calendar = calendar
        # Phase 11 §3.4：可选注入；P11-D 切换 DailyPipeline CP2 时统一注入。
        # 不注入时 score_universe 走冷启动 default_matrix 路径（与 V1.0 真机一致）。
        self._factor_monitor: FactorMonitorService | None = factor_monitor

    async def run_daily_scoring(
        self,
        trade_date: date,
        holding_codes: frozenset[str] | set[str] = frozenset(),
    ) -> list[CompositeScore]:
        """
        完整日度评分流程（SDD §8.1）：
        1. 仅加载 UniverseFilter 所需的轻量快照（snapshot_quotes + financials）
        2. UniverseFilter.filter() → universe
        3. 移除黑名单
        4. 获取当日市场状态
        5. 加载全量市场快照（adj_prices / pe_pb_history / 指数价格）
        6. asyncio.gather 并发调用各策略
        7. Scorer.aggregate() → composite_scores
        8. 查询白名单 + 上日池（淡出标记）
        9. CandidatePoolManager.compute_pool() → pool_entries
        10. 批量 upsert pool_entries（原子写入）
        11. 批量 upsert 淡出标记（原子写入）

        Phase 11 §6.3：``self._factor_monitor`` 已注入时自动切换到 5 步管线
        （``score_universe`` + ``write_candidate_pool``），调用形态不变；
        未注入时维持 Phase 4 旧路径（兼容入口，旧测试 + 冷启动）。
        """
        if self._factor_monitor is not None:
            return await self._run_phase11_pipeline(trade_date, holding_codes)

        # 1. 仅加载 UniverseFilter 所需的轻量快照（snapshot_quotes + financials，避免全量加载）
        ts_codes = await self._repo.get_active_stock_codes()
        if not ts_codes:
            logger.warning("scoring_no_active_stocks: trade_date=%s", trade_date)
            return []

        snapshot_quotes_raw, financials_raw, financials_n, avg_amount = (
            await self._build_filter_snapshot(trade_date, ts_codes)
        )

        # 2. UniverseFilter.filter()（P5-PRE-4: 含 avg_amount + financials_history）
        stock_info = snapshot_quotes_raw[
            ["is_st", "is_suspended", "list_date", "sw_industry_l1"]
        ].copy()
        daily_quotes_filter = snapshot_quotes_raw[["amount", "vol", "limit_up"]].copy()
        # 合并 20日均成交额（P5-PRE-4: F-7 优先使用 avg_amount 列）
        if not avg_amount.empty and "avg_amount" in avg_amount.columns:
            daily_quotes_filter["avg_amount"] = (
                avg_amount["avg_amount"].reindex(daily_quotes_filter.index)
            )

        universe = self._universe_filter.filter(
            stock_info=stock_info,
            financials=financials_raw,
            daily_quotes=daily_quotes_filter,
            today=trade_date,
            calendar=self._calendar,
            financials_history=financials_n if not financials_n.empty else None,
        )
        logger.info("scoring_universe: date=%s size=%d", trade_date, len(universe))

        # 3. 移除黑名单
        blacklist = await self._repo.get_blacklist_codes()
        universe = universe.difference(pd.Index(list(blacklist)))
        logger.info("scoring_universe_post_blacklist: size=%d", len(universe))

        if len(universe) == 0:
            return []

        # 完整 MarketSnapshot（仅含 universe 内的股票，C-09）
        market_data = await self._build_market_snapshot(trade_date, list(universe))

        # 4. 获取当日市场状态
        state_record = await self._repo.get_latest_market_state()
        if state_record is None:
            logger.warning("scoring_no_market_state: using OSCILLATION as fallback")
            from quantpilot.engine.market_state import MarketStateEnum
            market_state = MarketStateEnum.OSCILLATION
        else:
            from quantpilot.engine.market_state import MarketStateEnum
            market_state = MarketStateEnum(state_record.market_state)

        # 5. 并发调用各策略（asyncio.to_thread 包装纯函数）
        raw_scores_tuple = await asyncio.gather(
            *[asyncio.to_thread(s.score, universe, market_data) for s in self._strategies]
        )

        # 6. 构建 scores_by_name dict
        scores_by_name: dict[str, list] = {
            s.name: scores for s, scores in zip(self._strategies, raw_scores_tuple)
        }

        # 7. Scorer.aggregate_legacy()
        # Phase 11 P11-A2：run_daily_scoring 维持旧 Phase 4 权重矩阵路径（DailyPipeline
        # CP2 直接消费）。P11-D 改造 _cp2_scoring 切换到 score_universe，届时本方法
        # 仅保留作冷启动 fallback / 兼容入口。
        composite_scores = self._scorer.aggregate_legacy(market_state, scores_by_name)
        logger.info("scoring_composite_done: date=%s count=%d", trade_date, len(composite_scores))

        # 8. 查询白名单 + 上一交易日池（淡出标记）
        whitelist_codes = await self._repo.get_whitelist_codes()
        prev_date = self._calendar.get_prev_trade_date(trade_date, 1)
        prev_pool_codes = await self._repo.get_pool_codes(prev_date)

        # 9. 计算候选池
        pool_entries = self._pool_manager.compute_pool(
            composite_scores=composite_scores,
            holding_codes=holding_codes,
            whitelist_codes=whitelist_codes,
        )
        current_pool_codes = {e.ts_code for e in pool_entries}

        # 10. Upsert 入池标的（批量原子写入，无部分写入风险）
        await self._repo.upsert_candidate_pool_bulk([
            {
                "ts_code": entry.ts_code,
                "trade_date": trade_date,
                "composite_score": entry.composite_score,
                "trend_score": entry.trend_score,
                "momentum_score": entry.momentum_score,
                "reversion_score": entry.reversion_score,
                "value_score": entry.value_score,
                "market_state": entry.market_state,
                "in_pool": True,
                "is_holding": entry.is_holding,
            }
            for entry in pool_entries
        ])

        # 11. 淡出标记：上日在池，今日不在池 → in_pool=False（批量原子写入）
        fade_out_codes = prev_pool_codes - current_pool_codes
        current_market_state = state_record.market_state if state_record else None
        await self._repo.upsert_candidate_pool_bulk([
            {
                "ts_code": ts_code,
                "trade_date": trade_date,
                "composite_score": None,
                "trend_score": None,
                "momentum_score": None,
                "reversion_score": None,
                "value_score": None,
                "market_state": current_market_state,
                "in_pool": False,
                "is_holding": False,
            }
            for ts_code in fade_out_codes
        ])

        return composite_scores

    async def _build_filter_snapshot(
        self,
        trade_date: date,
        ts_codes: list[str],
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """加载 UniverseFilter 所需快照数据（轻量，避免加载 adj_prices/pe_pb_history）。

        返回：(snapshot_quotes, financials, financials_history, avg_amount)
        - financials_history: MultiIndex (ts_code, report_period)，供 F-5 两期检查
        - avg_amount: index=ts_code，columns=['avg_amount']，供 F-7 20日均成交额过滤
        """
        snapshot_quotes, financials, financials_history, avg_amount = await asyncio.gather(
            self._repo.get_snapshot_quotes(ts_codes, trade_date),
            self._repo.get_latest_financial(ts_codes, trade_date),
            self._repo.get_latest_n_financials(ts_codes, trade_date, n=2),
            self._repo.get_avg_amount(ts_codes, trade_date, window=20),
        )
        return snapshot_quotes, financials, financials_history, avg_amount

    async def _build_market_snapshot(
        self,
        trade_date: date,
        ts_codes: list[str],
    ) -> MarketSnapshot:  # type: ignore[return]
        """从 DB 构建 MarketSnapshot（含内部辅助键 _snapshot_quotes）。

        Phase 11 §3.0 P0-3 扩展：从 snapshot_quotes 派生 ``industry`` 字典
        （ts_code → sw_industry_l1）+ ``market_cap`` Series（ts_code →
        float_mkt_cap，单位元；neutralize 阶段取 log）。``beta`` V1.0 永远 None。
        """
        start_prices = trade_date - timedelta(days=_PRICE_WINDOW_DAYS)
        # V1.0 整改 Batch 2 — B2-3：用 timedelta 替代 date(yr-N, m, d)。
        # 闰年 2-29 在 date(yr-N, 2, 29) 非闰年时抛 ValueError → 5 年一次评分流水线降级。
        # 365 日近似覆盖 publish_date 历史窗口（每年 ≈ 365.25 日）。
        start_pepb = trade_date - timedelta(days=365 * _PE_PB_HISTORY_YEARS)

        # 并发查询所有需要的数据
        (
            adj_prices,
            snapshot_quotes,
            financials,
            pe_pb_history,
            index_history,
            market_cap_series,
        ) = await asyncio.gather(
            self._repo.get_adj_prices_bulk(ts_codes, start_prices, trade_date),
            self._repo.get_snapshot_quotes(ts_codes, trade_date),
            self._repo.get_latest_financial(ts_codes, trade_date),
            self._repo.get_pe_pb_history_bulk(ts_codes, start_pepb, trade_date),
            self._repo.get_index_history(_BENCHMARK_INDEX, start_prices, trade_date),
            self._repo.get_market_cap_pit(ts_codes, trade_date),
        )

        # 构建 index_adj_prices（wide：index=index_code, columns=trade_date）
        if not index_history.empty and "close" in index_history.columns:
            idx_hist = index_history.copy()
            idx_hist["adj_close"] = idx_hist["close"].astype(float)
            index_adj_prices = idx_hist.pivot_table(
                index="index_code", columns="trade_date", values="adj_close"
            )
        else:
            index_adj_prices = pd.DataFrame()

        # 将 pe_ttm/pb 并入 daily_quotes（ValueStrategy 使用）
        if not snapshot_quotes.empty and not financials.empty:
            fin_pepb = financials[["pe_ttm", "pb"]].reindex(snapshot_quotes.index)
            daily_quotes = snapshot_quotes.join(fin_pepb, how="left")
        else:
            daily_quotes = snapshot_quotes

        # 将 financials 与 stock_info sw_industry_l1 合并（MomentumStrategy 行业相对强度用）
        has_sw = "sw_industry_l1" in snapshot_quotes.columns
        if not snapshot_quotes.empty and not financials.empty and has_sw:
            financials = financials.copy()
            financials["sw_industry_l1"] = (
                snapshot_quotes["sw_industry_l1"].reindex(financials.index)
            )
        elif not snapshot_quotes.empty and has_sw:
            financials = snapshot_quotes[["sw_industry_l1"]].copy()

        # Phase 11 §3.0 P0-3：industry 字典 + market_cap Series
        industry: dict[str, str] = {}
        if has_sw and not snapshot_quotes.empty:
            sw_col = snapshot_quotes["sw_industry_l1"]
            for ts_code in sw_col.index:
                v = sw_col.loc[ts_code]
                if v is not None and not (isinstance(v, float) and pd.isna(v)):
                    industry[str(ts_code)] = str(v)

        market_cap: pd.Series | None = None
        if market_cap_series is not None and not market_cap_series.empty:
            market_cap = market_cap_series.astype(float)

        result: MarketSnapshot = {  # type: ignore[assignment]
            "trade_date": trade_date,
            "adj_prices": adj_prices,
            "daily_quotes": daily_quotes,
            "financials": financials,
            "pe_pb_history": pe_pb_history,
            "index_adj_prices": index_adj_prices,
            "industry": industry,
            "market_cap": market_cap,
            "beta": None,                          # V1.0 未实现，占位
            "_snapshot_quotes": snapshot_quotes,   # 供 run_daily_scoring 内部使用
        }
        return result

    # =====================================================================
    # Phase 11 §6.3：run_daily_scoring 切换到 5 步管线（DailyPipeline CP2 主入口）
    # =====================================================================

    async def _run_phase11_pipeline(
        self,
        trade_date: date,
        holding_codes: frozenset[str] | set[str] = frozenset(),
    ) -> list[CompositeScore]:
        """Phase 11 §6.3：``run_daily_scoring`` 在 ``factor_monitor`` 注入时走的新路径。

        - UniverseFilter + 黑名单 → universe
        - 当日 market_state（fallback OSCILLATION）
        - ``score_universe`` → composites（5 步管线）
        - ``write_candidate_pool`` → 入池 + fade-out 写入

        ``session`` 通过 ``self._repo.session`` 显式获取（同源于构造时的
        ``MarketDataRepository(session)``），透传给 ``score_universe`` 内
        ``FactorMonitorService.get_active_weights`` 查询当日 active 权重。
        """
        ts_codes = await self._repo.get_active_stock_codes()
        if not ts_codes:
            logger.warning(
                "scoring_no_active_stocks_phase11: trade_date=%s", trade_date
            )
            return []

        snapshot_quotes_raw, financials_raw, financials_n, avg_amount = (
            await self._build_filter_snapshot(trade_date, ts_codes)
        )

        stock_info = snapshot_quotes_raw[
            ["is_st", "is_suspended", "list_date", "sw_industry_l1"]
        ].copy()
        daily_quotes_filter = snapshot_quotes_raw[["amount", "vol", "limit_up"]].copy()
        if not avg_amount.empty and "avg_amount" in avg_amount.columns:
            daily_quotes_filter["avg_amount"] = (
                avg_amount["avg_amount"].reindex(daily_quotes_filter.index)
            )

        universe = self._universe_filter.filter(
            stock_info=stock_info,
            financials=financials_raw,
            daily_quotes=daily_quotes_filter,
            today=trade_date,
            calendar=self._calendar,
            financials_history=financials_n if not financials_n.empty else None,
        )
        blacklist = await self._repo.get_blacklist_codes()
        universe = universe.difference(pd.Index(list(blacklist)))
        logger.info(
            "scoring_universe_phase11: date=%s size=%d", trade_date, len(universe)
        )
        if len(universe) == 0:
            return []

        # before_date 用 trade_date+1 天，让 `<` 比较取到 trade_date 当日（或之前最近）
        # 的 state——而非全表最新（跨制度回测时不同 trade_date 必须各取各日 state）。
        from datetime import timedelta  # noqa: PLC0415

        state_record = await self._repo.get_latest_market_state(
            before_date=trade_date + timedelta(days=1)
        )
        if state_record is None:
            logger.warning(
                "scoring_no_market_state_phase11: using OSCILLATION as fallback"
            )
            market_state = MarketStateEnum.OSCILLATION
        else:
            market_state = MarketStateEnum(state_record.market_state)

        composites = await self.score_universe(
            self._repo.session, trade_date, list(universe), market_state,
        )

        await self.write_candidate_pool(
            composites, trade_date, holding_codes=holding_codes,
        )
        return composites

    # =====================================================================
    # Phase 11 §3.4：score_universe 5 步管线编排
    # =====================================================================

    async def score_universe(
        self,
        session: AsyncSession,
        trade_date: date,
        universe: list[str],
        market_state: MarketStateEnum,
    ) -> list[CompositeScore]:
        """5 步评分管线编排（Phase 11 §3.4）：构建 MarketSnapshot → 各策略
        ``compute_strategy_factors`` → ``FactorMonitorService.get_active_weights`` →
        ``Scorer.aggregate``。

        ``session`` 用于 ``factor_monitor.get_active_weights`` 查询当日 active 权重；
        ``factor_monitor`` 未注入时 fallback 到 default_matrix。

        本方法**不写 candidate_pool**——调用方按需调 ``write_candidate_pool``。
        """
        if not universe:
            return []

        snapshot = await self._build_market_snapshot(trade_date, universe)
        universe_idx = pd.Index(universe, name="ts_code")

        # 1. 收集每策略 raw 因子矩阵（并发 + asyncio.to_thread 包装纯函数）
        factor_dfs = await asyncio.gather(
            *[
                asyncio.to_thread(s.compute_strategy_factors, universe_idx, snapshot)
                for s in self._strategies
            ]
        )
        strategy_factors: dict[str, pd.DataFrame] = {
            s.name: df for s, df in zip(self._strategies, factor_dfs)
        }

        # 2. 取 active 权重（冷启动 fallback default_matrix）
        market_state_str = (
            market_state.value if hasattr(market_state, "value") else str(market_state)
        )
        if self._factor_monitor is not None:
            (
                weights_runtime, weights_source, order, hysteresis_status,
            ) = await self._factor_monitor.get_active_weights(
                session, trade_date, market_state_str,
            )
        else:
            from quantpilot.core.config_defaults import DEFAULT_STRATEGY_WEIGHTS
            weights_map = {
                "uptrend": DEFAULT_STRATEGY_WEIGHTS.uptrend,
                "downtrend": DEFAULT_STRATEGY_WEIGHTS.downtrend,
                "oscillation": DEFAULT_STRATEGY_WEIGHTS.oscillation,
            }
            default_w = DEFAULT_STRATEGY_WEIGHTS.oscillation
            weights_runtime = dict(weights_map.get(market_state_str, default_w))
            weights_source = "default_matrix"
            # 评审 R12-P2-6：按 default_matrix 当前 state 权重降序，让高权重策略先正交化
            order = sorted(weights_runtime, key=lambda s: weights_runtime[s], reverse=True)
            hysteresis_status = "stable"

        # 3. Scorer.aggregate（5 步管线）
        composites = self._scorer.aggregate(
            market_state=market_state,
            strategy_factors=strategy_factors,
            snapshot=snapshot,
            weights_runtime=weights_runtime,
            weights_source=weights_source,
            orthogonalize_order=order,
            hysteresis_status=hysteresis_status,
            single_strategy_mode=False,
        )
        logger.info(
            "score_universe_done: date=%s state=%s n_composites=%d weights_source=%s "
            "hysteresis=%s",
            trade_date, market_state_str, len(composites), weights_source, hysteresis_status,
        )
        return composites

    async def write_candidate_pool(
        self,
        composites: list[CompositeScore],
        trade_date: date,
        holding_codes: frozenset[str] | set[str] = frozenset(),
        whitelist_codes: frozenset[str] | set[str] | None = None,
    ) -> list[str]:
        """Phase 11 §3.4：写入 candidate_pool 新 6 列 + 兼容旧 4 列。

        - 先调 ``CandidatePoolManager.compute_pool`` 决定入池规则
        - 上 in_pool=True / False 行（fade-out 上日在池今日不在）批量 upsert
        - 返回入池的 ts_code 列表（保留顺序与 PoolEntry 一致）
        """
        if whitelist_codes is None:
            whitelist_codes = await self._repo.get_whitelist_codes()

        prev_date = self._calendar.get_prev_trade_date(trade_date, 1)
        prev_pool_codes = await self._repo.get_pool_codes(prev_date)

        pool_entries = self._pool_manager.compute_pool(
            composite_scores=composites,
            holding_codes=holding_codes,
            whitelist_codes=whitelist_codes,
        )
        current_pool_codes = {e.ts_code for e in pool_entries}

        # 索引 composites by ts_code 以补 Phase 11 新列
        composite_map = {c.ts_code: c for c in composites}

        in_pool_rows: list[dict] = []
        for entry in pool_entries:
            cs = composite_map.get(entry.ts_code)
            in_pool_rows.append({
                "ts_code": entry.ts_code,
                "trade_date": trade_date,
                "composite_score": entry.composite_score,
                "trend_score": entry.trend_score,
                "momentum_score": entry.momentum_score,
                "reversion_score": entry.reversion_score,
                "value_score": entry.value_score,
                "market_state": entry.market_state,
                "in_pool": True,
                "is_holding": entry.is_holding,
                # Phase 11 新列
                "composite_z": cs.composite_z if cs else None,
                "composite_pct_in_market": cs.composite_pct_in_market if cs else None,
                "weights_source": cs.weights_source if cs else None,
                "hysteresis_status": cs.hysteresis_status if cs else None,
                "score_breakdown_raw": cs.score_breakdown_raw if cs else None,
                "score_breakdown_residual": cs.score_breakdown_residual if cs else None,
                # Phase 12 新列（5 步管线 Step 1/2/4b 中间产物，P12 评审 P1-3/P1-4 修订）
                "factor_winsorized": cs.factor_winsorized if cs else None,
                "factor_neutralized": cs.factor_neutralized if cs else None,
                "factor_orthogonal": cs.factor_orthogonal if cs else None,
            })

        await self._repo.upsert_candidate_pool_bulk(in_pool_rows)

        # 淡出标记（fade-out）：上日在池，今日不在池
        fade_out_codes = prev_pool_codes - current_pool_codes
        if fade_out_codes:
            # 取当前 market_state 字符串（任一 composite 的 state，pool_entries 也有）
            current_market_state = None
            if pool_entries:
                current_market_state = pool_entries[0].market_state
            await self._repo.upsert_candidate_pool_bulk([
                {
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "composite_score": None,
                    "trend_score": None,
                    "momentum_score": None,
                    "reversion_score": None,
                    "value_score": None,
                    "market_state": current_market_state,
                    "in_pool": False,
                    "is_holding": False,
                    "composite_z": None,
                    "composite_pct_in_market": None,
                    "weights_source": None,
                    "hysteresis_status": None,
                    "score_breakdown_raw": None,
                    "score_breakdown_residual": None,
                    "factor_winsorized": None,
                    "factor_neutralized": None,
                    "factor_orthogonal": None,
                }
                for ts_code in fade_out_codes
            ])

        return [e.ts_code for e in pool_entries]
