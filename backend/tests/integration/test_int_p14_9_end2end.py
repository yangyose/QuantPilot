"""Phase 14 §14-9：INT-P14-9-01 日级 IC 生产者端到端集成测试。

依据 docs/design/phases/phase14_account_integrity.md §11.3 / §11.4：

- INT-P14-9-01a：合成 **daily_quote / financial_data / market_state_history**
  （跳周末；**非 candidate_pool**——backfill 经 `score_universe` 读原始数据不读 pool，S-03）
  小窗口 → 真跑 `score_universe_for_date` + `compute_forward_returns`（后复权 adj_close +
  严格 t=d+20 交易日）+ `compute_daily_ic` → `factor_ic_window_state` daily 行写入，
  且 **state 标签 = 因子值日 d 的真实 market_state**（PIT；§11.2 数据契约）。
- INT-P14-9-01b：日级 IC 行（含真实产出的若干行 + 补足窗口的 seed）串联
  `apply_monthly_rebalance` → dominant state（UPTREND）出 `weights_source='icir'`；
  稀疏 state（DOWNTREND，样本 < 60）仍 `default_matrix`（SDD §7.4 合规降级）。

脚本 `_run_one_trade_date` 内部新建 `AsyncSessionLocal`，与 db_session 事务回滚 fixture
不兼容（参 INT-P14-2-01 同款说明）——本测试直接调脚本的纯函数 / 编排 helper
（`_build_scoring_service` / `_extract_strategy_z` / `_excluded_codes`）+ engine 纯函数
在 db_session 内复现 per-day 逻辑；脚本的 per-day 循环仅是 session 管理 wrapper。

集成测试在独立 DB 跑（DATABASE_URL 指向 quantpilot-test-db @ port 5433）。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.factor_ic_repository import FactorICRepository, ICDailyRow
from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.diagnostics.ic_aggregator import (
    compute_daily_ic,
    compute_forward_returns,
)
from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.engine.market_state import MarketStateEnum, MarketStateRecord
from quantpilot.models.business import FactorICWindowState, StrategyWeightsHistory
from quantpilot.services.factor_monitor_service import FactorMonitorService

# 让 backfill_daily_ic 脚本可 import（复用其编排 helper）
_BACKEND_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_BACKEND_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SCRIPTS))

from backfill_daily_ic import (  # noqa: E402
    _FORWARD_WINDOW,
    _build_scoring_service,
    _excluded_codes,
    _extract_strategy_z,
)

_PREFIX = "P149"
_INDEX = "000300.SH"
_STOCK_CODES = [f"{_PREFIX}{i:02d}.SZ" for i in range(1, 9)]  # 8 只
_N_DAYS = 180  # 足够 lookback（~130）+ 3 因子值日 + 20 前向窗口
_MIN_XS = 5    # 8 只合成股 → 放宽至 calc_ic 自带 5 地板（生产默认 30）


# ============================================================
# 合成数据 fixture（仿 test_int_p11_scoring_e2e._setup_base，窗口拉长）
# ============================================================

def _trade_days(n: int, end: date) -> list[date]:
    days: list[date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _stock_info_df() -> pd.DataFrame:
    industries = ["计算机", "计算机", "金融", "金融", "消费", "消费", "医药", "医药"]
    return pd.DataFrame({
        "ts_code": _STOCK_CODES,
        "name": [f"测试股{i}" for i in range(len(_STOCK_CODES))],
        "market": ["MAIN"] * len(_STOCK_CODES),
        "sw_industry_l1": industries,
        "sw_industry_l2": ["软件"] * len(_STOCK_CODES),
        "list_date": [date(2023, 1, 2)] * len(_STOCK_CODES),
        "delist_date": [None] * len(_STOCK_CODES),
        "is_active": [True] * len(_STOCK_CODES),
    })


def _daily_quotes_df(all_days: list[date]) -> pd.DataFrame:
    rows = []
    for i, ts_code in enumerate(_STOCK_CODES):
        base = 10.0 + i * 2
        for j, td in enumerate(all_days):
            growth = 1.0 + (i * 0.001) * j
            close = base * growth
            rows.append({
                "ts_code": ts_code,
                "trade_date": td,
                "open": close * 0.995,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "pre_close": close / (1.0 + (i * 0.001)),
                "pct_chg": i * 0.1,
                "vol": 10_000_000,
                "amount": close * 10_000_000,
                "turnover_rate": 0.02,
                "float_mkt_cap": close * 1_000_000_000 * (i + 1),
                "adj_factor": 1.0,
                "is_suspended": False,
                "is_st": False,
                "limit_up": False,
                "limit_down": False,
            })
    return pd.DataFrame(rows)


def _financial_data_df(publish_before: date) -> pd.DataFrame:
    rows = []
    for i, ts_code in enumerate(_STOCK_CODES):
        rows.append({
            "ts_code": ts_code,
            "report_period": publish_before - timedelta(days=60),
            "publish_date": publish_before,
            "pe_ttm": 20.0 - i,
            "pb": 2.0 - i * 0.1,
            "roe": 0.10 + i * 0.01,
            "net_profit_yoy": 0.10 + i * 0.02,
            "revenue_yoy": 0.08 + i * 0.01,
            "dividend_yield": 0.02,
            "total_equity": 10_000_000_000.0,
            "debt_to_asset": 0.30,
        })
    return pd.DataFrame(rows)


def _index_history_df(all_days: list[date]) -> pd.DataFrame:
    rows = []
    for i, td in enumerate(all_days):
        close = 3000.0 + i * 15
        rows.append({
            "index_code": _INDEX, "trade_date": td,
            "open": close * 0.995, "high": close * 1.01, "low": close * 0.99,
            "close": close, "vol": 100_000_000, "pct_chg": 0.5,
        })
    return pd.DataFrame(rows)


async def _setup_base(repo: MarketDataRepository, all_days: list[date]) -> None:
    await repo.upsert_stock_list(_stock_info_df())
    await repo.upsert_daily_quotes(_daily_quotes_df(all_days))
    await repo.upsert_financial_data(_financial_data_df(all_days[40]))
    await repo.upsert_index_history(_index_history_df(all_days))


def _make_state(td: date, state: MarketStateEnum) -> MarketStateRecord:
    return MarketStateRecord(
        trade_date=td, market_state=state,
        trend_strength=30.0, adx_value=30.0, ma20=4000.0, ma60=3800.0,
        state_changed=False, description=f"合成 {state.value}",
    )


# ============================================================
# INT-P14-9-01a：真实产出 daily 行 + PIT state 标签
# ============================================================
async def test_int_p14_9_01a_real_producer_writes_daily_rows_with_pit_state(
    db_session: AsyncSession,
) -> None:
    """真跑 score_universe_for_date → compute_daily_ic → upsert_ic_daily：
    factor_ic_window_state 写 row_type='daily' 行，trade_date=d、state=market_state[d]（PIT）。
    """
    repo = MarketDataRepository(db_session)
    end = date(2025, 12, 15)
    all_days = _trade_days(_N_DAYS, end)
    calendar = TradingCalendar(all_days)
    await _setup_base(repo, all_days)

    # 因子值日：index 150/151/152（150 日 lookback + 20 日前向窗口 ≤ index 172 < 180）
    factor_days = [all_days[150], all_days[151], all_days[152]]
    # state：早期 UPTREND（≤ d0/d1 → carry-forward），factor_days[2] 当日切 DOWNTREND
    await repo.upsert_market_state(_make_state(all_days[140], MarketStateEnum.UPTREND))
    await repo.upsert_market_state(_make_state(factor_days[2], MarketStateEnum.DOWNTREND))
    await db_session.flush()

    expected_state = {
        factor_days[0]: "UPTREND",
        factor_days[1]: "UPTREND",
        factor_days[2]: "DOWNTREND",
    }

    svc = _build_scoring_service(db_session, calendar)
    ic_repo = FactorICRepository()

    for d in factor_days:
        composites = await svc.score_universe_for_date(d)
        assert composites, f"score_universe_for_date({d}) 应产出全 universe composites"

        strategy_z = _extract_strategy_z(composites)
        assert strategy_z, f"{d} 应抽到至少一个策略 z_raw Series"

        state_record = await repo.get_latest_market_state(before_date=d + timedelta(days=1))
        state = state_record.market_state
        assert state == expected_state[d], (
            f"{d} PIT state 期望 {expected_state[d]}，实际 {state}"
        )

        t = calendar.get_next_trade_date(d, _FORWARD_WINDOW)
        ts_codes = [str(c.ts_code) for c in composites]
        adj = await repo.get_adj_prices_bulk(ts_codes, d, t)
        excluded = await _excluded_codes(db_session, ts_codes, d, t)
        fwd = compute_forward_returns(adj, d, t, excluded=excluded)

        points = compute_daily_ic(strategy_z, fwd, min_xs=_MIN_XS)
        assert points, f"{d} 应产出至少一个 DailyICPoint（8 股 ≥ min_xs={_MIN_XS}）"

        rows = [
            ICDailyRow(strategy=p.strategy, factor=p.strategy, state=state,
                       trade_date=d, ic_value=p.ic_value, sample_size=p.sample_size)
            for p in points
        ]
        await ic_repo.upsert_ic_daily(db_session, rows)
    await db_session.flush()

    # 断言：每个因子值日写入 daily 行，且 state 标签 = 因子值日 PIT state
    for d in factor_days:
        stmt = select(FactorICWindowState).where(
            FactorICWindowState.row_type == "daily",
            FactorICWindowState.trade_date == d,
        )
        day_rows = (await db_session.execute(stmt)).scalars().all()
        assert day_rows, f"{d} 应有 row_type='daily' 行"
        for r in day_rows:
            assert r.state == expected_state[d], (
                f"{d} daily 行 state 应为 {expected_state[d]}（PIT），实际 {r.state}"
            )
            assert r.ic_value is not None
            assert r.sample_size >= _MIN_XS


# ============================================================
# INT-P14-9-01b：日级 IC 行串联 apply_monthly_rebalance → icir
# ============================================================
async def _seed_uptrend_window(
    session: AsyncSession, month_end: date, n_days: int = 70,
) -> None:
    """在 [month_end-89d, month_end-20d] 区间 seed n_days 个 UPTREND daily 行/策略，
    令 rolling 窗口 [t-272d, t-20d] 内 UPTREND 样本 ≥ 60（_STATE_MIN_SAMPLES）。

    DOWNTREND / OSCILLATION 不 seed → 仍 < 60 → default_matrix（稀疏 state 合规降级）。
    """
    repo = FactorICRepository()
    rng = np.random.default_rng(7)
    means = {"trend": 0.06, "momentum": 0.04, "mean_reversion": 0.03, "value": 0.02}
    rows: list[ICDailyRow] = []
    for strategy in ("trend", "momentum", "mean_reversion", "value"):
        for i in range(n_days):
            td = month_end - timedelta(days=20 + i)
            ic = float(rng.normal(means[strategy], 0.02))
            rows.append(ICDailyRow(
                strategy=strategy, factor=strategy, state="UPTREND",
                trade_date=td, ic_value=ic, sample_size=200,
            ))
    BATCH = 2000
    for i in range(0, len(rows), BATCH):
        await repo.upsert_ic_daily(session, rows[i:i + BATCH])
    await session.flush()


async def test_int_p14_9_01b_daily_ic_chains_to_icir_rebalance(
    db_session: AsyncSession,
) -> None:
    """UPTREND daily 行 ≥ 60 → apply_monthly_rebalance 出 weights_source='icir'；
    DOWNTREND 稀疏（无 seed）→ default_matrix。"""
    month_end = date(2025, 11, 28)  # 月末交易日（11/30 是周日）

    await _seed_uptrend_window(db_session, month_end)

    # calendar=None → 日历日回退窗口（与 INT-P14-2-01 一致；strict 窗口由 INT-P14-5-01 覆盖）
    service = FactorMonitorService(session=db_session, engine=FactorMonitorEngine())
    result = await service.apply_monthly_rebalance(db_session, month_end)
    await db_session.flush()
    assert len(result["UPTREND"]) == 4

    effective_date = month_end + timedelta(days=1)
    rows = (await db_session.execute(
        select(StrategyWeightsHistory).where(
            StrategyWeightsHistory.trade_date == effective_date,
        )
    )).scalars().all()
    by_state: dict[str, list[StrategyWeightsHistory]] = {}
    for r in rows:
        by_state.setdefault(r.state, []).append(r)

    # dominant state UPTREND：≥60 样本 → icir
    up = by_state.get("UPTREND", [])
    assert len(up) == 4, f"UPTREND 应有 4 策略权重行，实际 {len(up)}"
    assert all(r.weights_source == "icir" for r in up), (
        f"UPTREND 应为 icir，实际 {[r.weights_source for r in up]}"
    )

    # 稀疏 state DOWNTREND：无 seed < 60 → default_matrix（SDD §7.4 合规降级）
    down = by_state.get("DOWNTREND", [])
    assert len(down) == 4
    assert all(r.weights_source == "default_matrix" for r in down), (
        f"DOWNTREND 稀疏应为 default_matrix，实际 {[r.weights_source for r in down]}"
    )

    # ICIR 行确实读到 ≥60 daily 样本（避免回归到冷启动而误判）
    agg = (await db_session.execute(
        select(func.count()).select_from(FactorICWindowState).where(
            FactorICWindowState.row_type == "aggregate",
            FactorICWindowState.trade_date == month_end,
            FactorICWindowState.state == "UPTREND",
        )
    )).scalar() or 0
    assert agg == 4, f"UPTREND 应写 4 行 aggregate（4 策略），实际 {agg}"
