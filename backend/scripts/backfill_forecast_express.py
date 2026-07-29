"""A5c（SDD-EXT-03）：回填 financial_forecast（业绩预告 forecast / 业绩快报 express）PIT 数据。

用于 A5b 前瞻 ROE 覆盖：真空期（快报/预告已发、正式财报未发）用 est_net_profit 派生
前瞻 ROE 修正估值。本脚本回填历史预告/快报，使生产评分与回测在真空期消费到前瞻数据。

数据源与 quirk（A5c 2026-07-29 本地实证，见 adapter.fetch_forecast_express docstring）：
- forecast/express 接口**不支持逗号多码 ts_code**（batch 静默返回空）→ adapter 已改逐股单码。
- forecast net_profit_min/max 万元×10000→元；express n_income 元；forecast p_change %÷100；
  express yoy 由「去年同期净利润」派生增长率。以上单位均经真实数据实证。
- 逐股单码 → 全市场 5y ≈ 5500 股 ×（forecast+express）≈ 1.1w 次调用；分块 + 块间限流 +
  限频自动退避重试；每块 upsert 落库（可中断续跑，ON CONFLICT 幂等）。

⚠️ 生产写：须对**生产库**执行（DATABASE_URL 指向生产），执行前 C-1 确认 + pg_dump。
本地小样本先验证：`--limit 50 --end 2024-06-30` 对 5433 测试库跑通再上生产。

用法（backend/ 目录）：
  # 本地小样本 dry-run（拉数不写库，打印计数）：
  uv run python scripts/backfill_forecast_express.py --start 2024-01-01 --end 2024-06-30 \
      --limit 50 --dry-run
  # 本地小样本写 5433：
  DATABASE_URL=postgresql+asyncpg://quantpilot:quantpilot@127.0.0.1:5433/quantpilot \
      uv run python scripts/backfill_forecast_express.py --start 2024-01-01 --end 2024-06-30 \
      --limit 50 --skip-confirm
  # 生产 5y（须 C-1 + pg_dump）：
  uv run python scripts/backfill_forecast_express.py --start 2021-01-01 --end 2026-07-29
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date

from sqlalchemy import select

from quantpilot.core.config import settings
from quantpilot.core.database import AsyncSessionLocal
from quantpilot.data.adapters.tushare import TushareAdapter, _is_rate_limit_error
from quantpilot.data.repository import MarketDataRepository
from quantpilot.models.market import FinancialForecast, StockInfo

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_forecast_express")

_RATE_LIMIT_RETRIES = 5
_RATE_LIMIT_SLEEP_S = 60.0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="回填 financial_forecast（业绩预告/快报 PIT）")
    p.add_argument("--start", required=True, help="ann_date 起始 YYYY-MM-DD")
    p.add_argument("--end", required=True, help="ann_date 结束 YYYY-MM-DD")
    p.add_argument("--chunk-size", type=int, default=300, help="每块股票数（默认 300）")
    p.add_argument("--chunk-sleep", type=float, default=1.0, help="块间 sleep 秒（默认 1.0）")
    p.add_argument("--limit", type=int, default=0, help="仅取前 N 只（0=全市场；用于小样本）")
    p.add_argument("--dry-run", action="store_true", help="拉数不写库，打印计数")
    p.add_argument("--skip-confirm", action="store_true", help="跳过交互确认")
    return p.parse_args()


async def _load_universe(limit: int) -> list[str]:
    """全市场 ts_code（含退市，PIT 5y 完整性）——取自本地 stock_info。"""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(StockInfo.ts_code).order_by(StockInfo.ts_code)
        )).scalars().all()
    codes = list(rows)
    if limit > 0:
        codes = codes[:limit]
    return codes


async def _fetch_chunk_with_retry(
    adapter: TushareAdapter, chunk: list[str], start: date, end: date,
):
    """限频自动退避重试；其他异常直接抛（不静默吞——C-4）。"""
    for attempt in range(1, _RATE_LIMIT_RETRIES + 1):
        try:
            return await adapter.fetch_forecast_express(chunk, start, end)
        except Exception as exc:  # noqa: BLE001 — 仅对限频退避，其余重抛
            if _is_rate_limit_error(exc) and attempt < _RATE_LIMIT_RETRIES:
                logger.warning(
                    "限频命中（第 %d/%d 次），sleep %.0fs 后重试：%s",
                    attempt, _RATE_LIMIT_RETRIES, _RATE_LIMIT_SLEEP_S, exc,
                )
                await asyncio.sleep(_RATE_LIMIT_SLEEP_S)
                continue
            raise


async def _current_count() -> int:
    from sqlalchemy import func
    async with AsyncSessionLocal() as session:
        return (await session.execute(
            select(func.count()).select_from(FinancialForecast)
        )).scalar_one()


async def _run(args: argparse.Namespace) -> None:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        sys.exit("❌ end 早于 start")
    if not settings.tushare_token:
        sys.exit("❌ 未配置 TUSHARE_TOKEN")

    universe = await _load_universe(args.limit)
    if not universe:
        sys.exit("❌ stock_info 为空，无法确定回填股票池")
    n_chunks = (len(universe) + args.chunk_size - 1) // args.chunk_size
    logger.info(
        "回填窗口 %s ~ %s；股票池 %d 只（%d 块 ×%d）；dry_run=%s",
        start, end, len(universe), n_chunks, args.chunk_size, args.dry_run,
    )

    if not args.dry_run and not args.skip_confirm:
        db_url = str(settings.database_url)
        ans = input(
            f"将 upsert financial_forecast（DB={db_url.rsplit('@', 1)[-1]}），确认？[y/N] "
        )
        if ans.strip().lower() != "y":
            sys.exit("已取消")

    before = 0 if args.dry_run else await _current_count()
    adapter = TushareAdapter(settings.tushare_token)
    total_fetched = 0
    total_written = 0

    for ci in range(n_chunks):
        chunk = universe[ci * args.chunk_size : (ci + 1) * args.chunk_size]
        df = await _fetch_chunk_with_retry(adapter, chunk, start, end)
        n = 0 if df is None else len(df)
        total_fetched += n
        written = 0
        if n and not args.dry_run:
            # per-chunk 独立 session + commit（可中断续跑；ON CONFLICT 幂等）
            async with AsyncSessionLocal() as session:
                repo = MarketDataRepository(session)
                written = await repo.upsert_financial_forecast(df)
                await session.commit()
            total_written += written
        logger.info(
            "  [块 %d/%d] 股票 %d → 拉取 %d 行%s",
            ci + 1, n_chunks, len(chunk), n,
            "" if args.dry_run else f"，upsert {written} 行",
        )
        if ci + 1 < n_chunks:
            await asyncio.sleep(args.chunk_sleep)

    if args.dry_run:
        logger.info("✅ dry-run 完成：累计拉取 %d 行（未写库）。", total_fetched)
        return
    after = await _current_count()
    logger.info(
        "✅ 回填完成：拉取 %d 行 / upsert %d 行；financial_forecast 行数 %d → %d（净增 %d）。",
        total_fetched, total_written, before, after, after - before,
    )


def main() -> None:
    asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    main()
