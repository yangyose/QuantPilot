"""V1.5-C C2：回填 `total_share`（Piotroski 第 9 项 `no_dilution` 的唯一依赖）。

## 为什么单独一个脚本

`total_share` 来自 `daily_basic`（按**交易日**取全市场），而其余 6 个 C2 字段来自
`fina_indicator`（按**报告期**取）。两条路的取数维度不同，硬塞进一个脚本会让
「按期回填」和「按日回填」互相迁就。

选 `daily_basic` 而非 `balancesheet`：后者不支持逗号多码，全市场两期约需 11000 次
调用；前者每个交易日一次取回全市场，实测非空率 100%（设计 §4.1）。

## 取哪一天

每个报告期取**该期最后一个交易日**的快照，写成 `publish_date = 该交易日`、
`report_period = 该期`。PIT 正确：那一天的股本数就是当日已知量。
`get_financials_yoy_pairs` 按 `(ts_code, report_period)` 取 `max(total_share)`
且限定 `publish_date <= as_of`，故同比配对拿到的是各期末的股本，口径一致。

【降级说明】口径是「交易日时点股本」而非「报告期时点股本」，两者在期末同一天，
差异可忽略；恢复条件 = 改接 `balancesheet.total_share` 并承担逐单码回填成本。

## 用法

    DATABASE_URL=...:5434/quantpilot \
      uv run python scripts/backfill_total_share.py --start 2021-03-31 --end 2026-06-30

收尾自查逐期非空数，**不信 success_count**（§4.11）。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quantpilot.core.config import settings  # noqa: E402
from quantpilot.core.database import AsyncSessionLocal  # noqa: E402
from quantpilot.data.adapters.tushare import TushareAdapter  # noqa: E402
from quantpilot.data.calendar import TradingCalendar  # noqa: E402
from quantpilot.data.repository import MarketDataRepository  # noqa: E402
from quantpilot.models.market import FinancialData  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("backfill_total_share")


def _quarter_ends(start: date, end: date) -> list[date]:
    out: list[date] = []
    for y in range(start.year, end.year + 1):
        for m, d in ((3, 31), (6, 30), (9, 30), (12, 31)):
            q = date(y, m, d)
            if start <= q <= end:
                out.append(q)
    return out


async def _coverage(periods: list[date]):
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(FinancialData.report_period, func.count(FinancialData.total_share))
            .where(FinancialData.report_period.in_(periods))
            .group_by(FinancialData.report_period)
            .order_by(FinancialData.report_period)
        )).all()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    periods = _quarter_ends(start, end)

    logger.info("BEFORE")
    for rp, n in await _coverage(periods):
        logger.info("  %s total_share=%d", rp, n)
    if args.dry_run:
        logger.info("dry-run：未写库")
        return 0

    adapter = TushareAdapter(settings.tushare_token)
    async with AsyncSessionLocal() as s:
        cal = await TradingCalendar.from_repo(
            MarketDataRepository(s), start - __import__("datetime").timedelta(days=30), end
        )

    for rp in periods:
        # 该报告期最后一个交易日（期末当日若非交易日则向前回退）
        try:
            td = rp if cal.is_trade_date(rp) else cal.get_prev_trade_date(rp, 1)
        except Exception:
            logger.warning("period %s 无可用交易日，跳过", rp)
            continue
        got = 0
        async with AsyncSessionLocal() as s:
            repo = MarketDataRepository(s)
            try:
                df = await adapter._call(
                    adapter._pro.daily_basic,
                    trade_date=td.strftime("%Y%m%d"),
                    fields="ts_code,total_share",
                )
                if df is not None and not df.empty:
                    out = pd.DataFrame({
                        "ts_code": df["ts_code"],
                        "report_period": rp,
                        "publish_date": td,
                        # daily_basic 单位：万股 → 股（与适配器同一换算）
                        "total_share": pd.to_numeric(
                            df["total_share"], errors="coerce"
                        ) * 10_000,
                    })
                    got = await repo.upsert_financial_data(out)
                await s.commit()
            except Exception:
                logger.exception("period_failed period=%s td=%s", rp, td)
        logger.info("period %s (td=%s) ok=%d", rp, td, got)

    logger.info("AFTER")
    bad = []
    for rp, n in await _coverage(periods):
        logger.info("  %s total_share=%d", rp, n)
        if n == 0:
            bad.append(rp)
    if bad:
        logger.error("以下期回填后仍为 0：%s", bad)
        return 1
    return 0


sys.exit(asyncio.run(main()))
