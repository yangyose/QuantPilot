"""修复 daily_quote.is_st 历史断档（2026-06-18 真机发现）。

根因：tushare 日线适配器默认 is_st=False，真实 ST 标记只在 ingest_history 经
namechange 注入；每日管线 ingest_daily 不传 _st_codes → 回填用尽后（约 2026-05-20 起）
每天写入 is_st 全 False → universe ST 过滤失效 → *ST 仙股混入买入信号。

代码根因已在 DataService._build_current_st_codes 修复（每日管线自愈）；本脚本回填
**已经写坏的历史日期**，使历史信号/回测不再被 ST 仙股污染。

做法：拉 namechange（start-5y ~ end）→ _build_st_map 建每日 PIT ST 集合 →
逐交易日 `UPDATE daily_quote SET is_st = (ts_code = ANY(st_codes)) WHERE trade_date=d`
（幂等：ST 置 true、其余 false；可重复执行）。

依赖：TUSHARE_TOKEN + DATABASE_URL（指向待修库）。

用法（backend/ 目录）：
  # 预检（不写库，打印每日 ST 数）：
  uv run python scripts/backfill_is_st.py --start 2026-05-20 --end 2026-06-17 --dry-run
  # 执行：
  uv run python scripts/backfill_is_st.py --start 2026-05-20 --end 2026-06-17 --skip-confirm
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, timedelta

from sqlalchemy import bindparam, text

from quantpilot.core.config import settings
from quantpilot.core.database import AsyncSessionLocal
from quantpilot.data.adapters.tushare import TushareAdapter
from quantpilot.services.data_service import _build_st_map

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_is_st")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="回填 daily_quote.is_st 历史断档")
    p.add_argument("--start", required=True, help="起始交易日 YYYY-MM-DD")
    p.add_argument("--end", required=True, help="结束交易日 YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true", help="只算不写")
    p.add_argument("--skip-confirm", action="store_true", help="跳过交互确认")
    return p.parse_args()


async def _run(args: argparse.Namespace) -> None:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        sys.exit("❌ end 早于 start")

    # 1) 待修交易日 = daily_quote 在 [start, end] 实际存在的 trade_date
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            text("SELECT DISTINCT trade_date FROM daily_quote "
                 "WHERE trade_date BETWEEN :s AND :e ORDER BY trade_date"),
            {"s": start, "e": end},
        )).all()
    trade_dates = [r[0] for r in rows]
    if not trade_dates:
        sys.exit(f"❌ daily_quote 在 {start}~{end} 无数据")
    logger.info("待修交易日 %d 个：%s ~ %s", len(trade_dates), trade_dates[0], trade_dates[-1])

    # 2) 拉 namechange（5y 回溯，与 ingest_history 同源）→ 建每日 PIT ST 集合
    ns_lookback_start = start - timedelta(days=365 * 5)
    if not settings.tushare_token:
        sys.exit("❌ 未配置 TUSHARE_TOKEN，无法拉 namechange")
    adapter = TushareAdapter(settings.tushare_token)
    namechange_df = await adapter.fetch_namechange(ns_lookback_start, end)
    st_map = _build_st_map(namechange_df, trade_dates)
    logger.info(
        "namechange 行数=%d；各日 ST 数样例：%s",
        len(namechange_df),
        {str(d): len(st_map[d]) for d in trade_dates[:3]},
    )

    if args.dry_run:
        for d in trade_dates:
            logger.info("  [dry-run] %s → ST %d 只", d, len(st_map[d]))
        logger.info("dry-run 完成，未写库。")
        return

    if not args.skip_confirm:
        ans = input(f"将 UPDATE daily_quote.is_st 覆盖 {len(trade_dates)} 个交易日，确认？[y/N] ")
        if ans.strip().lower() != "y":
            sys.exit("已取消")

    # 3) 逐日 UPDATE（幂等：ST 置 true、其余 false）。per-day 独立 session + commit。
    total_st = 0
    for d in trade_dates:
        codes = sorted(st_map[d])
        stmt = text(
            "UPDATE daily_quote SET is_st = (ts_code = ANY(:codes)) WHERE trade_date = :d"
        ).bindparams(bindparam("codes"), bindparam("d"))
        async with AsyncSessionLocal() as session:
            await session.execute(stmt, {"codes": codes, "d": d})
            await session.commit()
        total_st += len(codes)
        logger.info("  %s → is_st=true %d 只", d, len(codes))

    logger.info("✅ 回填完成：%d 个交易日，累计标 ST %d（含重复票）。", len(trade_dates), total_st)


def main() -> None:
    asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    main()
