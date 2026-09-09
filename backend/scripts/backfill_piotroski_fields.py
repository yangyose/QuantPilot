"""V1.5-C C2：逐报告期回填 Piotroski 所需的 6 个 `fina_indicator` 字段。

## 为什么不能复用 `backfill_total_equity.py`

那个脚本走 `refresh_financials_full` → `fetch_financial_by_stock(start, end)`，
即**日期窗口**调用。而 `fina_indicator` 按日期窗口调用有 **100 行硬上限**
（2026-09-09 实调：5 码跨 5.7 年恰好返回 100 行、每码 18~21 期被截断）。
50 码/批时每股只拿到 2 期 → 只有最新一期有值，**而脚本报 ok=5515 fail=0**。

调用成功、行数被静默截断——§4.3「日期类接口静默返错数据」的又一形态。
⚠️ 设计文档 2026-08-27 的「真调核对」验的是 `period=` 定期调用，
**而回填实际走的是日期窗口调用**：验证做了，验的不是真正走的那条路径。

故本脚本**逐报告期**调用（`period=`），每期完整取回全市场。

## 用法

    DATABASE_URL=...:5434/quantpilot \
      uv run python scripts/backfill_piotroski_fields.py --start 2021-03-31 --end 2026-06-30

    # 先小规模验证能拿到多期（强烈建议）
    ... --limit-periods 2 --limit-codes 200

判断存活看落盘日志的 `period ... ok=` 行，不认通知、不认退出码（§4.11）。
收尾自查逐期非空数，**不信 success_count**。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quantpilot.core.config import settings  # noqa: E402
from quantpilot.core.database import AsyncSessionLocal  # noqa: E402
from quantpilot.data.adapters.tushare import TushareAdapter  # noqa: E402
from quantpilot.data.repository import MarketDataRepository  # noqa: E402
from quantpilot.models.market import FinancialData  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("backfill_piotroski")

_COLS = ("roa", "ocfps", "eps", "current_ratio", "grossprofit_margin", "assets_turn")


def _quarter_ends(start: date, end: date) -> list[date]:
    out: list[date] = []
    y = start.year
    while y <= end.year:
        for m, d in ((3, 31), (6, 30), (9, 30), (12, 31)):
            q = date(y, m, d)
            if start <= q <= end:
                out.append(q)
        y += 1
    return out


async def _coverage(periods: list[date]) -> list[tuple[date, int]]:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(FinancialData.report_period,
                   func.count(FinancialData.roa))
            .where(FinancialData.report_period.in_(periods))
            .group_by(FinancialData.report_period)
            .order_by(FinancialData.report_period)
        )).all()
    return [(r[0], int(r[1])) for r in rows]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--limit-periods", type=int, default=0, help="只跑前 N 期（试跑）")
    ap.add_argument("--limit-codes", type=int, default=0, help="只跑前 N 只（试跑）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    periods = _quarter_ends(start, end)
    if args.limit_periods:
        periods = periods[-args.limit_periods:]

    before = await _coverage(periods)
    logger.info("BEFORE periods=%d", len(periods))
    for rp, n in before:
        logger.info("  %s roa=%d", rp, n)
    if args.dry_run:
        logger.info("dry-run：未写库")
        return 0

    async with AsyncSessionLocal() as s:
        codes = await MarketDataRepository(s).get_active_stock_codes()
    if args.limit_codes:
        codes = codes[: args.limit_codes]
    logger.info("START periods=%d codes=%d", len(periods), len(codes))

    adapter = TushareAdapter(settings.tushare_token)
    t0 = time.perf_counter()
    for rp in periods:
        got = 0
        # per-period 独立 session：坏的一期不毒化其余（§4.3 ingest_history 同款教训）
        async with AsyncSessionLocal() as s:
            repo = MarketDataRepository(s)
            try:
                df = await adapter.fetch_financial_by_stock(
                    codes, rp, rp, period=rp.strftime("%Y%m%d")
                )
                if not df.empty:
                    got = await repo.upsert_financial_data(df)
                await s.commit()
            except Exception:
                logger.exception("period_failed period=%s", rp)
        logger.info(
            "period %s ok=%d elapsed=%.0fs", rp, got, time.perf_counter() - t0
        )

    after = await _coverage(periods)
    logger.info("AFTER elapsed=%.0fs", time.perf_counter() - t0)
    bad = []
    bmap = dict(before)
    for rp, n in after:
        logger.info("  %s roa=%d (+%d)", rp, n, n - bmap.get(rp, 0))
        if n == 0:
            bad.append(rp)
    # 收尾自查：不信 success_count，看库里逐期非空数
    if bad:
        logger.error("以下报告期回填后仍为 0：%s", bad)
        return 1
    return 0


sys.exit(asyncio.run(main()))
