"""V1.5-C C0：日级 IC 产出闭环集成测试（INT-C0-01~04）。

依据 `docs/design/phases/v1_5_c_strategy_expansion.md` §2.3 DoD：
- INT-C0-01：真跑 `FactorMonitorService.produce_daily_ic` → `factor_ic_window_state`
  row_type='daily' 行按预期落库（**精确 == N 断言**），state 取因子值日 PIT 状态
- INT-C0-02：幂等——同一日重复产出不新增行（upsert 覆盖），且 ic_value 被刷新
- INT-C0-03：前向窗口未实现的日 → 返回 0 且**零行写入**（缺数据 ≠ IC=0）
- INT-C0-04：`plan_catchup_dates` 与 `get_existing_daily_ic_dates` 真实串联——
  已回填日被跳过、限流取最旧（ICIR 窗口要从断档最左端往右填）

与 Phase 14 §14-9 的 INT-P14-9-01 的区别：那里测的是**一次性回填脚本**的编排
helper（脚本 `_run_one_trade_date` 自建 `AsyncSessionLocal`，与 db_session 事务
回滚不兼容，只能逐段复现）；本文件测的是 **V1.5-C C0 的调度化生产者**——
`produce_daily_ic` 显式接收 session，因此可直接在 db_session 事务内真跑全流程。

集成测试在独立 DB 跑（DATABASE_URL 指向测试库 :5433）。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.factor_ic_repository import FactorICRepository, ICDailyRow
from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.engine.market_state import MarketStateEnum, MarketStateRecord
from quantpilot.models.business import FactorICWindowState
from quantpilot.services.factor_monitor_service import (
    FactorMonitorService,
    plan_catchup_dates,
)
from quantpilot.services.scoring_factory import build_default_scoring_service

_PREFIX = "VC0"
_INDEX = "000300.SH"
_STOCK_CODES = [f"{_PREFIX}{i:02d}.SZ" for i in range(1, 9)]  # 8 只
_N_DAYS = 180   # lookback(~130) + 因子值日 + 20 日前向窗口
_MIN_XS = 5     # 8 只合成股 → 放宽至 calc_ic 自带 5 地板（生产默认 30）
_LAG = 20       # SDD §7.4 前向窗口（交易日）


# ============================================================
# 合成数据（跳周末——交易日序列不含周末）
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
        "name": [f"C0测试股{i}" for i in range(len(_STOCK_CODES))],
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
            close = base * (1.0 + (i * 0.001) * j)
            rows.append({
                "ts_code": ts_code, "trade_date": td,
                "open": close * 0.995, "high": close * 1.01, "low": close * 0.99,
                "close": close, "pre_close": close / (1.0 + (i * 0.001)),
                "pct_chg": i * 0.1, "vol": 10_000_000,
                "amount": close * 10_000_000, "turnover_rate": 0.02,
                "float_mkt_cap": close * 1_000_000_000 * (i + 1),
                "adj_factor": 1.0, "is_suspended": False, "is_st": False,
                "limit_up": False, "limit_down": False,
            })
    return pd.DataFrame(rows)


def _financial_data_df(publish_before: date) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "ts_code": ts_code,
            "report_period": publish_before - timedelta(days=60),
            "publish_date": publish_before,
            "pe_ttm": 20.0 - i, "pb": 2.0 - i * 0.1,
            "roe": 0.10 + i * 0.01, "net_profit_yoy": 0.10 + i * 0.02,
            "revenue_yoy": 0.08 + i * 0.01, "dividend_yield": 0.02,
            "total_equity": 10_000_000_000.0, "debt_to_asset": 0.30,
        }
        for i, ts_code in enumerate(_STOCK_CODES)
    ])


def _index_history_df(all_days: list[date]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "index_code": _INDEX, "trade_date": td,
            "open": (3000.0 + i * 15) * 0.995, "high": (3000.0 + i * 15) * 1.01,
            "low": (3000.0 + i * 15) * 0.99, "close": 3000.0 + i * 15,
            "vol": 100_000_000, "pct_chg": 0.5,
        }
        for i, td in enumerate(all_days)
    ])


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


def _service(session: AsyncSession, calendar: TradingCalendar) -> FactorMonitorService:
    return FactorMonitorService(
        session, FactorMonitorEngine(), FactorICRepository(), calendar=calendar,
    )


async def _count_daily_rows(session: AsyncSession, td: date) -> int:
    stmt = select(func.count()).select_from(FactorICWindowState).where(
        FactorICWindowState.row_type == "daily",
        FactorICWindowState.trade_date == td,
        FactorICWindowState.strategy.in_(
            ("trend", "momentum", "mean_reversion", "value")
        ),
    )
    return int((await session.execute(stmt)).scalar_one())


# ============================================================
# INT-C0-01：真跑 produce_daily_ic → 精确行数 + PIT state
# ============================================================
async def test_int_c0_01_produce_daily_ic_writes_exact_rows_with_pit_state(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = MarketDataRepository(db_session)
    all_days = _trade_days(_N_DAYS, date(2025, 12, 15))
    calendar = TradingCalendar(all_days)
    await _setup_base(repo, all_days)

    factor_day = all_days[150]      # +20 前向 = index 170 < 180，窗口完整
    await repo.upsert_market_state(_make_state(all_days[140], MarketStateEnum.UPTREND))
    await repo.upsert_market_state(_make_state(factor_day, MarketStateEnum.DOWNTREND))
    await db_session.flush()

    svc = _service(db_session, calendar)
    scoring = build_default_scoring_service(db_session, calendar)

    with caplog.at_level(logging.INFO):
        n = await svc.produce_daily_ic(db_session, factor_day, scoring, min_xs=_MIN_XS)
    await db_session.flush()

    assert n > 0, "合成面板应产出至少一个策略的日级 IC"
    # 生产实证靠这行日志判定激活（同 A5b forecast_roe_override_applied 先例）
    assert "daily_ic_produced" in caplog.text
    # 精确断言（禁宽松上界）：返回值 == 实际落库行数
    assert await _count_daily_rows(db_session, factor_day) == n

    stmt = select(FactorICWindowState).where(
        FactorICWindowState.row_type == "daily",
        FactorICWindowState.trade_date == factor_day,
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    for r in rows:
        # state 取因子值日当日 PIT 状态（当日切 DOWNTREND，不得取更早的 UPTREND）
        assert r.state == "DOWNTREND", f"state 应为因子值日 PIT 状态，实际 {r.state}"
        assert r.ic_value is not None
        assert r.sample_size >= _MIN_XS
        assert r.strategy == r.factor  # V1.0 简化：strategy 级 IC（factor=strategy）


# ============================================================
# INT-C0-02：幂等（重复产出不新增行）
# ============================================================
async def test_int_c0_02_produce_daily_ic_is_idempotent(
    db_session: AsyncSession,
) -> None:
    repo = MarketDataRepository(db_session)
    all_days = _trade_days(_N_DAYS, date(2025, 12, 15))
    calendar = TradingCalendar(all_days)
    await _setup_base(repo, all_days)

    factor_day = all_days[150]
    await repo.upsert_market_state(_make_state(all_days[140], MarketStateEnum.UPTREND))
    await db_session.flush()

    svc = _service(db_session, calendar)
    scoring = build_default_scoring_service(db_session, calendar)

    n1 = await svc.produce_daily_ic(db_session, factor_day, scoring, min_xs=_MIN_XS)
    await db_session.flush()
    count1 = await _count_daily_rows(db_session, factor_day)

    n2 = await svc.produce_daily_ic(db_session, factor_day, scoring, min_xs=_MIN_XS)
    await db_session.flush()
    count2 = await _count_daily_rows(db_session, factor_day)

    assert n1 == n2
    assert count1 == count2 == n1, "重跑同一日必须 upsert 覆盖，不得新增重复行"


# ============================================================
# INT-C0-03：前向窗口未实现 → 0 行（缺数据 ≠ IC=0）
# ============================================================
async def test_int_c0_03_incomplete_forward_window_writes_nothing(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = MarketDataRepository(db_session)
    all_days = _trade_days(_N_DAYS, date(2025, 12, 15))
    calendar = TradingCalendar(all_days)
    await _setup_base(repo, all_days)
    await repo.upsert_market_state(_make_state(all_days[140], MarketStateEnum.UPTREND))
    await db_session.flush()

    # 倒数第 5 个交易日：d+20 超出已有行情 → 前向收益未实现
    factor_day = all_days[-5]
    svc = _service(db_session, calendar)
    scoring = build_default_scoring_service(db_session, calendar)

    with caplog.at_level(logging.INFO):
        n = await svc.produce_daily_ic(db_session, factor_day, scoring, min_xs=_MIN_XS)
    await db_session.flush()

    assert n == 0
    assert await _count_daily_rows(db_session, factor_day) == 0, (
        "前向窗口未实现时不得写占位行——缺数据不等于 IC=0"
    )
    # 钉死早退**原因**：否则本测试会在"空 universe""无 IC point"等其它 0 值
    # 路径上假通过，起不到守护作用
    assert "daily_ic_forward_window_incomplete" in caplog.text
    assert "daily_ic_produced" not in caplog.text


# ============================================================
# INT-C0-04：追平计划与 DB 已有日真实串联
# ============================================================
async def test_int_c0_04_catchup_plan_skips_existing_and_takes_oldest(
    db_session: AsyncSession,
) -> None:
    all_days = _trade_days(60, date(2025, 12, 15))
    ic_repo = FactorICRepository()

    # 已回填：第 0、1 天（其余为断档）
    already = [all_days[0], all_days[1]]
    await ic_repo.upsert_ic_daily(db_session, [
        ICDailyRow(strategy="trend", factor="trend", state="UPTREND",
                   trade_date=d, ic_value=0.02, sample_size=200)
        for d in already
    ])
    await db_session.flush()

    last_eligible = all_days[40]
    existing = await ic_repo.get_existing_daily_ic_dates(
        db_session, all_days[0], last_eligible,
    )
    assert existing == set(already), "get_existing_daily_ic_dates 应只认 daily 行"

    planned = plan_catchup_dates(
        trade_dates=all_days, existing=existing,
        last_eligible=last_eligible, max_days=3,
    )

    # 跳过已有日 → 从断档最左端 all_days[2] 起，取最旧 3 天
    assert planned == [all_days[2], all_days[3], all_days[4]]
    assert all(d <= last_eligible for d in planned)


async def test_int_c0_04b_catchup_plan_ignores_non_daily_rows(
    db_session: AsyncSession,
) -> None:
    """aggregate 行不得让追平误判该日"已回填"（daily 与 aggregate 共表）。"""
    all_days = _trade_days(60, date(2025, 12, 15))
    ic_repo = FactorICRepository()

    from quantpilot.data.factor_ic_repository import ICAggregateRow

    await ic_repo.upsert_ic_aggregate(db_session, [
        ICAggregateRow(
            strategy="trend", factor="trend", state="UPTREND",
            trade_date=all_days[0], ic_mean_state=0.03, ic_std_state=0.02,
            icir=1.5, sample_size=200, ic_ci_low=0.01, ic_ci_high=0.05,
            t_stat=2.0, half_life=None,
        )
    ])
    await db_session.flush()

    existing = await ic_repo.get_existing_daily_ic_dates(
        db_session, all_days[0], all_days[40],
    )
    assert all_days[0] not in existing, "aggregate 行不应被计入已回填 daily 日"

    planned = plan_catchup_dates(
        trade_dates=all_days, existing=existing,
        last_eligible=all_days[40], max_days=2,
    )
    assert planned == [all_days[0], all_days[1]]
