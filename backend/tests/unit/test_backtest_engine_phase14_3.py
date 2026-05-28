"""Phase 14 §14-3 BacktestEngine 真 5 步管线接入 — UT-P14-3-01~05。

覆盖：
- UT-P14-3-01：`_lookup_active_weights` 前向查找正确（多 state × 多日期，PIT + state 过滤）
- UT-P14-3-02：`_lookup_active_weights` 找不到 snapshot → 返回 default_matrix sentinel
- UT-P14-3-03：BacktestEngine.run universe ≥ 30 + weights 就绪 → pipeline_mode='real_5step'
- UT-P14-3-04：BacktestEngine.run universe < 30 → pipeline_mode='legacy_fallback'
- UT-P14-3-05：BacktestEngine.run active_weights_history 为空 → pipeline_mode='legacy_fallback'

设计文档：docs/design/phases/phase14_account_integrity.md §5（v1.2）。

注：v1.2 §5.2.2 代码模板里的 (market_state, effective_date) 键 / weights_json blob 是
误判，实际 ORM `StrategyWeightsHistory` 是一行一 (state, strategy, trade_date) + weight_used
标量；本测试按实际 schema 注入数据（与 FactorMonitorService.get_active_weights:711 路径同）。
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from quantpilot.engine.backtest.engine import (
    BacktestConfig,
    BacktestDataBundle,
    BacktestEngine,
)
from quantpilot.engine.market_state import MarketStateEnum

# ============================================================
# UT-P14-3-01/02: _lookup_active_weights 纯函数
# ============================================================


def _make_engine_for_lookup() -> BacktestEngine:
    """构造最小 BacktestEngine 实例用于 helper 测试（不真正跑回测主循环）。"""
    return BacktestEngine(
        strategies=[],
        market_state_engine=None,
        universe_filter=None,
        scorer=None,
        signal_engine=None,
        position_engine=None,
        price_provider=None,
        calendar=None,
    )


def test_ut_p14_3_01_lookup_active_weights_pit_and_state_filter() -> None:
    """UT-P14-3-01：前向查找——max(effective_date) <= trade_date AND state 匹配。

    构造 3 个 (state, eff_date) snapshot，按 (UPTREND, 2024-04-01) 查询应返回 2024-04-01
    而不是 2024-05-01（PIT）；按 (DOWNTREND, 2024-04-01) 应返回 2024-02-01。
    """
    engine = _make_engine_for_lookup()
    history = {
        ("UPTREND", date(2024, 1, 1)): {
            "weights": {"trend": 0.3, "momentum": 0.3, "mean_reversion": 0.2, "value": 0.2},
            "weights_source": "icir",
            "orthogonalize_order": ["trend", "momentum", "mean_reversion", "value"],
            "hysteresis_status": "stable",
        },
        ("UPTREND", date(2024, 4, 1)): {
            "weights": {"trend": 0.4, "momentum": 0.3, "mean_reversion": 0.2, "value": 0.1},
            "weights_source": "icir",
            "orthogonalize_order": ["trend", "momentum", "mean_reversion", "value"],
            "hysteresis_status": "stable",
        },
        ("UPTREND", date(2024, 5, 1)): {
            "weights": {"trend": 0.5, "momentum": 0.2, "mean_reversion": 0.2, "value": 0.1},
            "weights_source": "icir",
            "orthogonalize_order": ["trend", "momentum", "mean_reversion", "value"],
            "hysteresis_status": "stable",
        },
        ("DOWNTREND", date(2024, 2, 1)): {
            "weights": {"trend": 0.1, "momentum": 0.1, "mean_reversion": 0.4, "value": 0.4},
            "weights_source": "icir",
            "orthogonalize_order": ["mean_reversion", "value", "trend", "momentum"],
            "hysteresis_status": "stable",
        },
    }

    rec = engine._lookup_active_weights(date(2024, 4, 1), "UPTREND", history)
    assert rec["weights"]["trend"] == pytest.approx(0.4)

    rec_down = engine._lookup_active_weights(date(2024, 4, 1), "DOWNTREND", history)
    assert rec_down["weights"]["mean_reversion"] == pytest.approx(0.4)


def test_ut_p14_3_02_lookup_active_weights_missing_returns_default() -> None:
    """UT-P14-3-02：找不到 snapshot（state 不存在或 trade_date 早于所有 eff_date）
    → 返回 `weights=None` 的 default_matrix sentinel，触发 legacy fallback。"""
    engine = _make_engine_for_lookup()
    history = {
        ("UPTREND", date(2024, 4, 1)): {
            "weights": {"trend": 0.4},
            "weights_source": "icir",
            "orthogonalize_order": ["trend"],
            "hysteresis_status": "stable",
        },
    }

    # state 不存在
    rec = engine._lookup_active_weights(date(2024, 4, 1), "OSCILLATION", history)
    assert rec["weights"] is None
    assert rec["weights_source"] == "default_matrix"
    assert rec["hysteresis_status"] == "stable"

    # trade_date 早于所有 eff_date
    rec2 = engine._lookup_active_weights(date(2023, 1, 1), "UPTREND", history)
    assert rec2["weights"] is None
    assert rec2["weights_source"] == "default_matrix"


# ============================================================
# UT-P14-3-03/04/05: 主循环 pipeline_mode 分支
# ============================================================


@pytest.fixture
def stub_calendar() -> object:
    class _Calendar:
        def get_trade_dates(self, start: date, end: date) -> list[date]:
            return [date(2024, 6, 3)]

    return _Calendar()


@pytest.fixture
def stub_market_state_engine() -> object:
    class _MSE:
        def identify_latest(self, hist: pd.DataFrame) -> object:
            class _R:
                market_state = MarketStateEnum.UPTREND

            return _R()

    return _MSE()


class _CapturingScorer:
    """记录 aggregate / aggregate_legacy 调用，断言 pipeline_mode 分支。"""

    def __init__(self) -> None:
        self.aggregate_calls = 0
        self.aggregate_legacy_calls = 0

    def aggregate(self, **kwargs: object) -> list:
        self.aggregate_calls += 1
        # 返回最小合法 list，含一个 CompositeScore-like object
        from quantpilot.engine.scorer import CompositeScore

        return [
            CompositeScore(
                ts_code="000001.SZ",
                composite_score=80.0,
                trend_score=80.0,
                momentum_score=70.0,
                reversion_score=60.0,
                value_score=50.0,
                market_state=MarketStateEnum.UPTREND,
                score_breakdown={},
                explanation="phase11",
                composite_z=1.5,
                composite_pct_in_market=0.02,
                weights_source="icir",
                hysteresis_status="stable",
            )
        ]

    def aggregate_legacy(self, *args: object, **kwargs: object) -> list:
        self.aggregate_legacy_calls += 1
        from quantpilot.engine.scorer import CompositeScore

        return [
            CompositeScore(
                ts_code="000001.SZ",
                composite_score=70.0,
                trend_score=70.0,
                momentum_score=60.0,
                reversion_score=50.0,
                value_score=40.0,
                market_state=MarketStateEnum.UPTREND,
                score_breakdown={},
                explanation="legacy",
            )
        ]


class _NoopStrategy:
    name = "trend"

    def compute_strategy_factors(self, universe: pd.Index, snap: dict) -> pd.DataFrame:
        return pd.DataFrame(
            {"factor_a": np.linspace(0.1, 1.0, len(universe))}, index=universe,
        )

    def score(self, universe: pd.Index, snap: dict) -> list:
        from quantpilot.engine.strategies.base import StrategyScore

        return [
            StrategyScore(ts_code=c, raw_factors={}, score=60.0, reason="legacy")
            for c in universe
        ]


def _stub_universe_filter(universe_size: int) -> object:
    class _UF:
        def filter(
            self,
            stock_info: pd.DataFrame,
            financials: pd.DataFrame,
            quotes: pd.DataFrame,
            trade_date: date,
            calendar: object,
        ) -> pd.Index:
            return stock_info.index[:universe_size]

    return _UF()


class _NoopSignalEngine:
    def generate(self, *args: object, **kwargs: object) -> list:
        return []


class _NoopPositionEngine:
    def suggest(self, signals: list, *args: object, **kwargs: object) -> list:
        return signals


def _build_data_bundle(
    n_codes: int,
    *,
    with_weights_history: bool = True,
    trade_date: date = date(2024, 6, 3),
) -> BacktestDataBundle:
    codes = [f"{i:06d}.SZ" for i in range(n_codes)]
    td_pd = pd.Timestamp(trade_date)
    prev_td = pd.Timestamp(trade_date - pd.Timedelta(days=1))

    # adj_prices: 2 行 × n_codes 列
    adj_prices = pd.DataFrame(
        np.random.RandomState(42).uniform(10, 50, size=(2, n_codes)),
        index=[prev_td, td_pd], columns=codes,
    )

    # stock_info：含 list_date / delist_date / sw_industry_l1 / float_mkt_cap
    si = pd.DataFrame(
        {
            "list_date": [date(2015, 1, 1)] * n_codes,
            "delist_date": [None] * n_codes,
            "sw_industry_l1": ["银行"] * (n_codes // 2) + ["医药"] * (n_codes - n_codes // 2),
        },
        index=pd.Index(codes, name="ts_code"),
    )

    # daily_quotes: MultiIndex(trade_date, ts_code)，含 close/open/limit/is_st/is_suspended/
    # float_mkt_cap/sw_industry_l1
    rows = []
    for d in (prev_td, td_pd):
        for i, c in enumerate(codes):
            rows.append({
                "trade_date": d,
                "ts_code": c,
                "open": 20.0 + i * 0.1,
                "high": 21.0,
                "low": 19.5,
                "close": 20.5 + i * 0.1,
                "vol": 10000.0,
                "amount": 200000.0,
                "adj_factor": 1.0,
                "is_suspended": False,
                "is_st": False,
                "limit_up": False,
                "limit_down": False,
                "float_mkt_cap": 1e9 + i * 1e7,
                "sw_industry_l1": si.loc[c, "sw_industry_l1"],
            })
    dq = pd.DataFrame(rows).set_index(["trade_date", "ts_code"]).sort_index()

    # hs300_history (DatetimeIndex)
    hs300 = pd.DataFrame(
        {"open": [3000.0, 3010.0], "high": [3050.0, 3030.0], "low": [2990.0, 3000.0],
         "close": [3020.0, 3015.0], "vol": [1e8, 1e8]},
        index=[prev_td, td_pd],
    )

    # active_weights_history
    if with_weights_history:
        awh = {
            ("UPTREND", date(2024, 5, 1)): {
                "weights": {"trend": 0.4, "momentum": 0.3, "mean_reversion": 0.2, "value": 0.1},
                "weights_source": "icir",
                "orthogonalize_order": ["trend", "momentum", "mean_reversion", "value"],
                "hysteresis_status": "stable",
            }
        }
    else:
        awh = {}

    return BacktestDataBundle(
        adj_prices=adj_prices,
        stock_info=si,
        financials=pd.DataFrame(),
        hs300_history=hs300,
        daily_quotes=dq,
        pe_pb_history=pd.DataFrame(),
        index_adj_prices=pd.Series(dtype=float),
        active_weights_history=awh,
    )


def test_ut_p14_3_03_real_5step_when_universe_ge_30_and_weights_ready(
    stub_calendar: object, stub_market_state_engine: object,
) -> None:
    """UT-P14-3-03：universe ≥ 30 + active_weights 就绪 → 走 Scorer.aggregate；
    BacktestResult.pipeline_mode == 'real_5step'。"""
    scorer = _CapturingScorer()
    engine = BacktestEngine(
        strategies=[_NoopStrategy()],
        market_state_engine=stub_market_state_engine,
        universe_filter=_stub_universe_filter(40),
        scorer=scorer,
        signal_engine=_NoopSignalEngine(),
        position_engine=_NoopPositionEngine(),
        price_provider=None,
        calendar=stub_calendar,
    )
    cfg = BacktestConfig(
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 3),
        initial_capital=1_000_000.0,
        strategy_config={},
        account_config={},
    )
    data = _build_data_bundle(40, with_weights_history=True)
    result = engine.run(cfg, data)

    assert scorer.aggregate_calls == 1
    assert scorer.aggregate_legacy_calls == 0
    assert result.pipeline_mode == "real_5step"


def test_ut_p14_3_04_legacy_fallback_when_universe_lt_30(
    stub_calendar: object, stub_market_state_engine: object,
) -> None:
    """UT-P14-3-04：universe < 30 → 走 aggregate_legacy；pipeline_mode == 'legacy_fallback'。"""
    scorer = _CapturingScorer()
    engine = BacktestEngine(
        strategies=[_NoopStrategy()],
        market_state_engine=stub_market_state_engine,
        universe_filter=_stub_universe_filter(20),  # < WINSORIZE_MIN_SAMPLES
        scorer=scorer,
        signal_engine=_NoopSignalEngine(),
        position_engine=_NoopPositionEngine(),
        price_provider=None,
        calendar=stub_calendar,
    )
    cfg = BacktestConfig(
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 3),
        initial_capital=1_000_000.0,
        strategy_config={},
        account_config={},
    )
    data = _build_data_bundle(20, with_weights_history=True)
    result = engine.run(cfg, data)

    assert scorer.aggregate_calls == 0
    assert scorer.aggregate_legacy_calls == 1
    assert result.pipeline_mode == "legacy_fallback"


def test_ut_p14_3_05_legacy_fallback_when_weights_history_empty(
    stub_calendar: object, stub_market_state_engine: object,
) -> None:
    """UT-P14-3-05：active_weights_history 为空 → _lookup 返回 weights=None →
    走 aggregate_legacy；pipeline_mode == 'legacy_fallback'。"""
    scorer = _CapturingScorer()
    engine = BacktestEngine(
        strategies=[_NoopStrategy()],
        market_state_engine=stub_market_state_engine,
        universe_filter=_stub_universe_filter(40),
        scorer=scorer,
        signal_engine=_NoopSignalEngine(),
        position_engine=_NoopPositionEngine(),
        price_provider=None,
        calendar=stub_calendar,
    )
    cfg = BacktestConfig(
        start_date=date(2024, 6, 3),
        end_date=date(2024, 6, 3),
        initial_capital=1_000_000.0,
        strategy_config={},
        account_config={},
    )
    data = _build_data_bundle(40, with_weights_history=False)
    result = engine.run(cfg, data)

    assert scorer.aggregate_calls == 0
    assert scorer.aggregate_legacy_calls == 1
    assert result.pipeline_mode == "legacy_fallback"
