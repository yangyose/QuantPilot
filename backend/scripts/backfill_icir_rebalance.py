"""Phase 14 §14-2.3：月末批 ICIR 历史回算脚本。

枚举 [--start, --end] 范围内每个月末交易日，逐月调
`FactorMonitorService.apply_monthly_rebalance(month_end)` 写
`factor_ic_window_state`（aggregate 行）+ `strategy_weights_history`
（5 年 60 月 → ~720 行 weights_source='icir'）。

依赖：
- candidate_pool 5y 全量已在库（`scripts/backfill_candidate_pool.py` 先行完成）
- TUSHARE_TOKEN / DATABASE_URL 环境变量
- PostgreSQL 容器运行中

用法：
  # 5y 全量回算：
  uv run python scripts/backfill_icir_rebalance.py \\
      --start 2021-01 --end 2026-05 --skip-confirm

  # 预检（仅打印 month_end 列表 + 已存在月数，不写库）：
  uv run python scripts/backfill_icir_rebalance.py \\
      --start 2021-01 --end 2026-05 --dry-run-plan

  # 强制重算所有月份（upsert 覆盖，不删行）：
  uv run python scripts/backfill_icir_rebalance.py \\
      --start 2024-01 --end 2026-05 --force --skip-confirm

graceful shutdown：捕 SIGINT/SIGTERM，等当前 month_end commit/rollback
完成再退出，不留半 commit 状态。

注：本脚本与 §14-2.1 candidate_pool 回填共享 `backfill_candidate_pool` 模块的
`_GracefulInterrupt` / `_compute_plan` 纯函数（DRY），但保持各自的脚本入口
便于独立调度（candidate_pool 50-80h 长跑 vs ICIR 5-20min 快跑）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from quantpilot.core.config import settings
from quantpilot.core.database import AsyncSessionLocal
from quantpilot.data.adapters.tushare import TushareAdapter
from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.factor_ic_repository import FactorICRepository
from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.models.business import StrategyWeightsHistory
from quantpilot.services.factor_monitor_service import FactorMonitorService

# 复用 candidate_pool 回填脚本的 graceful + plan 纯函数（DRY）
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from backfill_candidate_pool import (  # noqa: E402, I001
    _GracefulInterrupt,
    _compute_plan,
)


def _parse_year_month(s: str) -> date:
    return datetime.strptime(s, "%Y-%m").date()


def _enumerate_month_ends(
    calendar: TradingCalendar, start_ym: date, end_ym: date,
) -> list[date]:
    """枚举 [start_ym 月, end_ym 月] 范围内每个月的最后一个交易日。

    与 `backfill_attribution_history._enumerate_month_ends` 同款逻辑——非交易日
    （月末是周末/节假日）自动回退到前一个交易日。
    """
    month_ends: list[date] = []
    cur = date(start_ym.year, start_ym.month, 1)
    end_cap = date(end_ym.year, end_ym.month, 1)
    while cur <= end_cap:
        if cur.month == 12:
            next_first = date(cur.year + 1, 1, 1)
        else:
            next_first = date(cur.year, cur.month + 1, 1)
        month_last_calendar = next_first - timedelta(days=1)
        try:
            trade_d = calendar.offset_trade_date(month_last_calendar, 0)
            if trade_d.year == cur.year and trade_d.month == cur.month:
                month_ends.append(trade_d)
        except ValueError:
            pass
        cur = next_first
    return month_ends


async def _get_existing_rebalance_months(
    session, start: date, end: date,
) -> set[date]:
    """查 strategy_weights_history 表已有的 effective_date（month_end 集合）。"""
    stmt = (
        select(StrategyWeightsHistory.trade_date)
        .where(
            StrategyWeightsHistory.trade_date >= start,
            StrategyWeightsHistory.trade_date <= end,
        )
        .distinct()
    )
    result = await session.execute(stmt)
    return {row[0] for row in result.all()}


async def _print_plan(
    session,
    month_ends: list[date],
    force: bool,
) -> tuple[list[date], list[date]]:
    """打印计划：返回 (to_process, to_skip)。"""
    if not month_ends:
        print("      no month_end found in window")
        return [], []

    existing = await _get_existing_rebalance_months(
        session, min(month_ends), max(month_ends),
    )
    to_process, to_skip = _compute_plan(month_ends, existing, force)

    print(f"      total_month_ends: {len(month_ends)}")
    print(f"      already_in_history: {len(existing)}")
    print(f"      to_process: {len(to_process)}")
    print(f"      to_skip (already done): {len(to_skip)}")
    if to_process:
        print(f"      range: {to_process[0]} → {to_process[-1]}")
    return to_process, to_skip


async def _run_one_month(
    month_end: date, calendar: TradingCalendar,
) -> tuple[bool, int]:
    """单 month_end 独立 session + commit；异常 → 整月 rollback + (False, 0)。"""
    async with AsyncSessionLocal() as session:
        try:
            service = FactorMonitorService(
                session, FactorMonitorEngine(), FactorICRepository(),
                calendar=calendar,
            )
            result = await service.apply_monthly_rebalance(session, month_end)
            await session.commit()
            written = sum(len(rows) for rows in result.values())
            return True, written
        except Exception as exc:
            await session.rollback()
            print(
                f"      ERROR month_end={month_end} → {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return False, 0


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=_parse_year_month,
                        help="起点月（YYYY-MM，含）")
    parser.add_argument("--end", required=True, type=_parse_year_month,
                        help="终点月（YYYY-MM，含）")
    parser.add_argument("--dry-run-plan", action="store_true",
                        help="预检：仅打印 month_end 列表 + 已入库月数")
    parser.add_argument("--force", action="store_true",
                        help="强制重写已有 month_end（upsert 覆盖，不删行）")
    parser.add_argument("--skip-confirm", action="store_true",
                        help="跳过交互确认")
    args = parser.parse_args()

    if args.start > args.end:
        print(f"ERROR: start ({args.start}) > end ({args.end})", file=sys.stderr)
        return 2
    if not settings.tushare_token:
        print("ERROR: TUSHARE_TOKEN 未配置", file=sys.stderr)
        return 2

    print(
        f"=== Backfill ICIR rebalance: {args.start.strftime('%Y-%m')} → "
        f"{args.end.strftime('%Y-%m')} | "
        f"mode: {'force-overwrite' if args.force else 'skip-existing'} ==="
    )

    # 拉日历（前后 30 天 buffer）
    adapter = TushareAdapter(token=settings.tushare_token)
    cal_start = date(args.start.year, args.start.month, 1) - timedelta(days=30)
    cal_end_first = (
        date(args.end.year + 1, 1, 1) if args.end.month == 12
        else date(args.end.year, args.end.month + 1, 1)
    )
    cal_end = cal_end_first + timedelta(days=30)
    calendar = await TradingCalendar.from_adapter(adapter, cal_start, cal_end)

    month_ends = _enumerate_month_ends(calendar, args.start, args.end)

    # 预检
    print("[0/2] Pre-flight plan:")
    async with AsyncSessionLocal() as session:
        to_process, to_skip = await _print_plan(session, month_ends, args.force)

    if args.dry_run_plan:
        return 0
    if not to_process:
        print("\n[1/2] Nothing to do (all month_ends already in history).")
        return 0

    if not args.skip_confirm:
        try:
            ans = input(f"\n[?] proceed with {len(to_process)} month_ends? [y/N] ")
        except EOFError:
            ans = "n"
        if ans.strip().lower() not in ("y", "yes"):
            print("aborted by user")
            return 1

    # 主循环
    interrupt = _GracefulInterrupt()
    interrupt.install()
    print(f"\n[1/2] Backfilling {len(to_process)} month_ends...")
    success = 0
    fail = 0
    total_written = 0
    for i, me in enumerate(to_process, 1):
        if interrupt.stop:
            print(f"      interrupted at {i}/{len(to_process)} (month_end={me})")
            break
        ok, written = await _run_one_month(me, calendar)
        if ok:
            success += 1
            total_written += written
            print(f"      {i}/{len(to_process)}: month_end={me} written={written}")
        else:
            fail += 1

    print(f"\n[2/2] Done: success={success} fail={fail} total_rows={total_written}")
    async with AsyncSessionLocal() as session:
        cnt = (
            await session.execute(
                select(func.count()).select_from(StrategyWeightsHistory).where(
                    StrategyWeightsHistory.trade_date >= min(to_process),
                    StrategyWeightsHistory.trade_date <= max(to_process),
                )
            )
        ).scalar() or 0
        print(f"      strategy_weights_history rows in range: {cnt}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
