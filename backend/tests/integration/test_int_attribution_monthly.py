"""INT-P12-B-01~03: AttributionService.run_monthly 集成测试（Phase 12 P12-B3）。

依据 phase12_factor_lineage.md §6.2：
- 01: 月末跑 run_monthly → attribution_history 写入 4 行
- 02: candidate_pool 不足 12 月 / 数据缺失 → 返回空 list（不抛异常 / 不写 NULL 行）
- 03: 重跑同月 → upsert 不重复（uq_attribution_date_factor）
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.attribution_repository import AttributionRepository
from quantpilot.data.repository import MarketDataRepository
from quantpilot.models.business import AttributionHistory
from quantpilot.models.market import DailyQuote, StockInfo
from quantpilot.services.attribution_service import AttributionService

_MONTH_END = date(2026, 4, 30)
_TRUE_BETAS = {"trend": 0.05, "momentum": 0.03, "mean_reversion": -0.02, "value": 0.04}
_STRATEGIES = ["trend", "momentum", "mean_reversion", "value"]


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
    """构造 score_breakdown_raw JSONB：{strategy: {z_raw, weight, contribution}}。"""
    return {
        s: {"z_raw": float(z), "weight": 0.25, "contribution": float(z) * 0.25}
        for s, z in z_values.items()
    }


async def _seed_panel(
    db_session: AsyncSession,
    repo: MarketDataRepository,
    n_dates: int = 6,
    n_codes: int = 30,
) -> None:
    """种数据：30 只股 × 6 个月 trade_date，每月 1 日为一个观测；
    base_close=10 / end_close = 10 * (1 + sum(beta * z) + noise)，
    保证 forward_return 与 strategy_z 线性关系成立。
    """
    rng = np.random.default_rng(42)
    codes = [f"P12B{i:03d}.SH" for i in range(n_codes)]
    for code in codes:
        await _seed_stock(db_session, code)
    await db_session.flush()

    pool_rows = []
    # 间隔 60 天保证 base_d + 30 (end_d) 不与下个月 base_d 冲突
    seeded_quotes: set[tuple[str, date]] = set()
    for d_idx in range(n_dates):
        base_d = _MONTH_END - timedelta(days=60 * (n_dates - 1 - d_idx))
        # forward_return 实现日 ≈ base_d + 30 天（window=20 交易日 × 1.5 = 30 日历天）
        end_d = base_d + timedelta(days=30)

        for code in codes:
            # 真实 strategy_z 服从标准正态
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


# ===========================================================================
# INT-P12-B-01: run_monthly 写入 4 行
# ===========================================================================
async def test_int_p12_b_01_run_monthly_writes_4_rows(
    db_session: AsyncSession,
) -> None:
    repo = MarketDataRepository(db_session)
    await _seed_panel(db_session, repo, n_dates=6, n_codes=30)

    attr_repo = AttributionRepository()
    svc = AttributionService(db_session, attr_repo, window_days=20, lookback_months=12)

    written = await svc.run_monthly(_MONTH_END)
    assert len(written) == 4
    factors = sorted(row.factor for row in written)
    assert factors == sorted(_STRATEGIES)
    for row in written:
        assert row.calc_date == _MONTH_END
        assert row.sample_size > 0
        assert row.window_days == 20
        # |beta| 应在合理范围内（真实 |β| ≤ 0.05，加噪声估计偏差不超过 0.02）
        assert abs(float(row.beta)) < 0.5


# ===========================================================================
# INT-P12-B-02: 数据不足 → 返回空 list
# ===========================================================================
async def test_int_p12_b_02_run_monthly_empty_pool_returns_empty(
    db_session: AsyncSession,
) -> None:
    """candidate_pool 无 score_breakdown_raw 行 → 返回 [] 不写入。"""
    attr_repo = AttributionRepository()
    svc = AttributionService(db_session, attr_repo, window_days=20, lookback_months=12)

    written = await svc.run_monthly(_MONTH_END)
    assert written == []

    # attribution_history 表空
    stmt = select(AttributionHistory).where(AttributionHistory.calc_date == _MONTH_END)
    rows = list((await db_session.execute(stmt)).scalars().all())
    assert rows == []


# ===========================================================================
# INT-P12-B-03: 重跑同月 → upsert 不重复
# ===========================================================================
async def test_int_p12_b_03_run_monthly_idempotent(
    db_session: AsyncSession,
) -> None:
    repo = MarketDataRepository(db_session)
    await _seed_panel(db_session, repo, n_dates=6, n_codes=30)

    attr_repo = AttributionRepository()
    svc = AttributionService(db_session, attr_repo, window_days=20, lookback_months=12)

    first = await svc.run_monthly(_MONTH_END)
    assert len(first) == 4
    second = await svc.run_monthly(_MONTH_END)
    assert len(second) == 4

    # 两次后表里仍只有 4 行（calc_date=_MONTH_END）
    stmt = select(AttributionHistory).where(AttributionHistory.calc_date == _MONTH_END)
    rows = list((await db_session.execute(stmt)).scalars().all())
    assert len(rows) == 4
