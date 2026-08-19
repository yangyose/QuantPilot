"""V1.5-C C0-7：拆分 daily/aggregate 撞车合并的 factor_ic_window_state 行。

**背景**（详见 alembic 0025 docstring）：Phase 11 建的全表唯一约束不含 `row_type`，
月末当日市场状态若等于某 aggregate 行的 state，daily 与 aggregate 会合并成一行。
生产实测 156 行 / 39 个月末（2022-05 ~ 2026-03）。

**识别指纹**：`row_type='aggregate' AND ic_value IS NOT NULL`。
`upsert_ic_aggregate` 的 values/set_ 都不含 `ic_value`，故正常 aggregate 行该列恒
为 NULL；非 NULL 只可能是日级写入残留。（`monthly_quality` 行确实带 ic_value，但
row_type 不同且 state='ALL' 哨兵，不在本脚本范围。）

**两个方向**（取决于当初谁最后写，逐行判别）：

| 最后写者 | daily.ic_value | daily.sample_size | aggregate 统计列 |
|---------|---------------|-------------------|-----------------|
| aggregate（生产 156 行） | 残留完好 | **已丢失** | 完好 |
| daily（本地 2026-06-30） | 完好 | 完好 | sample_size 被覆盖 |

判别依据是硬的：ICIR 窗口固定 252 交易日（`_ICIR_WINDOW_DAYS`），故 aggregate 的
`sample_size` **恒 ≤ 252**；日级是横截面股票数（生产 ~2900）。因此
`sample_size > _MAX_AGG_SAMPLE` ⇒ 存的是日级值。

**【降级说明】**：当 aggregate 最后写时，日级 `sample_size` 不可还原（无任何列保留
其副本）。本脚本写 0 并计数上报。
- 影响：仅 `aggregate_monthly` 的 `avg_xs_sample` 展示列（月度平均横截面样本数）
  会被这些行拉低；`ic_value` 完好，**ICIR / 权重校准全链路不受影响**（
  `rolling_icir_state` 只读 ic_value）。
- 恢复条件：在本地算力中心对这些 trade_date 跑
  `scripts/backfill_daily_ic.py --force`（每日约 8 min），即可精确重算。

**aggregate 侧**：本脚本只把 `ic_value` 置 NULL，不动统计列。被覆盖的 aggregate
`sample_size` 交由 `scripts/backfill_icir_rebalance.py --force` 重算（精确，无需估算）。

**执行顺序（不可反）**：alembic 0025 → 本脚本 → backfill_icir_rebalance --force。
先跑本脚本会因旧的 4 列唯一约束插不进 daily 行。

用法：
    uv run python scripts/repair_ic_row_type_collision.py --dry-run
    uv run python scripts/repair_ic_row_type_collision.py --skip-confirm
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text

from quantpilot.core.database import AsyncSessionLocal

logger = logging.getLogger("repair_ic_row_type_collision")

# ICIR 窗口固定 252 交易日 → aggregate 的 sample_size 恒 ≤ 252。留出余量取 300。
_MAX_AGG_SAMPLE = 300

_FINGERPRINT = "row_type = 'aggregate' AND ic_value IS NOT NULL"


@dataclass(frozen=True)
class _Polluted:
    id: int
    strategy: str
    factor: str
    state: str
    trade_date: date
    ic_value: float
    sample_size: int

    @property
    def daily_sample_size_known(self) -> bool:
        """存的 sample_size 是否为日级横截面值（见模块 docstring 判别依据）。"""
        return self.sample_size > _MAX_AGG_SAMPLE


async def _load_polluted(session) -> list[_Polluted]:
    rows = (await session.execute(text(
        "SELECT id, strategy, factor, state, trade_date, ic_value, sample_size "
        f"FROM factor_ic_window_state WHERE {_FINGERPRINT} "
        "ORDER BY trade_date, state, strategy"
    ))).all()
    return [
        _Polluted(
            id=r[0], strategy=r[1], factor=r[2], state=r[3],
            trade_date=r[4], ic_value=float(r[5]), sample_size=int(r[6]),
        )
        for r in rows
    ]


async def _preflight(session) -> None:
    """确认 alembic 0025 已生效——否则 daily 行插不进去（旧 4 列唯一约束）。"""
    has_new = (await session.execute(text(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conrelid = 'factor_ic_window_state'::regclass "
        "AND conname = 'uq_factor_ic_window_state_skftr'"
    ))).scalar_one()
    if not has_new:
        raise SystemExit(
            "阻断：未检测到 uq_factor_ic_window_state_skftr。"
            "请先 `uv run alembic upgrade head` 应用 0025 再跑本脚本。"
        )


async def split_polluted_rows(session, polluted: list[_Polluted]) -> tuple[int, int]:
    """把合并行拆回 daily + aggregate 两行。**不 commit**，由调用方决定事务边界。

    Returns:
        ``(新增/更新的 daily 行数, 被清空 ic_value 的 aggregate 行数)``
    """
    for p in polluted:
        # ON CONFLICT 走 0025 的 5 列唯一键 → 重复执行幂等
        await session.execute(text(
            "INSERT INTO factor_ic_window_state "
            "(strategy, factor, state, trade_date, ic_value, sample_size, row_type) "
            "VALUES (:s, :f, :st, :d, :ic, :n, 'daily') "
            "ON CONFLICT (strategy, factor, state, trade_date, row_type) "
            "DO UPDATE SET ic_value = EXCLUDED.ic_value, "
            "sample_size = EXCLUDED.sample_size"
        ), {
            "s": p.strategy, "f": p.factor, "st": p.state, "d": p.trade_date,
            "ic": p.ic_value,
            "n": p.sample_size if p.daily_sample_size_known else 0,
        })

    # 清空 aggregate 行的 ic_value（该列本就不属于 aggregate 语义）
    result = await session.execute(text(
        f"UPDATE factor_ic_window_state SET ic_value = NULL WHERE {_FINGERPRINT}"
    ))
    return len(polluted), int(result.rowcount)


async def _repair(dry_run: bool, skip_confirm: bool) -> int:
    async with AsyncSessionLocal() as session:
        await _preflight(session)
        polluted = await _load_polluted(session)

        n = len(polluted)
        lossy = [p for p in polluted if not p.daily_sample_size_known]
        dates = sorted({p.trade_date for p in polluted})

        print(f"=== 拆分 daily/aggregate 合并行 | mode: {'DRY-RUN' if dry_run else 'APPLY'} ===")
        print(f"    被污染行数        : {n}")
        print(f"    涉及 trade_date   : {len(dates)}"
              + (f"（{dates[0]} ~ {dates[-1]}）" if dates else ""))
        print(f"    日级 sample_size 完好: {n - len(lossy)}")
        print(f"    日级 sample_size 丢失: {len(lossy)}  ← 写 0，见【降级说明】")
        if not n:
            print("    无需修复。")
            return 0
        if dry_run:
            for p in polluted[:10]:
                mark = "" if p.daily_sample_size_known else "  [sample_size→0]"
                print(f"      {p.trade_date} {p.state:12s} {p.strategy:15s} "
                      f"ic={p.ic_value:+.4f}{mark}")
            if n > 10:
                print(f"      … 其余 {n - 10} 行略")
            return 0

        if not skip_confirm:
            ans = input(f"将新增 {n} 条 daily 行并清空对应 aggregate 行的 ic_value，继续？[y/N] ")
            if ans.strip().lower() != "y":
                print("已取消。")
                return 1

        n_daily, n_cleared = await split_polluted_rows(session, polluted)
        await session.commit()   # 自建 session 必须显式 commit

        print(f"[done] 新增/更新 daily 行 {n_daily}；清空 aggregate.ic_value {n_cleared}")
        if lossy:
            logger.warning(
                "daily_sample_size_lost count=%d dates=%s —— 已写 0，"
                "精确值需在本地算力中心对这些日期跑 backfill_daily_ic.py --force",
                len(lossy), sorted({p.trade_date.isoformat() for p in lossy})[:5],
            )
        print("[next] 必须接着跑：scripts/backfill_icir_rebalance.py --force "
              "（重算被覆盖的 aggregate 统计列）")
        return 0


def main() -> int:
    # Windows 控制台默认 cp932/gbk，直接 print 中文会 UnicodeEncodeError 中断脚本。
    # 本脚本既在 Windows 本地跑（演练）也在 Linux 容器跑（生产），故在入口统一兜底。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写库")
    parser.add_argument("--skip-confirm", action="store_true", help="跳过交互确认")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(_repair(args.dry_run, args.skip_confirm))


if __name__ == "__main__":
    sys.exit(main())
