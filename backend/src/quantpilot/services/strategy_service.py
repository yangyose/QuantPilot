"""ScoringService：日度评分编排（Phase 4）。"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

import pandas as pd

from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.pool import CandidatePoolManager
from quantpilot.engine.scorer import CompositeScore, Scorer
from quantpilot.engine.strategies.base import BaseStrategy, MarketSnapshot
from quantpilot.engine.universe import UniverseFilter

logger = logging.getLogger(__name__)

# 沪深300 作为基准指数
_BENCHMARK_INDEX = "000300.SH"
# 后复权价格窗口：近 180 日历天 ≈ 120 交易日（覆盖 MomentumStrategy 6M 窗口）
_PRICE_WINDOW_DAYS = 180
# PE/PB 历史窗口：近 5 年
_PE_PB_HISTORY_YEARS = 5


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
    ) -> None:
        self._repo = repo
        self._universe_filter = universe_filter
        self._strategies = strategies
        self._scorer = scorer
        self._pool_manager = pool_manager
        self._calendar = calendar

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
        """
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

        # 7. Scorer.aggregate()
        composite_scores = self._scorer.aggregate(market_state, scores_by_name)
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
        """从 DB 构建 MarketSnapshot（含内部辅助键 _snapshot_quotes）。"""
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
        ) = await asyncio.gather(
            self._repo.get_adj_prices_bulk(ts_codes, start_prices, trade_date),
            self._repo.get_snapshot_quotes(ts_codes, trade_date),
            self._repo.get_latest_financial(ts_codes, trade_date),
            self._repo.get_pe_pb_history_bulk(ts_codes, start_pepb, trade_date),
            self._repo.get_index_history(_BENCHMARK_INDEX, start_prices, trade_date),
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

        result: MarketSnapshot = {  # type: ignore[assignment]
            "trade_date": trade_date,
            "adj_prices": adj_prices,
            "daily_quotes": daily_quotes,
            "financials": financials,
            "pe_pb_history": pe_pb_history,
            "index_adj_prices": index_adj_prices,
            "_snapshot_quotes": snapshot_quotes,  # 供 run_daily_scoring 内部使用
        }
        return result
