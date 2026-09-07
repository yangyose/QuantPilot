"""修复历史财务数据的前视偏差：清除公告日之前就已存在的基本面值。

## 缺陷

`fetch_financial_data` 写出的行是 `publish_date = 交易日`，语义为「该日已知」。
但它调的 `fina_indicator(period=最近季度末)` 返回的是**该期最终值**，与 as_of 无关：
回填历史时该期早已披露 → 把 8 月才公布的中报写进了 7 月的行。

实测（5434 与生产逐条一致）：`report_period=2025-06-30` 且 `publish_date=2025-07-15`
的 5408 行中，5384 行等于该期**最终公布值**、等于上一期值的仅 2 行——
是真的提前写了未来值，不是「沿用上期」的误标。

源头已修（`_truncate_to_announced`，按 `ann_date` 截断）；本脚本修**存量数据**。

## 公告日从哪来（无需重新调 API）

`total_equity` 走的是 `fetch_balance_sheet`，其 `publish_date = ann_date`，
故 `min(publish_date) WHERE total_equity IS NOT NULL` 即该 (股票, 报告期) 的公告日。

**该代理已用唯一有真值的一期校准**：2026-06-30 是实时采集（未公告即 NULL），
其 `yoy` 首次出现日与 `total_equity` 锚点日在 179 只可比股票上**完全一致**
（平均差 0.00 天）。

⚠️ 无锚点的 (股票, 报告期) **原样保留**——宁可留下少量未修，不猜公告日。
覆盖率实测 91~99.6%，脚本会报出未覆盖数。

## 安全

- 默认 `--dry-run`，只报数不改数
- `--apply` 前**必须**先写精确回滚点（受影响行的键 + 四个字段值，CSV）
- 按 `report_period` 分批，单批一个事务
- 收尾**自查**：重新跑探测查询，必须为 0；不信 UPDATE 的返回值

用法：

    DATABASE_URL=postgresql+asyncpg://...:5434/quantpilot \
      uv run python scripts/repair_financial_lookahead.py --dry-run
    ... --apply --backup-dir var/backup/lookahead
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quantpilot.core.database import AsyncSessionLocal  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("repair_lookahead")

_FIELDS = ("net_profit_yoy", "roe", "revenue_yoy", "debt_to_asset")
_DIRTY = " OR ".join(f"f.{c} IS NOT NULL" for c in _FIELDS)

_ANN_CTE = """
WITH ann AS (
    SELECT ts_code, report_period, min(publish_date) AS a
    FROM financial_data WHERE total_equity IS NOT NULL
    GROUP BY ts_code, report_period
)
"""


async def _scan(session) -> list[tuple[date, int]]:
    """按报告期统计待修行数。"""
    rows = (await session.execute(text(f"""
        {_ANN_CTE}
        SELECT f.report_period, count(*)
        FROM financial_data f JOIN ann
          ON ann.ts_code = f.ts_code AND ann.report_period = f.report_period
        WHERE f.publish_date < ann.a AND ({_DIRTY})
        GROUP BY 1 ORDER BY 1
    """))).all()
    return [(r[0], int(r[1])) for r in rows]


async def _uncovered(session) -> int:
    """有基本面值但**无公告日锚点**的 (股票, 报告期) 数——这些原样保留。"""
    return int((await session.execute(text("""
        WITH ann AS (
            SELECT ts_code, report_period FROM financial_data
            WHERE total_equity IS NOT NULL GROUP BY 1,2
        ), have AS (
            SELECT ts_code, report_period FROM financial_data
            WHERE net_profit_yoy IS NOT NULL OR roe IS NOT NULL GROUP BY 1,2
        )
        SELECT count(*) FROM have LEFT JOIN ann USING (ts_code, report_period)
        WHERE ann.ts_code IS NULL
    """))).scalar_one())


async def _backup(session, out: Path, period: date) -> int:
    """把该报告期待修行的键与原值写入 CSV（精确回滚点）。"""
    rows = (await session.execute(text(f"""
        {_ANN_CTE}
        SELECT f.ts_code, f.report_period, f.publish_date,
               f.net_profit_yoy, f.roe, f.revenue_yoy, f.debt_to_asset
        FROM financial_data f JOIN ann
          ON ann.ts_code = f.ts_code AND ann.report_period = f.report_period
        WHERE f.publish_date < ann.a AND ({_DIRTY}) AND f.report_period = :p
    """), {"p": period})).all()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ts_code", "report_period", "publish_date", *_FIELDS])
        for r in rows:
            w.writerow(list(r))
    return len(rows)


async def _apply(session, period: date) -> int:
    sets = ", ".join(f"{c} = NULL" for c in _FIELDS)
    res = await session.execute(text(f"""
        {_ANN_CTE}
        UPDATE financial_data f SET {sets}, updated_at = NOW()
        FROM ann
        WHERE ann.ts_code = f.ts_code AND ann.report_period = f.report_period
          AND f.publish_date < ann.a AND ({_DIRTY}) AND f.report_period = :p
    """), {"p": period})
    return int(res.rowcount or 0)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--apply", action="store_true", default=False)
    ap.add_argument("--backup-dir", default="var/backup/lookahead")
    args = ap.parse_args()
    if args.apply == args.dry_run:
        ap.error("必须且只能指定 --dry-run 或 --apply 其一")

    async with AsyncSessionLocal() as s:
        scan = await _scan(s)
        total = sum(n for _, n in scan)
        uncov = await _uncovered(s)
        logger.info(
            "lookahead_scan periods=%d rows=%d uncovered_pairs=%d",
            len(scan), total, uncov,
        )
        for p, n in scan:
            logger.info("  %s  %d 行", p, n)
        if uncov:
            logger.warning(
                "%d 个 (股票,报告期) 无公告日锚点 → **原样保留**（不猜公告日）", uncov
            )
        if args.dry_run:
            logger.info("dry-run：未改动任何数据")
            return

        bdir = Path(args.backup_dir)
        done = 0
        for p, n in scan:
            saved = await _backup(s, bdir / f"{p}.csv", p)
            if saved != n:
                raise RuntimeError(f"{p} 备份 {saved} 行 ≠ 扫描 {n} 行，中止")
            got = await _apply(s, p)
            await s.commit()
            done += got
            logger.info("  %s 已修 %d 行（备份 %s）", p, got, bdir / f"{p}.csv")

        # 自查：不信 rowcount，重新扫一遍
        left = sum(n for _, n in await _scan(s))
        logger.info("lookahead_repair_done updated=%d remaining=%d", done, left)
        if left:
            raise RuntimeError(f"收尾自查失败：仍有 {left} 行未修")


asyncio.run(main())
