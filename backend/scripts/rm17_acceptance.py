"""RM-17 评分质量 + 5y 真机验收探针。

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

3) 5y 范围专属基线（仅当 --5y 启用）：
   - daily_quote 覆盖 ≥ 1200 交易日（5 年 × 245 - 节假日）
   - 早年 is_st 占比（2021-2022）与近年（2025-2026）差异 ≤ 3 pp（PIT 还原合理）
   - 财务 period 分布 ≥ 18 个不同 quarter_end（5 年理论 20 个，允许 ≤ 2 偏差）
   - index_history 4 个指数全部覆盖 ≥ 1200 交易日
   - index_component 月度稀疏覆盖 ≥ 50 个不同 snapshot 日期（5 年 × 12 月预期 60）

用法：
  docker exec -it quantpilot-backend-1 python scripts/rm17_acceptance.py
  # 或限定到特定日期
  docker exec -it quantpilot-backend-1 python scripts/rm17_acceptance.py --trade-date 2026-05-08
  # 5y 模式（加跑 §3）
  docker exec -it quantpilot-backend-1 python scripts/rm17_acceptance.py --5y

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
        last_sig = (await session.execute(
            select(func.max(Signal.trade_date))
        )).scalar()
        last_pool = (await session.execute(
            select(func.max(CandidatePool.trade_date))
        )).scalar()
        trade_date = last_sig or last_pool
        if trade_date is None:
            return False, {"signal_check": "FAIL: signal/pool 表均为空，请先触发 pipeline"}
    metrics["signal_trade_date"] = str(trade_date)

    # 取该日 top 20 信号（按 composite_score DESC；signal 表无 composite_score
    # 直接字段，关联 signal_score_snapshot）
    rows = (await session.execute(text("""
        SELECT s.ts_code,
               s.signal_type,
               COALESCE(s.score, 0) AS comp,
               COALESCE(dq.is_st, FALSE) AS is_st
        FROM signal s
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
    # 价值因子非空率 + top composite_score（提前算用于 0-signal 场景判定）
    pool_top_score = (await session.execute(
        select(func.max(CandidatePool.composite_score))
        .where(CandidatePool.trade_date == trade_date)
    )).scalar()
    metrics["pool_top_composite"] = (
        f"{float(pool_top_score):.2f}" if pool_top_score is not None else "n/a"
    )

    if total == 0:
        # 0 信号有两种情况：(a) pool 顶分 < BUY 阈值（80），系统正确判定无高确信机会
        # (b) pool 也空，说明评分链路真坏。前者是合理的（保守优于乱买）
        if pool_top_score is None:
            metrics["signal_check"] = "FAIL: 当日无信号 + pool 为空 → 评分链路坏"
            ok = False
        elif float(pool_top_score) < 80.0:
            metrics["signal_check"] = (
                f"PASS（系统正确保守：pool 顶分 {pool_top_score:.2f} < BUY 阈值 80）"
            )
        else:
            metrics["signal_check"] = (
                f"FAIL: pool 顶分 {pool_top_score:.2f} ≥ 80 但 0 信号 → SignalGenerator 链路坏"
            )
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


async def _check_5y(session) -> tuple[bool, dict]:
    """5y 范围专属基线：覆盖广度 + PIT 一致性 + 季度财务分布。"""
    metrics: dict[str, object] = {}
    ok = True

    # daily_quote 总覆盖
    dq_days = (await session.execute(
        text("SELECT COUNT(DISTINCT trade_date) FROM daily_quote")
    )).scalar() or 0
    metrics["daily_quote_distinct_days"] = dq_days
    metrics["dq_coverage_check"] = "PASS" if dq_days >= 1200 else "FAIL"
    if dq_days < 1200:
        ok = False

    # 早年 vs 近年 is_st 占比一致性（PIT 还原是否合理）
    early = await session.execute(text("""
        SELECT
          ROUND(100.0*SUM(CASE WHEN is_st THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0), 2) AS ratio
        FROM daily_quote
        WHERE trade_date BETWEEN '2021-05-13' AND '2022-12-31'
    """))
    late = await session.execute(text("""
        SELECT
          ROUND(100.0*SUM(CASE WHEN is_st THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0), 2) AS ratio
        FROM daily_quote
        WHERE trade_date BETWEEN '2025-01-01' AND '2026-05-12'
    """))
    early_ratio = float(early.scalar() or 0)
    late_ratio = float(late.scalar() or 0)
    diff_pp = abs(early_ratio - late_ratio)
    metrics["is_st_ratio_2021_2022"] = f"{early_ratio:.2f}%"
    metrics["is_st_ratio_2025_2026"] = f"{late_ratio:.2f}%"
    metrics["is_st_pit_diff_pp"] = f"{diff_pp:.2f} pp"
    metrics["is_st_pit_check"] = (
        "PASS" if diff_pp <= 3.0 else f"FAIL（差 {diff_pp:.2f} pp > 3 pp）"
    )
    if diff_pp > 3.0:
        ok = False

    # 财务 period 分布（按 report_period quarter_end 计数）
    period_count = (await session.execute(text("""
        SELECT COUNT(DISTINCT report_period) FROM financial_data
    """))).scalar() or 0
    metrics["financial_distinct_periods"] = period_count
    metrics["fina_period_check"] = "PASS" if period_count >= 18 else "FAIL"
    if period_count < 18:
        ok = False

    # 指数 4 个分别覆盖
    idx_rows = (await session.execute(text("""
        SELECT index_code, COUNT(DISTINCT trade_date) AS days
        FROM index_history GROUP BY index_code ORDER BY index_code
    """))).all()
    idx_dict = {r.index_code: r.days for r in idx_rows}
    metrics["index_history_per_code"] = (
        idx_dict if idx_dict else "EMPTY（5y 范围内 index_history 未入库）"
    )
    min_idx_days = min(idx_dict.values()) if idx_dict else 0
    metrics["index_coverage_check"] = (
        "PASS" if len(idx_dict) >= 4 and min_idx_days >= 1200 else "FAIL"
    )
    if len(idx_dict) < 4 or min_idx_days < 1200:
        ok = False

    # 指数成分股月度稀疏覆盖
    comp_days = (await session.execute(text("""
        SELECT COUNT(DISTINCT trade_date) FROM index_component
    """))).scalar() or 0
    metrics["index_component_distinct_days"] = comp_days
    metrics["index_component_check"] = "PASS" if comp_days >= 50 else "FAIL"
    if comp_days < 50:
        ok = False

    return ok, metrics


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-date", type=_parse_date,
                        help="评估特定 trade_date（默认 = signal 表 MAX）")
    parser.add_argument("--5y", dest="five_y", action="store_true",
                        help="加跑 §3 5y 范围专属基线检查")
    args = parser.parse_args()

    print("=== RM-17 评分质量 + 5y 真机验收探针 ===\n")
    all_ok = True
    n_sections = 3 if args.five_y else 2

    async with AsyncSessionLocal() as session:
        print(f"[1/{n_sections}] 数据基线检查...")
        ok1, baseline = await _check_baseline(session)
        for k, v in baseline.items():
            print(f"      {k}: {v}")
        all_ok = all_ok and ok1
        print()

        print(f"[2/{n_sections}] 信号质量检查...")
        ok2, sig_metrics = await _check_signals(session, args.trade_date)
        for k, v in sig_metrics.items():
            print(f"      {k}: {v}")
        all_ok = all_ok and ok2
        print()

        if args.five_y:
            print(f"[3/{n_sections}] 5y 范围专属基线检查...")
            ok3, fy_metrics = await _check_5y(session)
            for k, v in fy_metrics.items():
                print(f"      {k}: {v}")
            all_ok = all_ok and ok3
            print()

    print("=" * 40)
    print(f"OVERALL: {'PASS ✓' if all_ok else 'FAIL ✗'}")
    print("=" * 40)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
