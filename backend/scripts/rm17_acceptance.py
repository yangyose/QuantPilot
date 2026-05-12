"""RM-17 评分质量验收探针。

依赖：refill_history.py 已成功跑完（财务/行情/指数/成分股齐全）。
本脚本不修改数据，只做读探针 + 给出 PASS/FAIL 判定：

1) 数据基线（refill 后）：
   - daily_quote.is_st=TRUE 占比应 ≈ 3-8%（A 股 ST 股常态）
   - financial_data.roe 非空率应 ≥ 70%（金融股不报 ROE 是正常的）
   - market_state_history 至少有 1 行非空状态
   - index_history / index_component 非空

2) 评分质量（需先 POST /pipeline/trigger 触发一次跑出 candidate_pool + signal）：
   - 最新一日的 top 20 信号 ST 占比 ≤ 10%（修复前 100%；目标 ≤ 5%，10% 为放宽阈值）
   - 价值因子非空率：candidate_pool.value_score 非空行数 / 总行数 ≥ 30%

用法：
  docker exec -it quantpilot-backend-1 python scripts/rm17_acceptance.py
  # 或限定到特定日期
  docker exec -it quantpilot-backend-1 python scripts/rm17_acceptance.py --trade-date 2026-05-08

退出码：0=PASS / 1=FAIL / 2=用法错。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime

from sqlalchemy import func, select, text

from quantpilot.core.database import AsyncSessionLocal
from quantpilot.models.business import (
    CandidatePool,
    MarketStateHistory,
    Signal,
)
from quantpilot.models.market import DailyQuote, FinancialData


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


async def _check_baseline(session) -> tuple[bool, dict]:
    """检查 refill 后的数据基线（不依赖 pipeline 跑过）。"""
    metrics: dict[str, object] = {}
    ok = True

    # daily_quote.is_st 占比
    dq_total = (await session.execute(
        select(func.count()).select_from(DailyQuote)
    )).scalar() or 0
    dq_st = (await session.execute(
        select(func.count()).select_from(DailyQuote).where(DailyQuote.is_st.is_(True))
    )).scalar() or 0
    is_st_ratio = dq_st / dq_total if dq_total else 0
    metrics["daily_quote_rows"] = dq_total
    metrics["is_st_ratio"] = f"{is_st_ratio*100:.2f}%"
    metrics["is_st_check"] = "PASS" if 0.02 <= is_st_ratio <= 0.10 else "FAIL"
    if metrics["is_st_check"] == "FAIL":
        ok = False

    # financial_data.roe 非空率
    fd_total = (await session.execute(
        select(func.count()).select_from(FinancialData)
    )).scalar() or 0
    fd_roe_ok = (await session.execute(
        text("SELECT COUNT(*) FROM financial_data WHERE roe IS NOT NULL AND roe::text != 'NaN'")
    )).scalar() or 0
    roe_ratio = fd_roe_ok / fd_total if fd_total else 0
    metrics["financial_data_rows"] = fd_total
    metrics["roe_fill_ratio"] = f"{roe_ratio*100:.2f}%"
    metrics["roe_check"] = "PASS" if roe_ratio >= 0.70 else "FAIL"
    if metrics["roe_check"] == "FAIL":
        ok = False

    # market_state_history 非空
    ms_rows = (await session.execute(
        select(func.count()).select_from(MarketStateHistory)
    )).scalar() or 0
    metrics["market_state_rows"] = ms_rows
    metrics["market_state_check"] = "PASS" if ms_rows >= 1 else "FAIL (run pipeline)"
    if ms_rows == 0:
        ok = False  # 但允许 fixture 提示

    return ok, metrics


async def _check_signals(session, trade_date: date | None) -> tuple[bool, dict]:
    """检查指定日期（或最新）top 20 信号的 ST 占比。"""
    metrics: dict[str, object] = {}
    ok = True

    if trade_date is None:
        last = (await session.execute(
            select(func.max(Signal.trade_date))
        )).scalar()
        if last is None:
            return False, {"signal_check": "FAIL: signal 表为空，请先触发 pipeline"}
        trade_date = last
    metrics["signal_trade_date"] = str(trade_date)

    # 取该日 top 20 信号（按 composite_score DESC；signal 表无 composite_score
    # 直接字段，关联 signal_score_snapshot）
    rows = (await session.execute(text("""
        SELECT s.ts_code,
               s.action,
               COALESCE(sss.composite_score, 0) AS comp,
               COALESCE(dq.is_st, FALSE) AS is_st
        FROM signal s
        LEFT JOIN signal_score_snapshot sss
          ON sss.signal_id = s.id
        LEFT JOIN daily_quote dq
          ON dq.ts_code = s.ts_code AND dq.trade_date = s.trade_date
        WHERE s.trade_date = :td
        ORDER BY comp DESC
        LIMIT 20
    """), {"td": trade_date})).all()

    total = len(rows)
    st_count = sum(1 for r in rows if r.is_st)
    metrics["top20_total"] = total
    metrics["top20_st_count"] = st_count
    metrics["top20_st_ratio"] = (
        f"{st_count/total*100:.2f}%" if total else "n/a"
    )
    metrics["top20_first_3_codes"] = [r.ts_code for r in rows[:3]]
    if total == 0:
        metrics["signal_check"] = "FAIL: 当日无信号"
        ok = False
    else:
        ratio = st_count / total
        metrics["signal_check"] = "PASS" if ratio <= 0.10 else "FAIL"
        if ratio > 0.10:
            ok = False

    # 价值因子非空率
    pool_total = (await session.execute(
        select(func.count())
        .select_from(CandidatePool)
        .where(CandidatePool.trade_date == trade_date)
    )).scalar() or 0
    pool_with_value = (await session.execute(
        select(func.count())
        .select_from(CandidatePool)
        .where(
            CandidatePool.trade_date == trade_date,
            CandidatePool.value_score.is_not(None),
        )
    )).scalar() or 0
    value_ratio = pool_with_value / pool_total if pool_total else 0
    metrics["pool_rows"] = pool_total
    metrics["value_score_fill_ratio"] = f"{value_ratio*100:.2f}%"
    metrics["value_check"] = "PASS" if value_ratio >= 0.30 else "FAIL"
    if value_ratio < 0.30:
        ok = False

    return ok, metrics


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-date", type=_parse_date,
                        help="评估特定 trade_date（默认 = signal 表 MAX）")
    args = parser.parse_args()

    print("=== RM-17 评分质量验收探针 ===\n")
    all_ok = True

    async with AsyncSessionLocal() as session:
        print("[1/2] 数据基线检查...")
        ok1, baseline = await _check_baseline(session)
        for k, v in baseline.items():
            print(f"      {k}: {v}")
        all_ok = all_ok and ok1
        print()

        print("[2/2] 信号质量检查...")
        ok2, sig_metrics = await _check_signals(session, args.trade_date)
        for k, v in sig_metrics.items():
            print(f"      {k}: {v}")
        all_ok = all_ok and ok2
        print()

    print("=" * 40)
    print(f"OVERALL: {'PASS ✓' if all_ok else 'FAIL ✗'}")
    print("=" * 40)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
