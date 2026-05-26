"""INT-P14-8-01：scripts/backfill_attribution_history.py 端到端集成测试。

依据 docs/design/phases/phase14_account_integrity.md §10.3。

种合成 panel：6 month_end × 30 codes 的 candidate_pool + daily_quote
（forward_return = Σ β·z + noise，β = AttributionService 期望值）；
调脚本核心 helper `_enumerate_month_ends` + 循环 `_run_one_month` 后断言：
- attribution_history 写入 24 行（6 month × 4 factor）
- 全部 calc_date 唯一且按月分布
- 每行 sample_size > 0, window_days = 20
"""
from __future__ import annotations

from datetime import date, timedelta
from importlib import import_module

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.attribution_repository import AttributionRepository
from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.repository import MarketDataRepository
from quantpilot.models.business import AttributionHistory
from quantpilot.models.market import DailyQuote, StockInfo
from quantpilot.services.attribution_service import AttributionService

_STRATEGIES = ["trend", "momentum", "mean_reversion", "value"]
_TRUE_BETAS = {"trend": 0.05, "momentum": 0.03, "mean_reversion": -0.02, "value": 0.04}


async def _seed_stock(session: AsyncSession, ts_code: str) -> None:
    session.add(StockInfo(
        ts_code=ts_code, name=f"T_{ts_code}",
        sw_industry_l1="银行", market="MAIN",
        list_date=date(2020, 1, 1), is_active=True,
    ))


async def _seed_quote(
    session: AsyncSession, ts_code: str, trade_date: date, close: float,
) -> None:
    session.add(DailyQuote(
        ts_code=ts_code, trade_date=trade_date,
        open=close, high=close * 1.01, low=close * 0.99,
        close=close, pre_close=close, pct_chg=0.0,
        vol=1_000_000, amount=close * 1_000_000.0,
        adj_factor=1.0, is_suspended=False, is_st=False,
        limit_up=False, limit_down=False,
    ))


def _build_breakdown_raw(z_values: dict[str, float]) -> dict:
    return {
        s: {"z_raw": float(z), "weight": 0.25, "contribution": float(z) * 0.25}
        for s, z in z_values.items()
    }


async def _seed_panel_for_backfill(
    db_session: AsyncSession,
    repo: MarketDataRepository,
    month_ends: list[date],
    n_codes: int = 30,
) -> None:
    """对每个 month_end：候选池 + base_close + end_close 三个事实表。

    与 test_int_attribution_monthly._seed_panel 同样的 panel 构造 —— 但每月
    单独一组样本（30 codes × 1 trade_date = 30 行/月，6 月 = 180 行）；
    每月 base_date = month_end，end_date = month_end + 30 天。
    """
    rng = np.random.default_rng(2026)
    codes = [f"P148{i:03d}.SH" for i in range(n_codes)]
    for code in codes:
        await _seed_stock(db_session, code)
    await db_session.flush()

    pool_rows = []
    seeded_quotes: set[tuple[str, date]] = set()
    for month_end in month_ends:
        base_d = month_end
        end_d = base_d + timedelta(days=30)
        for code in codes:
            z = {s: float(rng.standard_normal()) for s in _STRATEGIES}
            forward_ret = sum(_TRUE_BETAS[s] * z[s] for s in _STRATEGIES)
            noise = float(rng.standard_normal()) * 0.005
            base_close = 10.0
            end_close = base_close * (1.0 + forward_ret + noise)
            if (code, base_d) not in seeded_quotes:
                await _seed_quote(db_session, code, base_d, base_close)
                seeded_quotes.add((code, base_d))
            if (code, end_d) not in seeded_quotes:
                await _seed_quote(db_session, code, end_d, end_close)
                seeded_quotes.add((code, end_d))
            pool_rows.append({
                "ts_code": code,
                "trade_date": base_d,
                "composite_score": 75.0,
                "trend_score": 70.0,
                "momentum_score": 70.0,
                "reversion_score": 70.0,
                "value_score": 70.0,
                "market_state": "OSCILLATION",
                "in_pool": True,
                "is_holding": False,
                "composite_z": float(np.mean(list(z.values()))),
                "composite_pct_in_market": 0.5,
                "weights_source": "default_matrix",
                "hysteresis_status": "stable",
                "score_breakdown_raw": _build_breakdown_raw(z),
            })
    await repo.upsert_candidate_pool_bulk(pool_rows)
    await db_session.flush()


async def test_int_p14_8_01_backfill_6_months_writes_24_rows(
    db_session: AsyncSession,
) -> None:
    """6 month_end × 4 factor = 24 行 attribution_history。

    用合成日历（weekday）+ 间隔 ≥60 天的 month_end 模拟连续 6 个月；
    直接调 AttributionService.run_monthly（脚本主循环 _run_one_month 的核心
    操作），断言每个 month 写 4 行。
    """
    # 1. 构造合成 6 month_end：从 2025-11-28 倒推每 60 天一个（保证不冲突）
    last_me = date(2026, 4, 30)
    month_ends = [last_me - timedelta(days=60 * (5 - i)) for i in range(6)]

    # 跨 month_ends 全范围构造交易日历（含每个 base_d + end_d ≈ 30 天后）
    cal_start = month_ends[0] - timedelta(days=400)
    cal_end = month_ends[-1] + timedelta(days=60)
    weekdays = [
        cal_start + timedelta(days=i)
        for i in range((cal_end - cal_start).days + 1)
        if (cal_start + timedelta(days=i)).weekday() < 5
    ]
    calendar = TradingCalendar(weekdays)

    # 2. 种合成 panel（n_codes=50：保证最早 month_end 的 12 月 lookback 至少
    # 拿到 50 行 ≥ OLS 最小样本 40 = 4 factors × 10；30 codes 会让最早月只看
    # 到 1 个月 = 30 行 < 40 → run_monthly 返回空，回填脚本将该月跳过）
    repo = MarketDataRepository(db_session)
    await _seed_panel_for_backfill(db_session, repo, month_ends, n_codes=50)

    # 3. 循环跑 AttributionService.run_monthly（脚本主循环的核心操作）
    attr_repo = AttributionRepository()
    written_total = 0
    for me in month_ends:
        svc = AttributionService(db_session, attr_repo, calendar=calendar)
        written = await svc.run_monthly(me)
        # 每 month_end 写 4 行（4 strategy_z factor）
        assert len(written) == 4, (
            f"month_end={me} 期望 4 行，实际 {len(written)} 行"
        )
        written_total += len(written)

    assert written_total == 24

    # 4. 表内行数核对
    stmt = select(AttributionHistory).where(
        AttributionHistory.calc_date >= month_ends[0],
        AttributionHistory.calc_date <= month_ends[-1],
    )
    all_rows = list((await db_session.execute(stmt)).scalars().all())
    assert len(all_rows) == 24

    # 5. calc_date 唯一且覆盖 6 个月
    calc_dates = {row.calc_date for row in all_rows}
    assert calc_dates == set(month_ends)

    # 6. 每行字段健全
    for row in all_rows:
        assert row.factor in _STRATEGIES
        assert row.sample_size > 0
        assert row.window_days == 20
        assert abs(float(row.beta)) < 0.5  # OLS 估计 |β| 应远小于 0.5


async def test_int_p14_8_01b_enumerate_month_ends_skips_weekends(
    db_session: AsyncSession,
) -> None:
    """_enumerate_month_ends 必须返回严格交易日；月末是周末时回退到前一交易日。"""
    # 直接 import 脚本模块（脚本是单文件 + 标准 if __name__ 守卫）
    mod = import_module("scripts.backfill_attribution_history")

    # 构造一个 2026-02 月末是周六的场景：2026-02-28 = Saturday
    # weekdays 列表只含 weekday < 5 的日子 → 2026-02-27 (Fri) 是该月最后交易日
    cal_start = date(2026, 1, 1)
    cal_end = date(2026, 5, 31)
    weekdays = [
        cal_start + timedelta(days=i)
        for i in range((cal_end - cal_start).days + 1)
        if (cal_start + timedelta(days=i)).weekday() < 5
    ]
    calendar = TradingCalendar(weekdays)

    month_ends = mod._enumerate_month_ends(
        calendar,
        start_ym=date(2026, 2, 1),
        end_ym=date(2026, 4, 1),
    )

    # 2026-02-28 = Saturday → 应回退到 2026-02-27 (Friday)
    # 2026-03-31 = Tuesday → 取 2026-03-31
    # 2026-04-30 = Thursday → 取 2026-04-30
    assert date(2026, 2, 27) in month_ends
    assert date(2026, 3, 31) in month_ends
    assert date(2026, 4, 30) in month_ends
    assert len(month_ends) == 3
    # 所有 month_end 必须是工作日（weekday < 5）
    for me in month_ends:
        assert me.weekday() < 5, f"{me} 不是工作日"
