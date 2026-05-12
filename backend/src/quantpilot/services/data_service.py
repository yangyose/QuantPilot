from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from quantpilot.core.database import AsyncSessionLocal
from quantpilot.data.adapters.base import DataSourceAdapter
from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.repository import MarketDataRepository
from quantpilot.data.validators import DataValidator

logger = logging.getLogger(__name__)

_TARGET_INDEXES = ["000001.SH", "000300.SH", "000905.SH", "399006.SZ"]


@dataclass
class IngestResult:
    """ingest_daily() 返回值。
    snapshot_version 用于 DailyPipeline CP1 幂等性保障（system_design §2.2）。
    """

    trade_date: date
    quote_count: int
    financial_count: int
    snapshot_version: str  # SHA256(trade_date:quote_count:financial_count)
    errors: list[str] = field(default_factory=list)


def _build_st_map(
    namechange_df: object, trade_dates: list[date]
) -> dict[date, set[str]]:
    """从历史改名记录构建 {trade_date: st_ts_codes} 映射。

    只保留 name 含 'ST' 的记录；end_date=None 表示该名称至今有效。
    namechange_df: pd.DataFrame（避免循环导入，使用 object 类型注解）。
    """
    st_map: dict[date, set[str]] = {td: set() for td in trade_dates}
    if namechange_df.empty or "ts_code" not in namechange_df.columns:
        return st_map

    st_records = namechange_df[namechange_df["name"].str.contains("ST", na=False)]
    for _, row in st_records.iterrows():
        ts_code: str = row["ts_code"]
        period_start: date | None = row.get("start_date")
        period_end: date | None = row.get("end_date")  # None = still current
        if period_start is None:
            continue
        for td in trade_dates:
            if td < period_start:
                continue
            if period_end is not None and td > period_end:
                continue
            st_map[td].add(ts_code)
    return st_map


def _make_snapshot_version(trade_date: date, quote_count: int, financial_count: int) -> str:
    raw = f"{trade_date}:{quote_count}:{financial_count}"
    return hashlib.sha256(raw.encode()).hexdigest()


class DataService:
    """数据采集流程编排器，被 API 端点和调度器调用。"""

    def __init__(
        self,
        adapter: DataSourceAdapter,
        validator: DataValidator,
        repo: MarketDataRepository,
        calendar: TradingCalendar,
    ) -> None:
        self._adapter = adapter
        self._validator = validator
        self._repo = repo
        self._calendar = calendar

    async def ingest_daily(
        self,
        trade_date: date,
        _st_codes: set[str] | None = None,
        _skip_indexes: bool = False,
        _repo: MarketDataRepository | None = None,
    ) -> IngestResult:
        """单日全量采集流程。

        1. 校验 trade_date 是否为交易日
        2. fetch_daily_quotes → 校验 → upsert
        3. fetch_financial_data(as_of_date=trade_date) → 校验（PIT）→ upsert
        4. fetch_index_history for 4 indexes → upsert（_skip_indexes=True 时跳过）
        5. fetch_index_components for 4 indexes → upsert（_skip_indexes=True 时跳过）
        6. 生成 snapshot_version
        7. 返回 IngestResult

        校验失败（完整性不足）时：中止入库，errors 非空，snapshot_version 仍生成。
        _skip_indexes=True：供 ingest_history 使用，索引由外层批量拉取，避免 N×4 次重复调用。
        _repo：可选 repo 覆盖；ingest_history 用 per-day session 构造本地 repo 传入实现
        Bug 5 修复（per-day 原子性）；默认 None 时使用注入的 self._repo（直接 API 调用场景）。
        """
        repo = _repo if _repo is not None else self._repo
        errors: list[str] = []
        quote_count = 0
        financial_count = 0

        if not self._calendar.is_trade_date(trade_date):
            raise ValueError(f"{trade_date} is not a trading date")

        # ── 1. 日线行情 ──────────────────────────────────────────────────────
        try:
            quote_df = await self._adapter.fetch_daily_quotes(trade_date)
            # 历史回填时注入 namechange 缓存，按 PIT 还原 is_st（避免所有日期 is_st=False）
            if _st_codes is not None and "is_st" in quote_df.columns:
                quote_df["is_st"] = quote_df["ts_code"].isin(_st_codes)
            prev_count = await repo.get_active_stock_codes()
            vr = self._validator.validate_daily_quotes(quote_df, len(prev_count))
            if not vr.is_valid:
                errors.extend(vr.errors)
                logger.error(
                    "daily_quotes_validation_failed",
                    extra={"trade_date": str(trade_date), "errors": vr.errors},
                )
            else:
                quote_count = await repo.upsert_daily_quotes(quote_df)
                if vr.warnings:
                    logger.warning(
                        "daily_quotes_warnings",
                        extra={"trade_date": str(trade_date), "warnings": vr.warnings},
                    )
        except Exception as exc:
            errors.append(f"daily_quotes error: {exc}")
            logger.exception("daily_quotes_fetch_failed", extra={"trade_date": str(trade_date)})

        # ── 2. 财务数据（PIT，as_of_date=trade_date）────────────────────────
        try:
            fin_df = await self._adapter.fetch_financial_data(as_of_date=trade_date)
            vr_fin = self._validator.validate_financial_data(fin_df, as_of_date=trade_date)
            if vr_fin.errors:
                errors.extend(vr_fin.errors)
                logger.error(
                    "financial_data_pit_violations",
                    extra={"trade_date": str(trade_date), "errors": vr_fin.errors},
                )
            # 行级过滤：丢弃 invalid_rows，其余行仍入库
            valid_fin_df = fin_df.drop(index=vr_fin.invalid_rows)
            if not valid_fin_df.empty:
                financial_count = await repo.upsert_financial_data(valid_fin_df)
        except Exception as exc:
            errors.append(f"financial_data error: {exc}")
            logger.exception(
                "financial_data_fetch_failed", extra={"trade_date": str(trade_date)}
            )

        if not _skip_indexes:
            # ── 3. 指数历史 ──────────────────────────────────────────────────
            for idx_code in _TARGET_INDEXES:
                try:
                    idx_df = await self._adapter.fetch_index_history(
                        idx_code, trade_date, trade_date
                    )
                    if not idx_df.empty:
                        await repo.upsert_index_history(idx_df)
                except Exception as exc:
                    errors.append(f"index_history[{idx_code}] error: {exc}")
                    logger.exception(
                        "index_history_failed",
                        extra={"index_code": idx_code, "trade_date": str(trade_date)},
                    )

            # ── 4. 指数成分股 ────────────────────────────────────────────────
            for idx_code in _TARGET_INDEXES:
                try:
                    components = await self._adapter.fetch_index_components(idx_code, trade_date)
                    if components:
                        await repo.upsert_index_components(
                            idx_code, trade_date, components
                        )
                    else:
                        logger.warning(
                            "index_components_empty",
                            extra={"index_code": idx_code, "trade_date": str(trade_date)},
                        )
                except Exception as exc:
                    errors.append(f"index_components[{idx_code}] error: {exc}")
                    logger.exception(
                        "index_components_failed",
                        extra={"index_code": idx_code, "trade_date": str(trade_date)},
                    )

        snapshot_version = _make_snapshot_version(trade_date, quote_count, financial_count)
        return IngestResult(
            trade_date=trade_date,
            quote_count=quote_count,
            financial_count=financial_count,
            snapshot_version=snapshot_version,
            errors=errors,
        )

    async def ingest_history(
        self,
        start_date: date,
        end_date: date,
        progress_callback=None,
        _repo: MarketDataRepository | None = None,
    ) -> dict:
        """历史数据回填，支持断点续传（跳过已入库的交易日）。

        生产路径（`_repo` 默认 None）：per-day 独立 `AsyncSessionLocal`——当日 errors
        非空整日 rollback、否则整日 commit（Bug 5 修复，见 phase2 §4.8 / §8.3）。

        测试注入路径（`_repo` 显式传入）：所有交易日共用注入的 repo（通常来自集成
        测试 `db_session` fixture），跳过 per-day session 创建。这样测试的事务隔离
        + rollback 不被绕过；代价是这条路径不覆盖 per-day 原子性回滚逻辑，需要
        独立的 INT-DATA-** 用例显式构造混合失败场景验证 day_session.rollback。

        遇到单日失败：记录日志，继续下一日（不中断整批）。
        返回 {success_count, fail_count, failed_dates}
        """
        trade_dates = self._calendar.get_trade_dates(start_date, end_date)
        # Bug 6 修复：必须 daily_quote ∩ financial_data 两表都齐才算"已完成"，
        # 否则 savepoint 半 commit 的日期会被错误跳过 financial 永远补不上
        ingested_dates = await self._repo.get_fully_ingested_dates(start_date, end_date)

        # 回填前构建 is_st PIT 映射（namechange 历史缓存）
        # RM-16 修复：fetch_namechange 的 start/end 是 Tushare 公告日期（ann_date），
        # 仅传 ingest 窗口 [start_date, end_date] 只能拿到"窗口内被宣告改名"的股票。
        # 早就已经叫 *ST 的股票（公告在几年前）会全部缺失 → st_map 几乎空 →
        # daily_quote.is_st 全部 FALSE。把回溯起点放到 5 年前（覆盖绝大多数当前
        # ST 股票的命名公告日；3 年净亏损被实施 ST，超 5 年通常已强制退市）。
        # 【降级说明】若 5 年内有 > 5000 条 namechange（Tushare 单页上限）会被截断
        # 最旧的部分；V1.5 按年分批拉取彻底解决。
        ns_lookback_start = start_date - timedelta(days=365 * 5)
        st_map: dict[date, set[str]] = {}
        try:
            namechange_df = await self._adapter.fetch_namechange(
                ns_lookback_start, end_date
            )
            st_map = _build_st_map(namechange_df, trade_dates)
            logger.info(
                "namechange_cache_built",
                extra={
                    "trade_days": len(trade_dates),
                    "namechange_rows": len(namechange_df),
                    "lookback_start": str(ns_lookback_start),
                    "st_codes_total": sum(len(v) for v in st_map.values()),
                },
            )
        except Exception as exc:
            logger.warning(
                "namechange_fetch_failed_is_st_will_be_false",
                extra={"adapter": type(self._adapter).__name__, "error": str(exc)},
            )

        # ── 指数历史批量拉取（4 次调用，不随交易日数量线性增长）──────────────
        for idx_code in _TARGET_INDEXES:
            try:
                idx_df = await self._adapter.fetch_index_history(
                    idx_code, start_date, end_date
                )
                if not idx_df.empty:
                    await self._repo.upsert_index_history(idx_df)
            except Exception as exc:
                logger.exception(
                    "index_history_batch_failed",
                    extra={"index_code": idx_code, "start": str(start_date), "end": str(end_date)},
                )
                logger.warning("index_history_batch error: %s", exc)

        # ── 指数成分股批量拉取（Bug 7a 修复）─────────────────────────────────
        # index_weight 为月度稀疏数据（仅 rebalance 日有 snapshot），range query 一次取全
        for idx_code in _TARGET_INDEXES:
            try:
                snapshots = await self._adapter.fetch_index_components_range(
                    idx_code, start_date, end_date
                )
                for snap_date, components in snapshots.items():
                    if components:
                        await self._repo.upsert_index_components(
                            idx_code, snap_date, components
                        )
            except NotImplementedError:
                # AKShare 等不支持 range 批量，记录降级
                logger.warning(
                    "index_components_range_not_implemented",
                    extra={"adapter": type(self._adapter).__name__, "index_code": idx_code},
                )
            except Exception:
                logger.exception(
                    "index_components_batch_failed",
                    extra={
                        "index_code": idx_code,
                        "start": str(start_date),
                        "end": str(end_date),
                    },
                )

        success_count = 0
        fail_count = 0
        failed_dates: list[date] = []

        for i, td in enumerate(trade_dates):
            # 断点续传：仅跳过实际已入库的日期，避免 MAX(trade_date) 掩盖中间数据缺口
            if td in ingested_dates:
                success_count += 1
                if progress_callback:
                    progress_callback(i + 1, len(trade_dates))
                continue

            if _repo is not None:
                # 测试注入路径：共用注入 repo（含集成测试 rollback 隔离的 session）
                try:
                    result = await self.ingest_daily(
                        td, _st_codes=st_map.get(td), _skip_indexes=True, _repo=_repo,
                    )
                    if result.errors:
                        fail_count += 1
                        failed_dates.append(td)
                        logger.error(
                            "ingest_history_day_failed",
                            extra={"trade_date": str(td), "errors": result.errors},
                        )
                    else:
                        success_count += 1
                except Exception as exc:
                    fail_count += 1
                    failed_dates.append(td)
                    logger.exception(
                        "ingest_history_day_exception",
                        extra={"trade_date": str(td), "error": str(exc)},
                    )
            else:
                # 生产路径（Bug 5 修复）：per-day 独立 session，要么整天 commit 要么
                # 整天 rollback。修复前共用 outer session，asyncpg savepoint 让单条
                # upsert 失败只回滚自己那条，造成"daily_quote 进库 financial 全空"。
                async with AsyncSessionLocal() as day_session:
                    day_repo = MarketDataRepository(day_session)
                    try:
                        result = await self.ingest_daily(
                            td, _st_codes=st_map.get(td), _skip_indexes=True, _repo=day_repo,
                        )
                        if result.errors:
                            await day_session.rollback()
                            fail_count += 1
                            failed_dates.append(td)
                            logger.error(
                                "ingest_history_day_failed",
                                extra={"trade_date": str(td), "errors": result.errors},
                            )
                        else:
                            await day_session.commit()
                            success_count += 1
                    except Exception as exc:
                        await day_session.rollback()
                        fail_count += 1
                        failed_dates.append(td)
                        logger.exception(
                            "ingest_history_day_exception",
                            extra={"trade_date": str(td), "error": str(exc)},
                        )

            # 批次间 sleep，避免触发 Tushare 速率限制（设计文档 §4.2）
            await asyncio.sleep(0.3)

            if progress_callback:
                progress_callback(i + 1, len(trade_dates))

        return {
            "success_count": success_count,
            "fail_count": fail_count,
            "failed_dates": failed_dates,
        }

    # ─── P5-PRE-1: 历史财务/行业补录方法 ───────────────────────────────────────

    async def refresh_industry_classification(self) -> int:
        """重新获取全市场申万行业分类，更新 stock_info.sw_industry_l1/l2。
        调用 adapter.fetch_stock_industry() → upsert_stock_list()。
        幂等，可重复调用。返回更新行数。
        """
        df = await self._adapter.fetch_stock_industry()
        if df.empty:
            return 0
        upserted = await self._repo.upsert_stock_list(df)
        logger.info("refresh_industry_classification: updated=%d", upserted)
        return upserted

    async def refresh_financials_full(
        self,
        ts_codes: list[str] | None = None,
        batch_size: int = 50,
    ) -> dict:
        """按股票逐一补录 ROE/成长性指标和 total_equity（净资产）。
        ts_codes=None → 取全部活跃股票（is_active=True）。
        每批 batch_size 只，批次间 sleep 0.3s（避免 Tushare 速率限制）。
        返回 {success_count, fail_count, failed_codes}。

        【降级说明】首次部署需手动调用此方法完成初始化（见 phase5_signals.md §2 P5-PRE-1
        降级说明）；P5-PRE-2 季度调度任务上线后，后续更新自动维护，无需再次手动操作。
        """
        if ts_codes is None:
            ts_codes = await self._repo.get_active_stock_codes()

        success_count = 0
        fail_count = 0
        failed_codes: list[str] = []

        for i in range(0, len(ts_codes), batch_size):
            batch = ts_codes[i : i + batch_size]
            for ts_code in batch:
                try:
                    fin_df = await self._adapter.fetch_financial_by_stock(ts_code)
                    if not fin_df.empty:
                        await self._repo.upsert_financial_data(fin_df)
                    bal_df = await self._adapter.fetch_balance_sheet(ts_code)
                    if not bal_df.empty:
                        await self._repo.upsert_financial_data(bal_df)
                    success_count += 1
                except Exception as exc:
                    fail_count += 1
                    failed_codes.append(ts_code)
                    logger.warning(
                        "refresh_financials_full_stock_failed: ts_code=%s error=%s",
                        ts_code, str(exc),
                    )
            await asyncio.sleep(0.3)

        logger.info(
            "refresh_financials_full: success=%d fail=%d", success_count, fail_count
        )
        return {
            "success_count": success_count,
            "fail_count": fail_count,
            "failed_codes": failed_codes,
        }

    async def refresh_stock_list(self) -> dict:
        """刷新全市场股票基础信息（含退市股）"""
        df = await self._adapter.fetch_stock_list()
        upserted = await self._repo.upsert_stock_list(df)
        return {"upserted_count": upserted}

    async def fetch_dividends(self, trade_date: date) -> int:
        """从 Tushare 获取 trade_date 除权的股票分红数据，自动写入账户分红记录（Phase 7 D-07）。

        对每只当日除权且账户中有持仓的股票，调用 AccountService.record_dividend()。
        返回处理的分红笔数（0 = 当日无除权记录或无对应持仓）。

        数据源：TushareAdapter.fetch_dividend_data(trade_date)
        仅处理 ex_date == trade_date 的记录（精确日期匹配）。
        """
        from sqlalchemy import select

        from quantpilot.models.account import Position
        from quantpilot.services.account_service import AccountService

        df = await self._adapter.fetch_dividend_data(trade_date)
        if df.empty:
            logger.info("fetch_dividends_skip: no dividend data for %s", trade_date)
            return 0

        # 取所有账户持仓（跨账户）
        session = self._repo._session
        positions: list[Position] = list(
            (await session.execute(select(Position))).scalars().all()
        )
        if not positions:
            return 0

        # ts_code → list[(account_id, shares)]
        holding_map: dict[str, list[tuple[int, int]]] = {}
        for p in positions:
            if p.shares > 0:
                holding_map.setdefault(p.ts_code, []).append((p.account_id, p.shares))

        account_service = AccountService(session)
        processed = 0

        for _, row in df.iterrows():
            ts_code: str = row["ts_code"]
            cash_div: float = float(row["cash_div"])  # 每股分红额（元）

            holdings = holding_map.get(ts_code)
            if not holdings:
                continue

            for account_id, shares in holdings:
                total_div = cash_div * shares
                try:
                    await account_service.record_dividend(
                        account_id=account_id,
                        ts_code=ts_code,
                        amount=total_div,
                        trade_date=trade_date,
                        note=f"自动分红：每股 {cash_div:.4f} 元 × {shares} 股",
                    )
                    processed += 1
                except Exception:
                    logger.warning(
                        "fetch_dividends_record_failed: ts_code=%s account=%d",
                        ts_code, account_id, exc_info=True,
                    )

        logger.info("fetch_dividends_done: trade_date=%s processed=%d", trade_date, processed)
        return processed

    async def get_status(self) -> dict:
        """返回数据新鲜度状态，补算 is_up_to_date（latest_quote_date 是否等于最近交易日）"""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        raw = await self._repo.get_data_status()
        today = datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()
        try:
            most_recent = (
                today if self._calendar.is_trade_date(today)
                else self._calendar.get_prev_trade_date(today)
            )
            raw["is_up_to_date"] = raw.get("latest_quote_date") == most_recent
        except Exception:
            raw["is_up_to_date"] = False
        return raw
