"""跨制度（bull/bear/neutral/recent）触发 DailyPipeline 多日期验收脚本（task #117）。

依赖：refill_history.py 已完成 5y 数据回填。
本脚本对预选代表性交易日各跑一次 pipeline.run(td)，输出每日：
  - market_state（UPTREND / DOWNTREND / OSCILLATION）
  - market_state ADX / MA20 / MA60 指标
  - candidate_pool top composite_score
  - signal count（BUY/SELL/HOLD）
  - 跨日期对比（不同 regime 是否能识别不同的 market_state）

验收意图：
  1. 5y 数据扩展后，MarketStateEngine 在不同 regime 下能正确识别趋势
  2. ValueStrategy 5 年 PE/PB 真窗口分位（SDD §10）能跑出来（之前只有 78 日是降级路径）
  3. 评分排名跨 regime 时不同因子贡献度差异（bull 时 momentum 主导 / bear 时 value 主导）

预选交易日（可用 --dates 覆盖）：
  - bull-2024-09-30: 924 反弹后月末（A 股牛市起点）
  - bear-2022-04-25: 俄乌 + 疫情封控低点
  - neutral-2023-06-30: 23 H1 盘整段
  - recent-2026-05-12: 当前最近一日（参照基线）

用法（容器内）：
  docker exec -it quantpilot-backend-1 python scripts/pipeline_multi_date.py
  # 自定义日期
  docker exec -it quantpilot-backend-1 python scripts/pipeline_multi_date.py \
    --dates 2024-09-30,2022-04-25,2023-06-30

退出码：0=全部跑通 / 1=任一失败 / 2=用法错。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import date, datetime
from typing import Iterable

from sqlalchemy import select, text

from quantpilot.core.config import settings
from quantpilot.core.database import AsyncSessionLocal
from quantpilot.data.adapters.tushare import TushareAdapter
from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.validators import DataValidator
from quantpilot.models.business import MarketStateHistory
from quantpilot.pipeline.daily_pipeline import DailyPipeline

DEFAULT_DATES = [
    ("bull",    date(2024, 9, 30)),    # 924 反弹后月末
    ("bear",    date(2022, 4, 25)),    # 俄乌 + 疫情封控低点
    ("neutral", date(2023, 6, 30)),    # 23 H1 盘整段
    ("recent",  date(2026, 5, 12)),    # 最近一日参照
]


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_dates(s: str) -> list[tuple[str, date]]:
    return [(f"d{i+1}", _parse_date(d.strip())) for i, d in enumerate(s.split(","))]


async def _run_pipeline_for(td: date, calendar: TradingCalendar) -> dict:
    """对 trade_date 跑一次 pipeline，返回结果摘要。"""
    adapter = TushareAdapter(token=settings.tushare_token)
    validator = DataValidator()

    pipeline = DailyPipeline(
        session_factory=AsyncSessionLocal,
        adapter=adapter,
        validator=validator,
        calendar=calendar,
        redis=None,
        notification_channel=None,
    )

    start_t = time.perf_counter()
    run = await pipeline.run(td)
    duration = time.perf_counter() - start_t

    # 拉详细指标
    async with AsyncSessionLocal() as session:
        ms_row = (await session.execute(
            select(MarketStateHistory).where(MarketStateHistory.trade_date == td)
        )).scalar_one_or_none()
        pool_top = (await session.execute(
            text("SELECT MAX(composite_score) FROM candidate_pool WHERE trade_date = :td"),
            {"td": td},
        )).scalar()
        pool_count = (await session.execute(
            text("SELECT COUNT(*) FROM candidate_pool WHERE trade_date = :td"),
            {"td": td},
        )).scalar() or 0
        sig_breakdown = (await session.execute(
            text("""
                SELECT signal_type, COUNT(*) AS n FROM signal
                WHERE trade_date = :td GROUP BY signal_type
            """),
            {"td": td},
        )).all()

    return {
        "trade_date": str(td),
        "status": run.status,
        "duration_s": round(duration, 1),
        "market_state": ms_row.market_state if ms_row else "n/a",
        "adx": float(ms_row.adx_value) if ms_row and ms_row.adx_value is not None else None,
        "ma20": float(ms_row.ma20) if ms_row and ms_row.ma20 is not None else None,
        "ma60": float(ms_row.ma60) if ms_row and ms_row.ma60 is not None else None,
        "pool_count": pool_count,
        "pool_top_score": float(pool_top) if pool_top is not None else None,
        "signals": {row.signal_type: row.n for row in sig_breakdown},
        "signal_count_total": int(run.signal_count or 0),
        "error_msg": run.error_msg,
    }


async def _check_data_ready(target_dates: list[date]) -> tuple[bool, list[date]]:
    """快速检查目标日期是否在 DB 中已 fully ingested（daily_quote ∩ financial_data）。"""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text("""
            SELECT DISTINCT dq.trade_date
            FROM daily_quote dq
            INNER JOIN financial_data fd ON dq.trade_date = fd.publish_date
            WHERE dq.trade_date = ANY(:tds)
        """), {"tds": target_dates})).all()
    present = {r.trade_date for r in rows}
    missing = [td for td in target_dates if td not in present]
    return len(missing) == 0, missing


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dates",
        help="逗号分隔的 trade_date 列表（YYYY-MM-DD,YYYY-MM-DD,...）；"
             "未指定时用默认 bull/bear/neutral/recent 4 个代表性日期",
    )
    args = parser.parse_args()

    if not settings.tushare_token:
        print("ERROR: TUSHARE_TOKEN 未配置", file=sys.stderr)
        return 2

    targets: Iterable[tuple[str, date]] = (
        _parse_dates(args.dates) if args.dates else DEFAULT_DATES
    )
    targets = list(targets)
    print("=== Pipeline multi-date 验收 ===\n")
    print("Targets:")
    for label, td in targets:
        print(f"  - {label}: {td}")
    print()

    # 数据齐全性预检
    target_dates = [td for _, td in targets]
    ok, missing = await _check_data_ready(target_dates)
    if not ok:
        print(f"⚠️  Missing data for: {missing}")
        print("先跑 refill_history.py 把这些日期补齐再回来。")
        return 2
    print("✓ All target dates fully ingested\n")

    # 拉日历
    adapter = TushareAdapter(token=settings.tushare_token)
    min_d = min(target_dates)
    max_d = max(target_dates)
    from datetime import timedelta  # noqa: PLC0415
    calendar = await TradingCalendar.from_adapter(
        adapter, min_d - timedelta(days=180), max_d + timedelta(days=30)
    )

    all_ok = True
    results: list[tuple[str, dict]] = []
    for i, (label, td) in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] Running pipeline for {label} = {td}...")
        if not calendar.is_trade_date(td):
            print(f"      ⚠️  {td} 非交易日，跳过")
            continue
        try:
            r = await _run_pipeline_for(td, calendar)
            results.append((label, r))
            print(f"      status={r['status']}  duration={r['duration_s']}s")
            print(f"      market_state={r['market_state']}  adx={r['adx']}  "
                  f"ma20={r['ma20']}  ma60={r['ma60']}")
            print(f"      pool: count={r['pool_count']}  top={r['pool_top_score']}")
            print(f"      signals total={r['signal_count_total']}  "
                  f"breakdown={r['signals']}")
            if r["status"] != "SUCCESS":
                all_ok = False
                print(f"      ❌ ERROR: {r['error_msg']}")
        except Exception as exc:  # noqa: BLE001
            print(f"      ❌ EXCEPTION: {exc}")
            all_ok = False
        print()

    # 跨制度对比汇总
    if results:
        print("=" * 60)
        print("跨制度对比：")
        print(f"{'label':<10} {'date':<12} {'state':<12} {'adx':>6} "
              f"{'pool_top':>9} {'sig_total':>10}")
        for label, r in results:
            adx = f"{r['adx']:.1f}" if r["adx"] is not None else "n/a"
            top = f"{r['pool_top_score']:.2f}" if r["pool_top_score"] else "n/a"
            print(f"{label:<10} {r['trade_date']:<12} {r['market_state']:<12} "
                  f"{adx:>6} {top:>9} {r['signal_count_total']:>10}")
        print("=" * 60)

        # 期望：不同 label 应能看到至少 2 种不同的 market_state
        states = {r["market_state"] for _, r in results if r["market_state"] != "n/a"}
        if len(states) >= 2:
            print(f"✓ 识别出 {len(states)} 种 market_state: {states}")
        else:
            print(f"⚠️  只识别出 1 种 market_state（{states}），"
                  "MarketStateEngine 可能未对不同制度产生差异化判定")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
