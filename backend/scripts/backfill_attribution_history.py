"""Phase 14 §14-8.1：AttributionService 日级历史回填。

枚举 [--start, --end] 范围内的每个月末交易日，逐月调
`AttributionService.run_monthly(month_end)` 写 `attribution_history` 表
（每月 4 行：trend / momentum / mean_reversion / value）。

依赖：
- candidate_pool 全量已在库（§14-2 5y 回填先行）；本脚本独立 5y × 60 month_end
  × 4 行 = 1200 行远未到 asyncpg 32767 占位符限制
- TUSHARE_TOKEN / DATABASE_URL 环境变量
- PostgreSQL 容器运行中

用法：
  # 5y 全量回填：
  uv run python scripts/backfill_attribution_history.py \\
      --start 2021-01 --end 2026-05 --skip-confirm

  # 预检（不写库，仅打印 month_end 列表 + 已有 attribution_history 月数）：
  uv run python scripts/backfill_attribution_history.py \\
      --start 2021-01 --end 2026-05 --dry-run-plan

  # 跳过已写入月份（默认行为；已有 attribution_history 该月 4 行 → skip）：
  uv run python scripts/backfill_attribution_history.py \\
      --start 2025-01 --end 2026-05 --skip-confirm

  # 强制重写已有月份（upsert 覆盖，不删行）：
  uv run python scripts/backfill_attribution_history.py \\
      --start 2025-01 --end 2026-05 --force --skip-confirm

graceful shutdown：捕 SIGINT/SIGTERM，等当前 month_end commit/rollback 完成
再退出，不留半 commit 状态。
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from quantpilot.core.config import settings
from quantpilot.core.database import AsyncSessionLocal
from quantpilot.data.adapters.tushare import TushareAdapter
from quantpilot.data.attribution_repository import AttributionRepository
from quantpilot.data.calendar import TradingCalendar
from quantpilot.models.business import AttributionHistory
from quantpilot.services.attribution_service import AttributionService


def _parse_year_month(s: str) -> date:
    """解析 YYYY-MM → 该月 1 号 date。"""
    return datetime.strptime(s, "%Y-%m").date()


def _enumerate_month_ends(
    calendar: TradingCalendar, start_ym: date, end_ym: date,
) -> list[date]:
    """枚举 [start_ym 月, end_ym 月] 范围内每个月的最后一个交易日。

    用 TradingCalendar 严格交易日定位每月末——非交易日（月末是周末/节假日）
    自动回退到前一个交易日。

    例如 2026-04（含）→ 取 ≤ 2026-04-30 的最近交易日，可能是 4-30 (Thu) 或 4-28。
    """
    month_ends: list[date] = []
    cur = date(start_ym.year, start_ym.month, 1)
    end_cap = date(end_ym.year, end_ym.month, 1)
    while cur <= end_cap:
        # 该月 1 号 → 加一个月再减一天 = 该月最后一日（日历）
        if cur.month == 12:
            next_first = date(cur.year + 1, 1, 1)
        else:
            next_first = date(cur.year, cur.month + 1, 1)
        month_last_calendar = next_first - timedelta(days=1)

        # 用 calendar 找 ≤ month_last_calendar 的最近交易日
        # （offset_trade_date 在非交易日入参时自动回退到前一个交易日）
        try:
            trade_d = calendar.offset_trade_date(month_last_calendar, 0)
            if trade_d.year == cur.year and trade_d.month == cur.month:
                month_ends.append(trade_d)
        except ValueError:
            # 日历未覆盖该月（极早期日期），跳过
            pass

        cur = next_first
    return month_ends


async def _get_existing_calc_months(
    session, start: date, end: date,
) -> set[date]:
    """查 attribution_history 表已有的 calc_date（month_end 集合）。"""
    stmt = (
        select(AttributionHistory.calc_date)
        .where(
            AttributionHistory.calc_date >= start,
            AttributionHistory.calc_date <= end,
        )
        .distinct()
    )
    result = await session.execute(stmt)
    return {row[0] for row in result.all()}


async def _print_plan(
    session,
    calendar: TradingCalendar,
    month_ends: list[date],
    force: bool,
) -> tuple[list[date], list[date]]:
    """打印计划，返回 (待处理 month_ends, 跳过的 month_ends)。"""
    if not month_ends:
        print("      no month_end found in window")
        return [], []

    existing = await _get_existing_calc_months(
        session, min(month_ends), max(month_ends),
    )
    to_process: list[date] = []
    to_skip: list[date] = []
    for me in month_ends:
        if me in existing and not force:
            to_skip.append(me)
        else:
            to_process.append(me)

    print(f"      total_month_ends: {len(month_ends)}")
    print(f"      already_in_history: {len(existing)}")
    print(f"      to_process: {len(to_process)}")
    print(f"      to_skip (already done): {len(to_skip)}")
    if to_process:
        print(f"      range: {to_process[0]} → {to_process[-1]}")
    return to_process, to_skip


class _GracefulInterrupt:
    """信号 handler：SIGINT/SIGTERM 后置 stop=True，主循环检查后跳出。"""

    def __init__(self) -> None:
        self.stop = False

    def install(self) -> None:
        def _handler(signum, frame):  # noqa: ARG001
            print(f"\n[!] received signal {signum}, finishing current month_end then exit")
            self.stop = True

        # Windows 上 SIGTERM 仅作占位；SIGINT (Ctrl+C) 主用
        try:
            signal.signal(signal.SIGINT, _handler)
        except ValueError:
            # 非主线程不支持 signal —— pytest 集成测试场景跳过
            pass
        if hasattr(signal, "SIGTERM"):
            try:
                signal.signal(signal.SIGTERM, _handler)
            except ValueError:
                pass


async def _run_one_month(
    month_end: date, calendar: TradingCalendar,
) -> tuple[bool, int]:
    """对单个 month_end 跑 run_monthly，独立 session + commit。

    返回 (success, written_count)：成功时 written_count = 4（4 因子）；
    样本不足等 best-effort 返回 (True, 0)；异常 → 整月 rollback + (False, 0)。
    """
    async with AsyncSessionLocal() as session:
        try:
            repo = AttributionRepository()
            service = AttributionService(session, repo, calendar=calendar)
            written = await service.run_monthly(month_end)
            await session.commit()
            return True, len(written)
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
        f"=== Backfill attribution: {args.start.strftime('%Y-%m')} → "
        f"{args.end.strftime('%Y-%m')} | "
        f"mode: {'force-overwrite' if args.force else 'skip-existing'} ==="
    )

    # 拉日历（含前后 30 天 buffer，确保月末回退查找不越界）
    adapter = TushareAdapter(token=settings.tushare_token)
    cal_start = date(args.start.year, args.start.month, 1) - timedelta(days=30)
    cal_end_first = (
        date(args.end.year + 1, 1, 1) if args.end.month == 12
        else date(args.end.year, args.end.month + 1, 1)
    )
    cal_end = cal_end_first + timedelta(days=30)
    calendar = await TradingCalendar.from_adapter(adapter, cal_start, cal_end)

    # 枚举 month_ends（严格交易日）
    month_ends = _enumerate_month_ends(calendar, args.start, args.end)

    # 预检
    print("[0/2] Pre-flight plan:")
    async with AsyncSessionLocal() as session:
        to_process, to_skip = await _print_plan(
            session, calendar, month_ends, args.force,
        )

    if args.dry_run_plan:
        return 0
    if not to_process:
        print("\n[1/2] Nothing to do (all month_ends already in history).")
        return 0

    if not args.skip_confirm:
        ans = input(f"\n[?] proceed with {len(to_process)} month_ends? [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("aborted by user")
            return 1

    # 主循环：逐月 run_monthly + per-month session
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
            if i % 12 == 0 or i == len(to_process):
                print(f"      {i}/{len(to_process)}: month_end={me} written={written}")
        else:
            fail += 1

    # 收尾报告
    print(f"\n[2/2] Done: success={success} fail={fail} total_rows={total_written}")
    async with AsyncSessionLocal() as session:
        cnt = (
            await session.execute(
                select(func.count()).select_from(AttributionHistory).where(
                    AttributionHistory.calc_date >= min(to_process),
                    AttributionHistory.calc_date <= max(to_process),
                )
            )
        ).scalar() or 0
        print(f"      attribution_history rows in range: {cnt}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(_main())
    sys.exit(exit_code)
