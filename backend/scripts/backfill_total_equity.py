"""历史 `total_equity` 回填（balancesheet → financial_data）。

## 缺口来源

`total_equity`（总股东权益）来自 Tushare `balancesheet`，与日频 `fina_indicator`
不是同一个接口。2026-08 那轮回填只覆盖了当时的近两年默认窗口（`refresh_financials_full`
的 `start_date=None` → 今日-2y），**更早的报告期从未补过**。

后果不报错、但很实在：`universe.filter` 的 **F-4（净资产过滤）在 total_equity 全为
NULL 时整段跳过**，只留一条 `universe_filter_skipped_null_field` 的 INFO。于是历史
日期上的 universe 与今天口径不一致——2026-09-04 跑 V1.5-K 面板时被这条日志暴露。

## 安全性

- `upsert_financial_data` 用 `ON CONFLICT (ts_code, report_period, publish_date)`
  + COALESCE **保留已有非 NULL 值** → 合并而非覆盖，对既有数据无损
- `refresh_financials_full` 每批包 savepoint，坏批不毒化整个事务
- 只新增 balancesheet 公告日行（实测约 +3.8 万行 / 5515 只）

## ⚠️ 判据：不认 success_count

历史 bug 的指纹正是「**success 照计而 total_equity 恒 NULL**」
（2026-06-30 生产实证 success=0 fail=5515；修完 arity 后又出现过多码返空的形态）。
故本脚本收尾**自己查一次库**，报告各报告期的实际非空数；
调用方还应再验一次「面板/评分日志里那条 F-4 跳过警告是否消失」——那才是机制生效的痕迹。

## 用法

    # 算力机
    DATABASE_URL=postgresql+asyncpg://quantpilot:quantpilot@localhost:5434/quantpilot \\
      uv run python scripts/backfill_total_equity.py --start 2021-01-01 --end 2024-06-30

    # 生产（容器内，detached）
    docker compose -f docker-compose.prod.yml --env-file .env.prod exec -d backend \\
      python scripts/backfill_total_equity.py --start 2021-01-01 --end 2024-06-30

实测 0.24 s/股 → 5515 只约 22 分钟。判断存活看落盘日志的 `chunk` 行，
不认通知、不认退出码（CLAUDE.md §4.11）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quantpilot.core.config import settings  # noqa: E402
from quantpilot.core.database import AsyncSessionLocal  # noqa: E402
from quantpilot.data.adapters.tushare import TushareAdapter  # noqa: E402
from quantpilot.data.calendar import TradingCalendar  # noqa: E402
from quantpilot.data.repository import MarketDataRepository  # noqa: E402
from quantpilot.data.validators import DataValidator  # noqa: E402
from quantpilot.models.market import FinancialData  # noqa: E402
from quantpilot.services.data_service import DataService  # noqa: E402

_CHUNK = 500


def emit(msg: str) -> None:
    """按字节写出——Windows 非 UTF-8 控制台下 print 中文经管道会崩（§4.12）。"""
    sys.stdout.buffer.write((msg + "\n").encode("utf-8"))
    sys.stdout.flush()


async def _coverage(start: date, end: date) -> list[tuple[date, int]]:
    """各报告期的 total_equity 非空数——**真实痕迹**，不是 success_count。"""
    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                select(
                    FinancialData.report_period,
                    func.count(FinancialData.total_equity),
                )
                .where(FinancialData.report_period.between(start, end))
                .group_by(FinancialData.report_period)
                .order_by(FinancialData.report_period)
            )
        ).all()
    return [(r[0], int(r[1])) for r in rows]


async def main() -> int:
    ap = argparse.ArgumentParser(description="历史 total_equity 回填")
    ap.add_argument("--start", required=True, help="报告期窗口起 YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="报告期窗口止 YYYY-MM-DD")
    ap.add_argument("--chunk", type=int, default=_CHUNK)
    ap.add_argument("--dry-run", action="store_true", help="只报当前覆盖，不写库")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    before = await _coverage(start, end)
    emit(f"BEFORE window=[{start}, {end}]")
    for rp, n in before:
        emit(f"  {rp} has_te={n}")

    if args.dry_run:
        emit("DRY-RUN 未写库")
        return 0

    async with AsyncSessionLocal() as s:
        codes = await MarketDataRepository(s).get_active_stock_codes()
    emit(f"START stocks={len(codes)} chunk={args.chunk}")

    t0 = time.perf_counter()
    ok = fail = 0
    for i in range(0, len(codes), args.chunk):
        chunk = codes[i : i + args.chunk]
        async with AsyncSessionLocal() as s:
            repo = MarketDataRepository(s)
            cal = await TradingCalendar.from_repo(repo, start, date.today())
            svc = DataService(
                TushareAdapter(settings.tushare_token), DataValidator(), repo, cal,
            )
            try:
                res = await svc.refresh_financials_full(
                    ts_codes=chunk, start_date=start, end_date=end,
                )
                await s.commit()
                ok += res["success_count"]
                fail += res["fail_count"]
            except Exception as exc:  # noqa: BLE001 - 单块失败不阻断整体，但必须留痕
                fail += len(chunk)
                emit(f"chunk_failed at={i} n={len(chunk)} err={exc!r}")
        done = min(i + args.chunk, len(codes))
        el = time.perf_counter() - t0
        emit(
            f"chunk {done}/{len(codes)} ok={ok} fail={fail} "
            f"elapsed={el:.0f}s eta={(el / done) * (len(codes) - done):.0f}s"
        )

    after = await _coverage(start, end)
    emit(f"AFTER  ok={ok} fail={fail} elapsed={time.perf_counter() - t0:.0f}s")
    prev = dict(before)
    for rp, n in after:
        emit(f"  {rp} has_te={n} (+{n - prev.get(rp, 0)})")
    emit("EXIT")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
