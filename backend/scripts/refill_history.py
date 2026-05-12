"""强制重灌历史数据（财务 + 行情 + 指数 + 成分股 + is_st PIT）。

应用场景（RM-12 真机验收 §2.9）：上一轮回填时上游 bug 把数据写脏后又被部分清理，
留下「daily_quote 有 / financial_data 行存在但 roe 等关键列全 NULL / is_st 全 FALSE」
的混合状态。Bug 6 修复后的 get_fully_ingested_dates 会把这些日期判为已完成而跳过，
正常 ingest_history 重跑不会刷新它们。本脚本：
  1. DELETE financial_data + daily_quote + index_history + index_components 范围内行
  2. 重新调 DataService.ingest_history（走 Bug 9 修后的 NaN→NULL + RM-16 修后的
     5 年 namechange 回溯 + RM-15 修后的 dividend 接口名）
  3. 输出每张表重灌行数 + roe 非空率 + is_st=TRUE 行数等关键质量指标

用法：
  # 在 backend/ 目录
  uv run python scripts/refill_history.py --start 2026-01-15 --end 2026-05-08

  # 可选 --dry-run 只删不重灌；--skip-confirm 跳过交互确认
  uv run python scripts/refill_history.py --start 2026-01-15 --end 2026-05-08 --skip-confirm

  # 空 DB 场景（schema 刚 alembic upgrade head，stock_info 空）：加 --fresh，
  # 在 ingest_history 之前先调 refresh_stock_list 拉全 A 股基础信息
  uv run python scripts/refill_history.py --start 2026-01-15 --end 2026-05-08 --fresh --skip-confirm

依赖：
  - TUSHARE_TOKEN / DATABASE_URL 环境变量
  - PostgreSQL 容器运行中
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime

from sqlalchemy import delete, func, select, text

from quantpilot.core.config import settings
from quantpilot.core.database import AsyncSessionLocal
from quantpilot.data.adapters.tushare import TushareAdapter
from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.repository import MarketDataRepository
from quantpilot.data.validators import DataValidator
from quantpilot.models.market import (
    DailyQuote,
    FinancialData,
    IndexComponent,
    IndexHistory,
)
from quantpilot.services.data_service import DataService


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


async def _delete_range(session, start: date, end: date) -> dict[str, int]:
    """删除 [start, end] 范围内 4 张表的行；返回各表删除条数。"""
    counts: dict[str, int] = {}
    for model, label, date_col in [
        (DailyQuote, "daily_quote", DailyQuote.trade_date),
        (FinancialData, "financial_data", FinancialData.publish_date),
        (IndexHistory, "index_history", IndexHistory.trade_date),
        (IndexComponent, "index_component", IndexComponent.trade_date),
    ]:
        result = await session.execute(
            delete(model).where(date_col >= start, date_col <= end)
        )
        counts[label] = result.rowcount or 0
    await session.commit()
    return counts


async def _report_quality(session, start: date, end: date) -> dict[str, object]:
    """重灌后输出关键质量指标：行数 + ROE 非空率 + is_st 真值率。"""
    metrics: dict[str, object] = {}

    # daily_quote 行数 + is_st=TRUE 行数
    dq_total = (
        await session.execute(
            select(func.count())
            .select_from(DailyQuote)
            .where(DailyQuote.trade_date >= start, DailyQuote.trade_date <= end)
        )
    ).scalar() or 0
    dq_st = (
        await session.execute(
            select(func.count())
            .select_from(DailyQuote)
            .where(
                DailyQuote.trade_date >= start,
                DailyQuote.trade_date <= end,
                DailyQuote.is_st.is_(True),
            )
        )
    ).scalar() or 0
    metrics["daily_quote_rows"] = dq_total
    metrics["daily_quote_is_st_true"] = dq_st
    metrics["daily_quote_is_st_ratio"] = (
        f"{(dq_st / dq_total * 100):.2f}%" if dq_total else "n/a"
    )

    # financial_data 行数 + ROE 非空率（同时排除 NUMERIC 'NaN' 残留）
    fd_total = (
        await session.execute(
            select(func.count())
            .select_from(FinancialData)
            .where(
                FinancialData.publish_date >= start, FinancialData.publish_date <= end
            )
        )
    ).scalar() or 0
    fd_roe_ok = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM financial_data "
                "WHERE publish_date BETWEEN :s AND :e "
                "AND roe IS NOT NULL AND roe::text != 'NaN'"
            ),
            {"s": start, "e": end},
        )
    ).scalar() or 0
    metrics["financial_data_rows"] = fd_total
    metrics["financial_data_roe_non_null"] = fd_roe_ok
    metrics["financial_data_roe_fill_ratio"] = (
        f"{(fd_roe_ok / fd_total * 100):.2f}%" if fd_total else "n/a"
    )

    # index_history / index_component 计数
    metrics["index_history_rows"] = (
        await session.execute(
            select(func.count())
            .select_from(IndexHistory)
            .where(
                IndexHistory.trade_date >= start, IndexHistory.trade_date <= end
            )
        )
    ).scalar() or 0
    metrics["index_component_rows"] = (
        await session.execute(
            select(func.count())
            .select_from(IndexComponent)
            .where(
                IndexComponent.trade_date >= start,
                IndexComponent.trade_date <= end,
            )
        )
    ).scalar() or 0

    return metrics


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=_parse_date,
                        help="重灌起点（YYYY-MM-DD，含）")
    parser.add_argument("--end", required=True, type=_parse_date,
                        help="重灌终点（YYYY-MM-DD，含）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只删不重灌（用于先看会清掉多少行）")
    parser.add_argument("--skip-confirm", action="store_true",
                        help="跳过交互确认（非交互环境用）")
    parser.add_argument("--fresh", action="store_true",
                        help="空 DB 场景：在 ingest_history 之前先 refresh_stock_list")
    args = parser.parse_args()

    if args.start > args.end:
        print(f"ERROR: start ({args.start}) > end ({args.end})", file=sys.stderr)
        return 2
    if not settings.tushare_token:
        print("ERROR: TUSHARE_TOKEN 未配置", file=sys.stderr)
        return 2

    print(f"=== Refill plan: {args.start} → {args.end} ===")
    if not args.skip_confirm:
        try:
            ans = input("继续？(yes/no): ").strip().lower()
        except EOFError:
            ans = "no"
        if ans != "yes":
            print("Aborted.")
            return 1

    async with AsyncSessionLocal() as session:
        # 1) 删除范围内现有数据
        print("[1/3] Deleting range data...")
        deleted = await _delete_range(session, args.start, args.end)
        for label, n in deleted.items():
            print(f"      - {label}: {n} rows deleted")

    if args.dry_run:
        print("--dry-run: skip re-ingest.")
        return 0

    # 2) 重灌（per-day AsyncSessionLocal，Bug 5 修复保留）
    adapter = TushareAdapter(token=settings.tushare_token)
    validator = DataValidator()
    # 拉日历需要 start/end；放宽 30 天上下界以覆盖 namechange 5 年回溯下需要的
    # 交易日（虽然回溯日历不参与日历日范围核对，但为安全留余量）
    from datetime import timedelta as _td
    calendar = await TradingCalendar.from_adapter(
        adapter, args.start - _td(days=30), args.end + _td(days=30),
    )

    if args.fresh:
        print("[2a/3] --fresh: refresh_stock_list 先拉全 A 股基础信息...")
        async with AsyncSessionLocal() as session:
            repo = MarketDataRepository(session)
            service = DataService(adapter, validator, repo, calendar)
            stock_summary = await service.refresh_stock_list()
            await session.commit()
        print(f"        - {stock_summary}")

    print("[2/3] Re-ingesting via DataService.ingest_history (per-day session)...")
    async with AsyncSessionLocal() as session:
        repo = MarketDataRepository(session)
        service = DataService(adapter, validator, repo, calendar)
        summary = await service.ingest_history(args.start, args.end)
        # ingest_history 内部对 daily_quote/financial_data 走 per-day session 自己 commit；
        # 但 index_history / index_components_range 用 self._repo（= 本 outer session），
        # 必须在退出 async with 前显式 commit，否则 close 时 rollback 丢数据。
        await session.commit()
    print(f"      - success={summary['success_count']}  fail={summary['fail_count']}")
    if summary["failed_dates"]:
        print(f"      - failed_dates: {summary['failed_dates']}")

    # 3) 质量指标
    print("[3/3] Quality metrics:")
    async with AsyncSessionLocal() as session:
        metrics = await _report_quality(session, args.start, args.end)
    for k, v in metrics.items():
        print(f"      - {k}: {v}")

    # 退出码：成功 0；有失败日期 → 1（CI 友好）
    return 0 if summary["fail_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
