"""数据完整性审计：以 trade_calendar 为权威基准，对各日频表做交易日差集。

此前缺权威日历参照，缺口只能靠「假日交叉验证」启发式判断。本脚本以入库的
trade_calendar（is_open=True）为准，逐表报告缺失交易日。

检查表（按 trade_date 入库的日频表）：
- daily_quote
- candidate_pool
- index_history（缺口为整日粒度：某日任一指数有数据即视为 present，单指数尾部缺需另行核对）

退出码：无缺口=0，有缺口=1，参数/环境错误=2（供 cron / CI 消费）。

用法：
  # 默认审计 daily_quote 实际数据区间（自动夹在日历覆盖内，不含未来 / 早于回填起点）：
  uv run python scripts/audit_data_integrity.py

  # 指定范围：
  uv run python scripts/audit_data_integrity.py --start 2021-05-13 --end 2026-05-29

依赖：DATABASE_URL；trade_calendar 已落库（首次启动 app 会自愈拉取，或等月度
刷新 job；也可指定 --start/--end 但日历为空时本脚本会提示先灌日历）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime

from sqlalchemy import func, select

from quantpilot.core.database import AsyncSessionLocal
from quantpilot.data.calendar import missing_trading_days, resolve_audit_range
from quantpilot.data.repository import MarketDataRepository
from quantpilot.models.business import CandidatePool
from quantpilot.models.market import DailyQuote, IndexHistory

_TABLES = {
    "daily_quote": DailyQuote,
    "candidate_pool": CandidatePool,
    "index_history": IndexHistory,
}


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


async def _distinct_trade_dates(session, model, start: date, end: date) -> set[date]:
    result = await session.execute(
        select(model.trade_date)
        .where(model.trade_date >= start, model.trade_date <= end)
        .distinct()
    )
    return set(result.scalars().all())


async def _data_span(session, model) -> tuple[date | None, date | None]:
    """参照表实际数据区间 (min, max) trade_date；空表返回 (None, None)。"""
    result = await session.execute(
        select(func.min(model.trade_date), func.max(model.trade_date))
    )
    row = result.one()
    return row[0], row[1]


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=_parse_date, help="范围起点 YYYY-MM-DD（默认日历最早）")
    parser.add_argument("--end", type=_parse_date, help="范围终点 YYYY-MM-DD（默认日历最晚）")
    parser.add_argument("--exchange", default="SSE", help="交易所（默认 SSE）")
    args = parser.parse_args()

    async with AsyncSessionLocal() as session:
        repo = MarketDataRepository(session)
        coverage = await repo.get_trade_calendar_coverage(exchange=args.exchange)
        if coverage is None:
            print(
                "ERROR: trade_calendar 为空。先启动一次 app（自愈拉取）或运行日历刷新后再审计。",
                file=sys.stderr,
            )
            return 2
        # 默认范围 = daily_quote 实际数据区间（夹在日历 coverage 内），避免拿日历
        # 未来前瞻日 / 早于回填起点的历法日做差集而误报假缺口（评审 CAL-C-01）。
        data_min, data_max = await _data_span(session, DailyQuote)
        if data_min is None:
            print(
                "ERROR: daily_quote 为空，无可审计的数据范围；先回填行情再审计。",
                file=sys.stderr,
            )
            return 2
        start, end = resolve_audit_range(args.start, args.end, coverage, data_min, data_max)
        if start > end:
            print(f"ERROR: start ({start}) > end ({end})", file=sys.stderr)
            return 2

        open_days = await repo.get_trade_calendar_dates(
            start, end, only_open=True, exchange=args.exchange
        )
        print(f"=== 数据完整性审计 {start} → {end}（{args.exchange}）===")
        print(f"基准交易日（trade_calendar is_open=True）：{len(open_days)} 天")

        total_gaps = 0
        for name, model in _TABLES.items():
            present = await _distinct_trade_dates(session, model, start, end)
            missing = missing_trading_days(open_days, present)
            status = "OK" if not missing else f"缺 {len(missing)} 天"
            print(f"\n[{name}] present={len(present)}  {status}")
            if missing:
                total_gaps += len(missing)
                # 缺口较多时只打印前后各若干，避免刷屏
                if len(missing) <= 30:
                    for d in missing:
                        print(f"    - {d} ({d:%a})")
                else:
                    for d in missing[:15]:
                        print(f"    - {d} ({d:%a})")
                    print(f"    ... 省略 {len(missing) - 30} 天 ...")
                    for d in missing[-15:]:
                        print(f"    - {d} ({d:%a})")

    print("\n" + ("=== 审计通过：无缺口 ===" if total_gaps == 0
                  else f"=== 发现 {total_gaps} 处表-日缺口（需补数据）==="))
    return 0 if total_gaps == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
