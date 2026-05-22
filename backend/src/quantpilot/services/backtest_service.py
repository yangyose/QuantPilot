"""BacktestService：回测任务编排（Phase 8，SDD §7.7）。负责 IO，不含回测计算逻辑。"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.engine.backtest.engine import (
    BacktestConfig,
    BacktestDataBundle,
    BacktestEngine,
)
from quantpilot.models.system import BacktestResult, BacktestTask

logger = logging.getLogger(__name__)


class BacktestService:
    """
    编排 IO：
    ① 创建/更新 BacktestTask
    ② 预加载 BacktestDataBundle（adj_prices / stock_info / financials / hs300_history）
    ③ asyncio.to_thread(engine.run, config, data, progress_cb)
    ④ 写 BacktestResult
    ⑤ 更新 BacktestTask.status
    """

    def __init__(
        self, session: AsyncSession, engine: BacktestEngine | None = None,
    ) -> None:
        """构造 BacktestService。

        - REST 查询端点（`get_task`/`get_result`/`create_task`）不需要 engine，可传 None。
        - 仅 `run_task` 需要 engine；后台任务（Phase 10 §4.4 评审 C-02/C-03）会
          根据 `task.config_snapshot` 即时构造 BacktestEngine 并新建 BacktestService 调用。
        """
        self._session = session
        self._engine = engine

    async def create_task(
        self,
        config: BacktestConfig,
        engine_snapshot: dict | None = None,
    ) -> str:
        """创建 BacktestTask（status=PENDING），返回 task_id。

        Phase 10 §4.4：`engine_snapshot` 由端点层通过 `ConfigService.get_all_for_snapshot()`
        预取；写入 `backtest_task.config_snapshot` 作为本次回测参数标识，支持结果可复现。
        """
        task_id = str(uuid.uuid4())
        task = BacktestTask(
            task_id=task_id,
            status="PENDING",
            config_json=_config_to_dict(config),
            config_snapshot=engine_snapshot,
        )
        self._session.add(task)
        # 立即 commit：background task 在 get_db() 自动 commit 之前就可能启动，
        # 若仅 flush 则 task 对后台独立 session 不可见（FK 违约）。
        await self._session.commit()
        logger.info("backtest_task_created task_id=%s", task_id)
        return task_id

    async def get_task(self, task_id: str) -> BacktestTask | None:
        return (await self._session.execute(
            select(BacktestTask).where(BacktestTask.task_id == task_id)
        )).scalar_one_or_none()

    async def get_result(self, task_id: str) -> BacktestResult | None:
        return (await self._session.execute(
            select(BacktestResult).where(BacktestResult.task_id == task_id)
        )).scalar_one_or_none()

    async def run_task(
        self,
        task_id: str,
        config: BacktestConfig,
        progress_cb: Callable[[str, int, float], None] | None = None,
    ) -> None:
        """
        异步编排主流程：
        ① 更新状态 RUNNING
        ② 预加载 BacktestDataBundle
        ③ 在线程池中执行 engine.run()
        ④ 写 BacktestResult
        ⑤ 更新状态 SUCCESS
        异常时更新 FAILED。
        """
        # ① 更新 RUNNING 并立即提交，保证轮询端点可见（flush 在 READ COMMITTED 下不可见）
        await self._update_status(task_id, "RUNNING", started_at=datetime.now(tz=timezone.utc))
        await self._session.commit()

        if self._engine is None:
            raise RuntimeError(
                "BacktestService.run_task 需注入 BacktestEngine"
                "（应由 _run_backtest_bg 根据 task.config_snapshot 构造）"
            )

        # R13-P1-1：BACKTEST_QUEUE_DEPTH inc/dec —— 用 try/finally 保证异常分支也释放
        from quantpilot.core.metrics import BACKTEST_QUEUE_DEPTH
        BACKTEST_QUEUE_DEPTH.inc()
        try:
            # ② 预加载历史数据
            data = await self._load_data_bundle(config)

            # ③ 线程池执行（同步 CPU 密集）
            result = await asyncio.to_thread(self._engine.run, config, data, progress_cb)

            # ④ 写 BacktestResult
            daily_nav_dict = {
                str(d): float(v) for d, v in zip(result.daily_nav.index, result.daily_nav.values)
            }
            br = BacktestResult(
                task_id=task_id,
                performance_json=result.performance,
                daily_nav_json=daily_nav_dict,
                disclaimer=result.disclaimer,
            )
            self._session.add(br)

            # ⑤ 更新 SUCCESS
            await self._update_status(task_id, "SUCCESS", finished_at=datetime.now(tz=timezone.utc))
            await self._session.commit()
            logger.info("backtest_task_success task_id=%s", task_id)

        except Exception as exc:
            logger.exception("backtest_task_failed task_id=%s", task_id)
            await self._update_status(
                task_id, "FAILED",
                finished_at=datetime.now(tz=timezone.utc),
                error_msg=str(exc),
            )
            await self._session.commit()
        finally:
            # R13-P1-1：成功/失败/异常分支都释放 queue depth
            BACKTEST_QUEUE_DEPTH.dec()

    async def _update_status(
        self,
        task_id: str,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        error_msg: str | None = None,
    ) -> None:
        values: dict = {"status": status}
        if started_at is not None:
            values["started_at"] = started_at
        if finished_at is not None:
            values["finished_at"] = finished_at
        if error_msg is not None:
            values["error_msg"] = error_msg
        await self._session.execute(
            sql_update(BacktestTask).where(BacktestTask.task_id == task_id).values(**values)
        )
        await self._session.flush()

    async def _load_data_bundle(self, config: BacktestConfig) -> BacktestDataBundle:
        """
        预加载全量历史数据。

        V1.0 整改 Batch 3 — B3-1/3/5/6/8 扩展：
        - adj_prices：close × adj_factor 后复权（同前）
        - daily_quotes：B3-1 完整字段（close/open/limit/suspended/st/amount）
        - stock_info：B3-6 含 delist_date；is_st/is_suspended 不再 hardcode False
        - financials：含 publish_date 供 PIT 切片；B3-7 主循环按 trade_date 切片
        - pe_pb_history：B3-3 真实加载（ValueStrategy 真实分位数）
        - index_adj_prices：B3-3 HS300 累计后复权 close（Momentum.rs_6m）
        - hs300_history：保持 OHLC 供 MarketStateEngine 使用
        - DataValidator：B3-8 daily_quotes 加载后走 validate_daily_quotes
        """
        from quantpilot.data.validators import DataValidator
        from quantpilot.models.market import DailyQuote, IndexHistory, StockInfo

        # 前置历史窗口：TrendStrategy 需要 65 日 MA60，MomentumStrategy 需要 61 日 return_3m，
        # 取 130 日历天（≈ 90 交易日）确保各策略均能计算有效因子值。
        lookback_start = config.start_date - timedelta(days=130)

        # ── 1. daily_quotes 全字段加载（B3-1） ───────────────────────────────
        dq_rows = (await self._session.execute(
            select(DailyQuote)
            .where(DailyQuote.trade_date >= lookback_start)
            .where(DailyQuote.trade_date <= config.end_date)
        )).scalars().all()
        if dq_rows:
            dq_df = pd.DataFrame([{
                "trade_date": r.trade_date,
                "ts_code": r.ts_code,
                "open": float(r.open) if r.open is not None else None,
                "high": float(r.high) if r.high is not None else None,
                "low": float(r.low) if r.low is not None else None,
                "close": float(r.close) if r.close is not None else None,
                "vol": float(r.vol) if r.vol is not None else 0,
                "amount": float(r.amount) if r.amount is not None else 0.0,
                "adj_factor": float(r.adj_factor) if r.adj_factor is not None else 1.0,
                "is_suspended": bool(r.is_suspended),
                "is_st": bool(r.is_st),
                "limit_up": bool(r.limit_up),
                "limit_down": bool(r.limit_down),
            } for r in dq_rows])

            # B3-8：DataValidator 校验 + 剔除无效行
            validator = DataValidator()
            validation = validator.validate_daily_quotes(dq_df, prev_count=len(dq_df))
            if len(validation.invalid_rows) > 0:
                logger.warning(
                    "backtest_data_validator_drops invalid_rows=%d reason=%s",
                    len(validation.invalid_rows),
                    validation.warnings or validation.errors,
                )
                dq_df = dq_df.drop(index=validation.invalid_rows)

            dq_df["adj_close"] = dq_df["close"] * dq_df["adj_factor"]
            adj_prices = dq_df.pivot(
                index="trade_date", columns="ts_code", values="adj_close"
            )
            adj_prices.index = pd.to_datetime(adj_prices.index)
            dq_ts_codes: set[str] = set(dq_df["ts_code"].unique())

            # daily_quotes 完整字段保留（B3-1 主循环按 trade_date+ts_code 切片用）
            daily_quotes = dq_df.set_index(["trade_date", "ts_code"]).sort_index()
        else:
            dq_df = pd.DataFrame()
            adj_prices = pd.DataFrame()
            dq_ts_codes = set()
            daily_quotes = pd.DataFrame()

        # ── 2. stock_info（B3-6 含 delist_date） ─────────────────────────────
        stock_rows = (await self._session.execute(
            select(StockInfo)
        )).scalars().all()
        si_map: dict[str, dict] = {
            r.ts_code: {
                "list_date": r.list_date,
                "delist_date": r.delist_date,  # B3-6：退市日时点过滤
                "sw_industry_l1": r.sw_industry_l1,
            }
            for r in stock_rows
        }
        # 补入 daily_quote 中有行情但 stock_info 缺失的股票
        _DEFAULT_LIST_DATE = date(2000, 1, 1)
        for ts_code in dq_ts_codes:
            if ts_code not in si_map:
                si_map[ts_code] = {
                    "list_date": _DEFAULT_LIST_DATE,
                    "delist_date": None,
                    "sw_industry_l1": None,
                }
        for v in si_map.values():
            if v["list_date"] is None:
                v["list_date"] = _DEFAULT_LIST_DATE
        if si_map:
            stock_info = pd.DataFrame.from_dict(si_map, orient="index")
            stock_info.index.name = "ts_code"
        else:
            stock_info = pd.DataFrame()

        # ── 3. financials ─────────────────────────────────────────────────────
        from quantpilot.models.market import FinancialData
        fin_rows = (await self._session.execute(
            select(FinancialData)
        )).scalars().all()
        if fin_rows:
            financials = pd.DataFrame([{
                "ts_code": r.ts_code,
                "report_period": r.report_period,
                "publish_date": r.publish_date,
                "net_profit_yoy": float(r.net_profit_yoy) if r.net_profit_yoy is not None else None,
                "total_equity": float(r.total_equity) if r.total_equity is not None else None,
                "debt_to_asset": float(r.debt_to_asset) if r.debt_to_asset is not None else None,
                "pe_ttm": float(r.pe_ttm) if r.pe_ttm is not None else None,
                "pb": float(r.pb) if r.pb is not None else None,
            } for r in fin_rows])
            if not financials.empty:
                financials = financials.set_index(["ts_code", "report_period"])
        else:
            financials = pd.DataFrame()

        # ── 3b. pe_pb_history（B3-3 ValueStrategy 真实分位数） ───────────────
        if fin_rows:
            pe_pb_history = pd.DataFrame([{
                "ts_code": r.ts_code,
                "publish_date": r.publish_date,
                "pe_ttm": float(r.pe_ttm) if r.pe_ttm is not None else None,
                "pb": float(r.pb) if r.pb is not None else None,
            } for r in fin_rows])
            if not pe_pb_history.empty:
                pe_pb_history = (
                    pe_pb_history.set_index(["ts_code", "publish_date"]).sort_index()
                )
        else:
            pe_pb_history = pd.DataFrame()

        # ── 4. hs300_history（OHLC + 累计后复权 close） ──────────────────────
        hs300_rows = (await self._session.execute(
            select(IndexHistory)
            .where(IndexHistory.index_code == "000300.SH")
            .where(IndexHistory.trade_date >= lookback_start)
            .where(IndexHistory.trade_date <= config.end_date)
            .order_by(IndexHistory.trade_date)
        )).scalars().all()
        if hs300_rows:
            hs300_history = pd.DataFrame([{
                "trade_date": r.trade_date,
                "open": float(r.open) if r.open is not None else None,
                "high": float(r.high) if r.high is not None else None,
                "low": float(r.low) if r.low is not None else None,
                "close": float(r.close) if r.close is not None else None,
                "vol": float(r.vol) if r.vol is not None else None,
            } for r in hs300_rows])
            # B3-3：index_adj_prices 提供给 Momentum 相对强度计算
            index_adj_prices = hs300_history.set_index("trade_date")["close"].copy()
            index_adj_prices.index = pd.to_datetime(index_adj_prices.index)
        else:
            hs300_history = pd.DataFrame()
            index_adj_prices = pd.Series(dtype=float)

        return BacktestDataBundle(
            adj_prices=adj_prices,
            stock_info=stock_info,
            financials=financials,
            hs300_history=hs300_history,
            daily_quotes=daily_quotes,
            pe_pb_history=pe_pb_history,
            index_adj_prices=index_adj_prices,
        )


def _config_to_dict(config: BacktestConfig) -> dict:
    return {
        "start_date": str(config.start_date),
        "end_date": str(config.end_date),
        "initial_capital": config.initial_capital,
        "strategy_config": config.strategy_config,
        "account_config": config.account_config,
        "commission_rate": config.commission_rate,
        "stamp_tax_rate": config.stamp_tax_rate,
        "slippage_rate": config.slippage_rate,
    }
