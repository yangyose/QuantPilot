"""INT-FINDEDUP-01~03：get_latest_n_financials 按 report_period 去重（生产事故 2026-07 修复）。

事故根因：每日快照 `fetch_financial_data` 对未披露报告期（如 Q2 2026-06-30，7 月才写、
8 月才真披露）每天写一条 publish_date=当日 的全 NULL 财务行；UNIQUE 含 publish_date →
每天新增一行而非更新 → 同一 (ts_code, report_period) 堆积多条重复 NULL 行。

旧 get_latest_n_financials 按 `ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY
report_period DESC)` 取前 n 行——重复的同期 NULL 行占满 n=2 窗口，把真实的上一期数据挤出，
导致 UniverseFilter F-5（连续亏损过滤）失效、候选池近翻倍、2GB 生产机评分卡死 12 个交易日。

修复：先按 (ts_code, report_period) 去重（保留 publish_date 最新一行），再对不同报告期
排序取前 n。这些每日重复行携带合法的当日 pe/pb（get_pe_pb_history_bulk 依赖），不能删除，
故修在消费端。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.repository import MarketDataRepository
from quantpilot.models.market import FinancialData


async def _add_fin(
    session: AsyncSession,
    ts_code: str,
    report_period: date,
    publish_date: date,
    *,
    net_profit_yoy: float | None,
    debt_to_asset: float | None = None,
) -> None:
    session.add(FinancialData(
        ts_code=ts_code,
        report_period=report_period,
        publish_date=publish_date,
        net_profit_yoy=net_profit_yoy,
        debt_to_asset=debt_to_asset,
    ))
    await session.flush()


async def test_findedup_01_duplicate_unpublished_period_not_evicting_real(
    db_session: AsyncSession,
) -> None:
    """INT-FINDEDUP-01：未披露报告期的多条重复 NULL 行不应挤出真实上一期。

    复现事故：Q1(2026-03-31) 有真实 net_profit_yoy=-0.2（亏损）；Q2(2026-06-30) 未披露，
    从 07-01 起每天写一条全 NULL 行。n=2 应返回 {Q2:NULL, Q1:-0.2}，而非 {Q2:NULL, Q2:NULL}。
    """
    repo = MarketDataRepository(db_session)
    ts = "600001.SH"
    # Q1 真实数据（4 月披露）
    await _add_fin(db_session, ts, date(2026, 3, 31), date(2026, 4, 20), net_profit_yoy=-0.2)
    # Q2 未披露占位：07-01 ~ 07-20 每天一条全 NULL（publish_date 递增 → 每天新行）
    for day in range(1, 21):
        await _add_fin(
            db_session, ts, date(2026, 6, 30), date(2026, 7, day), net_profit_yoy=None
        )

    df = await repo.get_latest_n_financials([ts], as_of_date=date(2026, 7, 20), n=2)

    # 恰好两个不同报告期
    periods = sorted({rp for (_, rp) in df.index})
    assert periods == [date(2026, 3, 31), date(2026, 6, 30)], periods
    # 每个报告期只保留一行（去重后）
    assert len(df) == 2, df
    # 真实 Q1 数据仍在窗口内（未被重复 NULL 挤出）
    q1_yoy = df.loc[(ts, date(2026, 3, 31)), "net_profit_yoy"]
    assert float(q1_yoy) == -0.2


async def test_findedup_02_keeps_latest_publish_per_period(
    db_session: AsyncSession,
) -> None:
    """INT-FINDEDUP-02：同一报告期多条 publish_date 时保留最新披露的一行。

    Q1 先写占位 NULL（04-01），后真披露带值（04-20）。去重应保留 04-20 的真实值。
    """
    repo = MarketDataRepository(db_session)
    ts = "600002.SH"
    await _add_fin(db_session, ts, date(2026, 3, 31), date(2026, 4, 1), net_profit_yoy=None)
    await _add_fin(db_session, ts, date(2026, 3, 31), date(2026, 4, 20), net_profit_yoy=0.15)

    df = await repo.get_latest_n_financials([ts], as_of_date=date(2026, 7, 20), n=2)

    assert len(df) == 1, df
    yoy = df.loc[(ts, date(2026, 3, 31)), "net_profit_yoy"]
    assert float(yoy) == 0.15


async def test_findedup_03_pit_cutoff_respected(db_session: AsyncSession) -> None:
    """INT-FINDEDUP-03：as_of_date 之后披露的行不进入结果（PIT）。"""
    repo = MarketDataRepository(db_session)
    ts = "600003.SH"
    await _add_fin(db_session, ts, date(2026, 3, 31), date(2026, 4, 20), net_profit_yoy=0.1)
    # 未来披露（08-30）——as_of 07-20 时不可见
    await _add_fin(db_session, ts, date(2026, 6, 30), date(2026, 8, 30), net_profit_yoy=0.3)

    df = await repo.get_latest_n_financials([ts], as_of_date=date(2026, 7, 20), n=2)

    periods = {rp for (_, rp) in df.index}
    assert periods == {date(2026, 3, 31)}, periods
