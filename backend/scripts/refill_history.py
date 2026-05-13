"""历史数据回填（财务 + 行情 + 指数 + 成分股 + is_st PIT）。

**两种模式**（2026-05-13 拆分；原默认 DELETE 行为收编到 --force-clean）：

1. **默认 / 扩存量模式**（无 --force-clean）：
   - 不删任何已有数据
   - 调 DataService.ingest_history 走双表交集断点续传
   - get_fully_ingested_dates 自动跳过已完整入库的日子
   - 用途：首次回填、按需扩大历史窗口（如 90 天 → 2 年 → 5 年）

2. **--force-clean / 修脏模式**（RM-12 真机验收原场景 §2.9）：
   - 先 DELETE financial_data + daily_quote + index_history + index_component 范围内行
   - 再走 ingest_history 全量重灌
   - 用途：上一轮上游 bug 把数据写脏，断点续传会跳过脏行不刷新，必须强制重做

用法：
  # 扩存量（推荐 / 默认）：
  uv run python scripts/refill_history.py --start 2021-05-13 --end 2026-05-12 --skip-confirm

  # 预检计划（不删不拉，仅打印 trade_dates 总数 / 已入库 / 待补数量）：
  uv run python scripts/refill_history.py --start 2021-05-13 --end 2026-05-12 --dry-run-plan

  # 修脏：
  uv run python scripts/refill_history.py --start 2026-01-15 --end 2026-05-08 \
      --force-clean --skip-confirm

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
from datetime import date, datetime, timedelta

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


async def _print_plan(
    session, start: date, end: date, calendar: TradingCalendar
) -> dict[str, int]:
    """打印预检：trade_dates 总数 / 已完整入库 / 待补缺失。"""
    from quantpilot.data.repository import MarketDataRepository
    repo = MarketDataRepository(session)
    trade_dates = calendar.get_trade_dates(start, end)
    already_done = await repo.get_fully_ingested_dates(start, end)
    total = len(trade_dates)
    done = len(already_done)
    remaining = total - done
    print(f"      total_trade_dates: {total}")
    print(f"      already_fully_ingested: {done}"
          + (f" ({min(already_done)} → {max(already_done)})" if already_done else ""))
    print(f"      remaining_to_ingest: {remaining}")
    return {"total": total, "done": done, "remaining": remaining}


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=_parse_date,
                        help="范围起点（YYYY-MM-DD，含）")
    parser.add_argument("--end", required=True, type=_parse_date,
                        help="范围终点（YYYY-MM-DD，含）")
    parser.add_argument("--force-clean", action="store_true",
                        help="修脏模式：先 DELETE 范围内 4 表数据再重灌（默认走断点续传扩存量）")
    parser.add_argument("--dry-run-plan", action="store_true",
                        help="预检模式：仅打印 trade_dates / 已完整入库 / 待补数量，不删不拉")
    parser.add_argument("--dry-run", action="store_true",
                        help="（仅 --force-clean 时有效）只删不重灌，用于先看会清掉多少行")
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
    if args.dry_run and not args.force_clean:
        print("ERROR: --dry-run 仅 --force-clean 模式有效；扩存量模式请用 --dry-run-plan",
              file=sys.stderr)
        return 2

    mode = "force-clean (DELETE + 全量重灌)" if args.force_clean else "incremental (断点续传)"
    print(f"=== Refill plan: {args.start} → {args.end} | mode: {mode} ===")

    # 拉日历（namechange 5 年回溯下放宽 30 天上下界）
    adapter = TushareAdapter(token=settings.tushare_token)
    validator = DataValidator()
    calendar = await TradingCalendar.from_adapter(
        adapter, args.start - timedelta(days=30), args.end + timedelta(days=30),
    )

    # 预检（无论哪种模式都先打印计划）
    print("[0/3] Pre-flight plan:")
    async with AsyncSessionLocal() as session:
        plan = await _print_plan(session, args.start, args.end, calendar)
    if args.dry_run_plan:
        return 0
    if not args.force_clean and plan["remaining"] == 0:
        print("Nothing to do (incremental mode, all dates already ingested).")
        return 0

    if not args.skip_confirm:
        try:
            ans = input("继续？(yes/no): ").strip().lower()
        except EOFError:
            ans = "no"
        if ans != "yes":
            print("Aborted.")
            return 1

    # 1) DELETE（仅 --force-clean 时）
    if args.force_clean:
        async with AsyncSessionLocal() as session:
            print("[1/3] Deleting range data (--force-clean)...")
            deleted = await _delete_range(session, args.start, args.end)
            for label, n in deleted.items():
                print(f"      - {label}: {n} rows deleted")
        if args.dry_run:
            print("--dry-run: skip re-ingest.")
            return 0
    else:
        print("[1/3] Skipping DELETE (incremental mode).")

    # 2) refresh_stock_list（仅 --fresh 时）
    if args.fresh:
        print("[2a/3] --fresh: refresh_stock_list 先拉全 A 股基础信息...")
        async with AsyncSessionLocal() as session:
            repo = MarketDataRepository(session)
            service = DataService(adapter, validator, repo, calendar)
            stock_summary = await service.refresh_stock_list()
            await session.commit()
        print(f"        - {stock_summary}")

    # 3) ingest_history（per-day AsyncSessionLocal，Bug 5 修复；自带断点续传）
    print("[2/3] Re-ingesting via DataService.ingest_history (per-day session)...")
    async with AsyncSessionLocal() as session:
        repo = MarketDataRepository(session)
        service = DataService(adapter, validator, repo, calendar)
        summary = await service.ingest_history(args.start, args.end)
        # index_history / index_components_range 走 self._repo（= 本 outer session），
        # 必须在退出 async with 前显式 commit，否则 close 时 rollback 丢数据。
        await session.commit()
    print(f"      - success={summary['success_count']}  fail={summary['fail_count']}")
    if summary["failed_dates"]:
        print(f"      - failed_dates: {summary['failed_dates']}")

    # 4) 质量指标
    print("[3/3] Quality metrics:")
    async with AsyncSessionLocal() as session:
        metrics = await _report_quality(session, args.start, args.end)
    for k, v in metrics.items():
        print(f"      - {k}: {v}")

    return 0 if summary["fail_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
