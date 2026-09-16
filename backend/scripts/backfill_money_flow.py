"""V1.5-C C4：回填 money_flow（个股资金流向，Tushare `moneyflow`），按交易日逐日、可续跑。

为什么不走 `ingest_history`：它按 daily_quote ∩ financial_data 断点续传，历史日早已"完成"
会被整日跳过，资金流永远补不上。本脚本自己按 `money_flow` 表已有日期续跑。

调用形态（2026-09-16 真调）：`moneyflow(trade_date=...)` 全市场单日 5548 行、约 1s，
未触发接口 6000 行上限 → 一日一调，2y ≈ 488 次。逐日独立 session（要么整日 commit
要么整日 rollback），日间 sleep 防限频，限频 / 瞬时网络错误退避重试。

设计 §6.4 的纪律：**先 100 日样本实测行宽（`pg_total_relation_size`）校正预算，再决定
2y 窗口**；本脚本跑完自动打印表大小与行数供校正，禁止按估算直接开跑 2y。

⚠️ 对**生产库**执行前须 C-1 单独确认 + `pg_dump -t money_flow`（新表，备份很小）。
本地先在 5434（算力中心全量副本）跑通。

用法（backend/ 目录）：
  # 100 日样本（本地 5434）：
  DATABASE_URL=postgresql+asyncpg://...@127.0.0.1:5434/quantpilot \
      uv run python scripts/backfill_money_flow.py --start 2026-04-01 --end 2026-09-15
  # 只看计划不拉数：
  uv run python scripts/backfill_money_flow.py --start 2024-09-16 --end 2026-09-15 --dry-run-plan
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import date

from sqlalchemy import text

from quantpilot.core.config import settings
from quantpilot.core.database import AsyncSessionLocal
from quantpilot.data.adapters.tushare import TushareAdapter, _is_rate_limit_error
from quantpilot.data.repository import MarketDataRepository

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_money_flow")

_RATE_LIMIT_RETRIES = 5
_RATE_LIMIT_SLEEP_S = 60.0
_TRANSIENT_SLEEP_S = 10.0
_TRANSIENT_MARKERS = (
    "timed out", "timeout", "connectionpool", "connection aborted",
    "connection reset", "connection refused", "max retries", "temporarily unavailable",
)


def _is_transient(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="回填 money_flow（个股资金流向）")
    p.add_argument("--start", required=True, help="起始交易日 YYYY-MM-DD")
    p.add_argument("--end", required=True, help="结束交易日 YYYY-MM-DD")
    p.add_argument("--day-sleep", type=float, default=0.3, help="日间 sleep 秒（默认 0.3）")
    p.add_argument("--dry-run-plan", action="store_true", help="只打印待回填日期数，不拉数")
    p.add_argument("--skip-confirm", action="store_true", help="跳过交互确认")
    return p.parse_args()


async def _trade_dates(start: date, end: date) -> list[date]:
    """待回填日 = daily_quote 实际存在的交易日（权威日历同源）− money_flow 已有日。"""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            text("SELECT DISTINCT trade_date FROM daily_quote "
                 "WHERE trade_date BETWEEN :s AND :e ORDER BY trade_date"),
            {"s": start, "e": end},
        )).all()
        done = await MarketDataRepository(session).get_money_flow_dates(start, end)
    all_dates = [r[0] for r in rows]
    todo = [d for d in all_dates if d not in done]
    logger.info("窗口内交易日 %d，已有 %d，待回填 %d", len(all_dates), len(done), len(todo))
    return todo


async def _fetch_with_retry(adapter: TushareAdapter, td: date):
    for attempt in range(1, _RATE_LIMIT_RETRIES + 1):
        try:
            return await adapter.fetch_money_flow(td)
        except Exception as exc:
            if _is_rate_limit_error(exc):
                logger.warning("限频 %s（%d/%d），sleep %.0fs", td, attempt,
                               _RATE_LIMIT_RETRIES, _RATE_LIMIT_SLEEP_S)
                await asyncio.sleep(_RATE_LIMIT_SLEEP_S)
                continue
            if _is_transient(exc):
                logger.warning("瞬时网络错误 %s（%d/%d）: %s", td, attempt,
                               _RATE_LIMIT_RETRIES, exc)
                await asyncio.sleep(_TRANSIENT_SLEEP_S)
                continue
            raise
    raise RuntimeError(f"{td}: 重试 {_RATE_LIMIT_RETRIES} 次仍失败")


async def _report_table_size() -> None:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(text(
            "SELECT count(*), min(trade_date), max(trade_date), "
            "pg_total_relation_size('money_flow') FROM money_flow"
        ))).one()
    n, lo, hi, size = row
    per_row = (size / n) if n else 0
    logger.info(
        "money_flow 现状：%d 行（%s ~ %s），表+索引 %.1f MB，≈ %.0f B/行 "
        "→ 2y(≈488 日 × 5500 股) 外推 ≈ %.2f GB",
        n, lo, hi, size / 1e6, per_row, per_row * 488 * 5500 / 1e9,
    )


async def _run(args: argparse.Namespace) -> None:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        sys.exit("❌ end 早于 start")
    logger.info("DATABASE_URL 主机端口：%s", settings.database_url.split("@")[-1])

    todo = await _trade_dates(start, end)
    if args.dry_run_plan or not todo:
        await _report_table_size()
        return
    if not args.skip_confirm:
        ans = input(f"将向上述库写入 {len(todo)} 个交易日的 money_flow，继续？[y/N] ").strip()
        if ans.lower() != "y":
            sys.exit("已取消")

    adapter = TushareAdapter(token=settings.tushare_token)
    ok = fail = 0
    failed: list[date] = []
    t0 = time.monotonic()
    for i, td in enumerate(todo, 1):
        try:
            df = await _fetch_with_retry(adapter, td)
            if df.empty:
                logger.warning("%s 返回 0 行——交易日不该为空，记失败以便复查", td)
                fail += 1
                failed.append(td)
            else:
                async with AsyncSessionLocal() as session:
                    n = await MarketDataRepository(session).upsert_money_flow(df)
                    await session.commit()
                ok += 1
                if i % 20 == 0 or i == len(todo):
                    rate = (time.monotonic() - t0) / i
                    logger.info("[%d/%d] %s rows=%d  %.1fs/日  ETA %.0f min",
                                i, len(todo), td, n, rate, rate * (len(todo) - i) / 60)
        except Exception:
            fail += 1
            failed.append(td)
            logger.exception("%s 回填失败", td)
        await asyncio.sleep(args.day_sleep)

    logger.info("完成：ok=%d fail=%d failed=%s", ok, fail, [str(d) for d in failed])
    await _report_table_size()
    if fail:
        sys.exit(1)


def main() -> None:
    asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    main()
