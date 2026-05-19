"""Phase 11 §3.4 ScoringService.score_universe + write_candidate_pool 集成测试。

覆盖（INT-P11-SC-01 ~ INT-P11-SC-04）：
- 01: score_universe 冷启动（factor_monitor=None）→ default_matrix 路径
- 02: score_universe 注入 FactorMonitorService → ICIR 加权路径
- 03: write_candidate_pool 写入 candidate_pool 新 6 列（composite_z /
  composite_pct_in_market / weights_source / hysteresis_status /
  score_breakdown_raw / score_breakdown_residual）
- 04: market_cap PIT 切片 + industry 加载 + Step 2 中性化端到端贯通

集成测试在独立 DB 跑（DATABASE_URL 指向 quantpilot-test-db @ port 5433）。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from quantpilot.data.calendar import TradingCalendar
from quantpilot.data.factor_ic_repository import (
    FactorICRepository,
    StrategyWeightsRow,
)
from quantpilot.data.repository import MarketDataRepository
from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.engine.market_state import MarketStateEnum, MarketStateRecord
from quantpilot.engine.pool import CandidatePoolManager
from quantpilot.engine.scorer import Scorer
from quantpilot.engine.strategies.mean_reversion import MeanReversionStrategy
from quantpilot.engine.strategies.momentum import MomentumStrategy
from quantpilot.engine.strategies.trend import TrendStrategy
from quantpilot.engine.strategies.value import ValueStrategy
from quantpilot.engine.universe import UniverseFilter
from quantpilot.services.factor_monitor_service import FactorMonitorService
from quantpilot.services.strategy_service import ScoringService

# Phase 11 集成测试用前缀，避免与其它集成测试串扰
_PREFIX = "P11SC"
_INDEX = "000300.SH"
_TRADE_DATE = date(2025, 12, 31)
_STOCK_CODES = [f"{_PREFIX}{i:02d}.SZ" for i in range(1, 9)]  # 8 只


# ============================================================
# 数据 fixture
# ============================================================

def _make_calendar() -> TradingCalendar:
    dates: list[date] = []
    d = date(2025, 1, 2)
    while d <= date(2026, 1, 2):
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return TradingCalendar(dates)


def _make_trade_days(n: int, end: date = _TRADE_DATE) -> list[date]:
    days: list[date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _stock_info_df() -> pd.DataFrame:
    # 多行业，避免行业 dummy 退化（drop_first=True 后至少 1 个有效 dummy）
    industries = ["计算机", "计算机", "金融", "金融", "消费", "消费", "医药", "医药"]
    return pd.DataFrame({
        "ts_code": _STOCK_CODES,
        "name": [f"测试股{i}" for i in range(len(_STOCK_CODES))],
        "market": ["MAIN"] * len(_STOCK_CODES),
        "sw_industry_l1": industries,
        "sw_industry_l2": ["软件"] * len(_STOCK_CODES),
        "list_date": [date(2024, 1, 2)] * len(_STOCK_CODES),
        "delist_date": [None] * len(_STOCK_CODES),
        "is_active": [True] * len(_STOCK_CODES),
    })


def _daily_quotes_df(n_days: int = 130) -> pd.DataFrame:
    trade_days = _make_trade_days(n_days)
    rows = []
    for i, ts_code in enumerate(_STOCK_CODES):
        base = 10.0 + i * 2
        for j, td in enumerate(trade_days):
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
                "float_mkt_cap": close * 1_000_000_000 * (i + 1),  # 市值差异化
                "adj_factor": 1.0,
                "is_suspended": False,
                "is_st": False,
                "limit_up": False,
                "limit_down": False,
            })
    return pd.DataFrame(rows)


def _financial_data_df() -> pd.DataFrame:
    rows = []
    for i, ts_code in enumerate(_STOCK_CODES):
        rows.append({
            "ts_code": ts_code,
            "report_period": date(2025, 9, 30),
            "publish_date": date(2025, 11, 1),
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


def _index_history_df(n_days: int = 130) -> pd.DataFrame:
    trade_days = _make_trade_days(n_days)
    rows = []
    for i, td in enumerate(trade_days):
        close = 3000.0 + i * 15
        rows.append({
            "index_code": _INDEX, "trade_date": td,
            "open": close * 0.995, "high": close * 1.01, "low": close * 0.99,
            "close": close, "vol": 100_000_000, "pct_chg": 0.5,
        })
    return pd.DataFrame(rows)


async def _setup_base(repo: MarketDataRepository) -> None:
    await repo.upsert_stock_list(_stock_info_df())
    await repo.upsert_daily_quotes(_daily_quotes_df())
    await repo.upsert_financial_data(_financial_data_df())
    await repo.upsert_index_history(_index_history_df())
    await repo.upsert_market_state(MarketStateRecord(
        trade_date=_TRADE_DATE,
        market_state=MarketStateEnum.UPTREND,
        trend_strength=30.0,
        adx_value=30.0,
        ma20=4000.0,
        ma60=3800.0,
        state_changed=False,
        description="合成 UPTREND",
    ))


def _make_service(
    session: AsyncSession,
    factor_monitor: FactorMonitorService | None = None,
    pool_capacity: int = 3,
) -> ScoringService:
    repo = MarketDataRepository(session)
    return ScoringService(
        repo=repo,
        universe_filter=UniverseFilter(),
        strategies=[
            TrendStrategy(), MomentumStrategy(), MeanReversionStrategy(), ValueStrategy(),
        ],
        scorer=Scorer(),
        pool_manager=CandidatePoolManager(pool_capacity=pool_capacity),
        calendar=_make_calendar(),
        factor_monitor=factor_monitor,
    )


# ============================================================
# INT-P11-SC-01：冷启动 — factor_monitor=None → default_matrix
# ============================================================
@pytest.mark.anyio
async def test_int_p11_sc_01_cold_start_default_matrix(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    await _setup_base(repo)
    svc = _make_service(db_session, factor_monitor=None)

    composites = await svc.score_universe(
        db_session, _TRADE_DATE, _STOCK_CODES, MarketStateEnum.UPTREND,
    )
    assert len(composites) > 0, "5 步管线应产出 CompositeScore"
    for c in composites:
        # weights_source 标记为 default_matrix（冷启动）
        assert c.weights_source == "default_matrix"
        assert c.hysteresis_status == "stable"
        # Phase 11 三层输出全部填充
        assert c.composite_z is not None
        assert c.composite_pct_in_market is not None and 0 <= c.composite_pct_in_market <= 1
        assert 0 <= c.composite_score <= 100
        # breakdown 结构齐备
        assert isinstance(c.score_breakdown_raw, dict)
        assert isinstance(c.score_breakdown_residual, dict)


# ============================================================
# INT-P11-SC-02：ICIR 加权路径 — 注入 FactorMonitorService（含 strategy_weights_history）
# ============================================================
@pytest.mark.anyio
async def test_int_p11_sc_02_icir_weighted_path(db_session: AsyncSession) -> None:
    repo = MarketDataRepository(db_session)
    await _setup_base(repo)

    # 写入 strategy_weights_history（UPTREND 的 4 策略权重；source=icir）
    ic_repo = FactorICRepository()
    weights_rows = [
        StrategyWeightsRow(
            state="UPTREND",
            strategy=s,
            trade_date=_TRADE_DATE - timedelta(days=10),
            weight_used=w,
            weights_source="icir",
            icir_inputs={"mock": True},
            hysteresis_status="stable",
        )
        for s, w in zip(
            ["trend", "momentum", "mean_reversion", "value"],
            [0.40, 0.25, 0.15, 0.20],
        )
    ]
    await ic_repo.upsert_strategy_weights(db_session, weights_rows)
    await db_session.flush()

    engine = FactorMonitorEngine()
    fm = FactorMonitorService(session=None, engine=engine, repo=ic_repo)
    svc = _make_service(db_session, factor_monitor=fm)

    composites = await svc.score_universe(
        db_session, _TRADE_DATE, _STOCK_CODES, MarketStateEnum.UPTREND,
    )
    assert len(composites) > 0
    # 注入的权重应被读到（weights_source=icir）
    for c in composites:
        assert c.weights_source == "icir"
        # 权重和 = 1（active strategy 之和归一化后）
        if c.score_breakdown_raw:
            total = sum(bd["weight"] for bd in c.score_breakdown_raw.values())
            assert abs(total - 1.0) < 1e-6


# ============================================================
# INT-P11-SC-03：write_candidate_pool 写入 Phase 11 新 6 列
# ============================================================
@pytest.mark.anyio
async def test_int_p11_sc_03_write_candidate_pool_new_columns(
    db_session: AsyncSession,
) -> None:
    repo = MarketDataRepository(db_session)
    await _setup_base(repo)
    svc = _make_service(db_session, factor_monitor=None, pool_capacity=3)

    composites = await svc.score_universe(
        db_session, _TRADE_DATE, _STOCK_CODES, MarketStateEnum.UPTREND,
    )
    pool_codes = await svc.write_candidate_pool(
        composites=composites,
        trade_date=_TRADE_DATE,
        holding_codes=frozenset(),
        whitelist_codes=frozenset(),
    )
    assert len(pool_codes) == 3, "pool_capacity=3 限制下应入池 3 只"
    await db_session.flush()

    # 读回 candidate_pool 并核对新列
    pool_rows = await repo.get_pool(trade_date=_TRADE_DATE, in_pool_only=True)
    assert len(pool_rows) == 3
    for row in pool_rows:
        assert row.composite_z is not None, "composite_z 应写入"
        assert row.composite_pct_in_market is not None
        assert row.weights_source == "default_matrix"
        assert row.hysteresis_status == "stable"
        assert isinstance(row.score_breakdown_raw, dict)
        assert isinstance(row.score_breakdown_residual, dict)


# ============================================================
# INT-P11-SC-04：market_cap PIT + industry 中性化贯通（Step 2 不抛异常）
# ============================================================
@pytest.mark.anyio
async def test_int_p11_sc_04_market_cap_pit_and_industry_neutralize(
    db_session: AsyncSession,
) -> None:
    repo = MarketDataRepository(db_session)
    await _setup_base(repo)

    # 直接调 _build_market_snapshot 验证 industry / market_cap 加载
    svc = _make_service(db_session)
    snap = await svc._build_market_snapshot(_TRADE_DATE, _STOCK_CODES)

    # industry 字典正确填充 8 只股票
    assert isinstance(snap["industry"], dict)
    assert set(snap["industry"]) == set(_STOCK_CODES)
    # market_cap Series 含 PIT 值
    market_cap = snap["market_cap"]
    assert market_cap is not None
    assert len(market_cap) == len(_STOCK_CODES)
    # 单调递增（设置时 i+1 因子）→ 第 0 只市值最小，第 7 只最大
    sorted_codes = market_cap.sort_values().index.tolist()
    assert sorted_codes[0] == _STOCK_CODES[0]
    assert sorted_codes[-1] == _STOCK_CODES[-1]
    # beta V1.0 未实现
    assert snap.get("beta") is None

    # 端到端 score_universe 跑通（中性化不应抛异常）
    composites = await svc.score_universe(
        db_session, _TRADE_DATE, _STOCK_CODES, MarketStateEnum.UPTREND,
    )
    assert len(composites) > 0


# ============================================================
# INT-P11-SC-05：_run_phase11_pipeline PIT 选 trade_date 当日 state（修 #155）
# ============================================================
@pytest.mark.anyio
async def test_int_p11_sc_05_phase11_pipeline_uses_pit_market_state(
    db_session: AsyncSession,
) -> None:
    """跑 _run_phase11_pipeline 时 market_state 必须取 trade_date 当日（PIT），
    不能取整表最新日（修 #155：旧实现 get_latest_market_state() 无 before_date
    导致跨制度回测全部用最新 state 权重）。
    """
    repo = MarketDataRepository(db_session)
    await _setup_base(repo)  # 写 _TRADE_DATE=2025-12-31 UPTREND

    # 追加一条更晚的 market_state（应被忽略）
    later_date = _TRADE_DATE + timedelta(days=10)
    await repo.upsert_market_state(MarketStateRecord(
        trade_date=later_date,
        market_state=MarketStateEnum.DOWNTREND,
        trend_strength=40.0, adx_value=40.0, ma20=3500.0, ma60=3700.0,
        state_changed=True, description="后续 DOWNTREND（不该被 _TRADE_DATE 取到）",
    ))

    svc = _make_service(db_session)
    composites = await svc._run_phase11_pipeline(_TRADE_DATE, holding_codes=frozenset())
    # 若实现错误取了 later_date 的 DOWNTREND，权重矩阵将不同 → 评分输出会有差异
    # 这里粗粒度断言 composites 非空（具体权重断言由单元测试覆盖）；
    # 通过 inspect 候选池的 market_state 列侧验：
    pool_rows = await repo.get_pool(trade_date=_TRADE_DATE)
    assert pool_rows, "应有候选池"
    states_in_pool = {r.market_state for r in pool_rows if r.in_pool}
    assert states_in_pool == {MarketStateEnum.UPTREND.value}, (
        f"_TRADE_DATE 的 candidate_pool.market_state 应为 UPTREND（当日），"
        f"实际 {states_in_pool} —— 说明 _run_phase11_pipeline 取了更晚的 state"
    )
    assert len(composites) > 0
