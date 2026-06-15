"""INT-BE-01~08：BacktestEngine 集成测试（mock 全部依赖，无 DB）。

V1.0 整改 Batch 3 — B3-10 新增 INT-BE-03~08：
- INT-BE-03：T+1 撮合（默认 OPEN_T1，T 日信号 → T+1 开盘价撮合）
- INT-BE-04：涨停日 BUY 不成交（limit_up=True）
- INT-BE-05：停牌日 BUY/SELL 全跳过（is_suspended=True）
- INT-BE-06：退市过滤（trade_date >= delist_date 时 stock_info 移除）
- INT-BE-07：RiskChecker BLOCK 集中度 → BUY 信号被剔除
- INT-BE-08：daily_quotes 完整字段切片 + open 价撮合差异验证
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pandas as pd

from quantpilot.engine.backtest.engine import (
    BacktestConfig,
    BacktestDataBundle,
    BacktestEngine,
)
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.scorer import CompositeScore


def _make_calendar(trade_dates: list[date]) -> MagicMock:
    cal = MagicMock()
    cal.get_trade_dates.return_value = trade_dates
    cal.get_prev_trade_date.return_value = date(2022, 1, 1)
    return cal


def _make_market_state_engine(state: MarketStateEnum = MarketStateEnum.OSCILLATION) -> MagicMock:
    eng = MagicMock()
    eng.identify_latest.return_value = state
    return eng


def _make_universe_filter(pass_all: bool = True) -> MagicMock:
    uf = MagicMock()
    # 返回空 Index 代表无任何通过过滤的股票（简化回测）
    uf.filter.return_value = pd.Index([], name="ts_code")
    return uf


def _make_scorer() -> MagicMock:
    scorer = MagicMock()
    scorer.aggregate_legacy.return_value = pd.DataFrame()
    return scorer


def _make_signal_engine() -> MagicMock:
    eng = MagicMock()
    eng.generate.return_value = []
    return eng


def _make_position_engine() -> MagicMock:
    eng = MagicMock()
    eng.suggest.return_value = []
    return eng


def _make_config(
    trade_dates: list[date],
    commission: float = 0.00025,
    stamp_tax: float = 0.0005,
    slippage: float = 0.001,
) -> BacktestConfig:
    return BacktestConfig(
        start_date=trade_dates[0],
        end_date=trade_dates[-1],
        initial_capital=1_000_000.0,
        strategy_config={},
        account_config={},
        commission_rate=commission,
        stamp_tax_rate=stamp_tax,
        slippage_rate=slippage,
    )


def _make_empty_bundle() -> BacktestDataBundle:
    return BacktestDataBundle(
        adj_prices=pd.DataFrame(),
        stock_info=pd.DataFrame(),
        financials=pd.DataFrame(),
        hs300_history=pd.DataFrame(),
    )


# ─── INT-BE-01 ───

def test_int_be_01_empty_result_structure() -> None:
    """INT-BE-01：mock 全部依赖（空信号/空分），3 交易日 → BacktestResult 结构完整。"""
    trade_dates = [date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5)]
    config = _make_config(trade_dates)
    calendar = _make_calendar(trade_dates)

    engine = BacktestEngine(
        strategies=[],
        market_state_engine=_make_market_state_engine(),
        universe_filter=_make_universe_filter(),
        scorer=_make_scorer(),
        signal_engine=_make_signal_engine(),
        position_engine=_make_position_engine(),
        price_provider=None,
        calendar=calendar,
    )

    result = engine.run(config, _make_empty_bundle())

    # 结构完整
    assert hasattr(result, "daily_nav")
    assert hasattr(result, "daily_positions")
    assert hasattr(result, "signal_history")
    assert hasattr(result, "performance")
    assert hasattr(result, "disclaimer")

    # daily_nav 长度 = 3（3 个交易日，全部跳过行情因 adj_prices 为空，但仍记录）
    # 实际上 adj_prices 为空时，引擎对每个日期直接用 initial_capital 作为 nav
    assert len(result.daily_nav) == 3

    # performance 包含必要字段
    assert "cumulative_return" in result.performance
    assert "max_drawdown" in result.performance


# ─── INT-BE-02 ───

def test_int_be_02_cost_reduces_nav() -> None:
    """INT-BE-02：有交易成本 vs. 无交易成本（0/0/0），同一信号 → 有成本时 nav 应 ≤ 无成本时 nav。"""
    # 构造一个 BUY 信号，让引擎触发交易
    from quantpilot.engine.signal import TradeSignal
    from quantpilot.engine.strategies.base import StrategyScore

    trade_dates = [date(2023, 1, 3), date(2023, 1, 4)]

    # 行情：ts_code=0001.SZ，close=10.0（构造 adj_prices）
    adj_prices = pd.DataFrame(
        {"0001.SZ": [10.0, 10.0]},
        index=pd.DatetimeIndex([date(2023, 1, 3), date(2023, 1, 4)]),
    )
    adj_prices.index.name = "trade_date"

    buy_signal = TradeSignal(
        ts_code="0001.SZ",
        signal_type="BUY",
        trade_date=date(2023, 1, 3),
        score=90.0,
        suggested_pct=0.10,
    )

    def _run_with_cost(commission: float, stamp_tax: float, slippage: float) -> float:
        sig_engine = MagicMock()
        sig_engine.generate.return_value = [buy_signal]
        pos_engine = MagicMock()
        pos_engine.suggest.return_value = [buy_signal]

        # universe_filter 返回我们的股票
        uf = MagicMock()
        uf.filter.return_value = pd.Index(["0001.SZ"])

        # mock 策略：返回 0001.SZ 的评分（确保 strategy_scores 非空，scorer.aggregate 被调用）
        # 引擎仅在 strategy_scores 非空时才调用 scorer.aggregate → signal_engine.generate
        mock_strategy = MagicMock()
        mock_strategy.score.return_value = [
            StrategyScore(ts_code="0001.SZ", raw_factors={}, score=85.0, reason="mock")
        ]

        # scorer 返回非空 composite，signal_engine 才会被调用
        # 实际契约：Scorer.aggregate → list[CompositeScore]（engine.py 按 cs.ts_code 等字段读取）
        scorer = MagicMock()
        scorer.aggregate_legacy.return_value = [
            CompositeScore(
                ts_code="0001.SZ", composite_score=85.0,
                trend_score=85.0, momentum_score=None,
                reversion_score=None, value_score=None,
                market_state=MarketStateEnum.OSCILLATION,
                score_breakdown={"trend": {"score": 85.0, "weight": 1.0, "contribution": 85.0}},
                explanation="mock",
            )
        ]

        # stock_info 包含 0001.SZ
        stock_info = pd.DataFrame(
            {
                "list_date": [date(2020, 1, 1)], "is_st": [False],
                "sw_industry_l1": ["制造"], "is_suspended": [False],
            },
            index=pd.Index(["0001.SZ"], name="ts_code"),
        )

        config = BacktestConfig(
            start_date=date(2023, 1, 3),
            end_date=date(2023, 1, 4),
            initial_capital=1_000_000.0,
            strategy_config={},
            account_config={},
            commission_rate=commission,
            stamp_tax_rate=stamp_tax,
            slippage_rate=slippage,
        )
        data = BacktestDataBundle(
            adj_prices=adj_prices,
            stock_info=stock_info,
            financials=pd.DataFrame(),
            hs300_history=pd.DataFrame(),
        )
        engine = BacktestEngine(
            strategies=[mock_strategy],
            market_state_engine=_make_market_state_engine(),
            universe_filter=uf,
            scorer=scorer,
            signal_engine=sig_engine,
            position_engine=pos_engine,
            price_provider=None,
            calendar=_make_calendar(trade_dates),
        )
        result = engine.run(config, data)
        # 返回最终净值
        if len(result.daily_nav) > 0:
            return float(result.daily_nav.iloc[-1])
        return 1.0

    nav_with_cost = _run_with_cost(0.00025, 0.0005, 0.001)
    nav_no_cost = _run_with_cost(0.0, 0.0, 0.0)

    # C-01 修复后实际执行了买入；nav 是归一化值（1.0 = 初始资金）
    # 有成本时 nav 严格小于无成本，差值至少 1e-4（对应 1M 资金中约 100 元成本）
    assert nav_with_cost < nav_no_cost - 1e-4


# ---------------------------------------------------------------------------
# INT-BE-03~08 公共 helper（B3-10）
# ---------------------------------------------------------------------------

def _build_two_day_run(
    *,
    quotes_t0: dict,
    quotes_t1: dict,
    stock_info_extra: dict | None = None,
    execution_price: str = "OPEN_T1",
):
    """构造 2 交易日 + 1 只股票的最小回测：T0 生成 BUY 信号，T1 撮合。

    quotes_t0 / quotes_t1：daily_quote 字段字典（含 open/close/limit_up/is_suspended 等）。
    stock_info_extra：覆盖 stock_info 默认（如加 delist_date）。
    """
    from quantpilot.engine.signal import TradeSignal
    from quantpilot.engine.strategies.base import StrategyScore

    ts_code = "0001.SZ"
    t0, t1 = date(2023, 1, 3), date(2023, 1, 4)
    trade_dates = [t0, t1]

    # adj_prices（仅 close × adj_factor）
    adj_prices = pd.DataFrame(
        {ts_code: [quotes_t0["close"], quotes_t1["close"]]},
        index=pd.DatetimeIndex([t0, t1]),
    )
    adj_prices.index.name = "trade_date"

    # daily_quotes 含完整字段，MultiIndex(trade_date, ts_code)
    rows = []
    for d, q in [(t0, quotes_t0), (t1, quotes_t1)]:
        rows.append({
            "trade_date": d, "ts_code": ts_code,
            "open": q.get("open", q["close"]),
            "high": q["close"], "low": q["close"],
            "close": q["close"],
            "vol": q.get("vol", 1_000_000),
            "amount": q.get("amount", 10_000_000.0),
            "adj_factor": 1.0,
            "is_suspended": q.get("is_suspended", False),
            "is_st": q.get("is_st", False),
            "limit_up": q.get("limit_up", False),
            "limit_down": q.get("limit_down", False),
            "sw_industry_l1": "制造",
        })
    daily_quotes = pd.DataFrame(rows).set_index(["trade_date", "ts_code"]).sort_index()

    # stock_info
    si = {"list_date": date(2020, 1, 1), "delist_date": None, "sw_industry_l1": "制造"}
    if stock_info_extra:
        si.update(stock_info_extra)
    stock_info = pd.DataFrame([si], index=pd.Index([ts_code], name="ts_code"))

    # 信号生成 mock：T0 给出 BUY 信号
    buy_signal = TradeSignal(
        ts_code=ts_code, signal_type="BUY",
        trade_date=t0, score=85.0, suggested_pct=0.10,
    )
    sig_engine = MagicMock()
    sig_engine.generate.return_value = [buy_signal]
    pos_engine = MagicMock()
    pos_engine.suggest.return_value = [buy_signal]

    uf = MagicMock()
    uf.filter.return_value = pd.Index([ts_code])

    mock_strategy = MagicMock()
    mock_strategy.score.return_value = [
        StrategyScore(ts_code=ts_code, raw_factors={}, score=85.0, reason="mock")
    ]

    scorer = MagicMock()
    scorer.aggregate_legacy.return_value = [
        CompositeScore(
            ts_code=ts_code, composite_score=85.0,
            trend_score=85.0, momentum_score=None,
            reversion_score=None, value_score=None,
            market_state=MarketStateEnum.OSCILLATION,
            score_breakdown={"trend": {"score": 85.0, "weight": 1.0, "contribution": 85.0}},
            explanation="mock",
        )
    ]

    config = BacktestConfig(
        start_date=t0, end_date=t1,
        initial_capital=1_000_000.0,
        strategy_config={}, account_config={},
        commission_rate=0.0, stamp_tax_rate=0.0, slippage_rate=0.0,
        execution_price=execution_price,
    )
    data = BacktestDataBundle(
        adj_prices=adj_prices, stock_info=stock_info,
        financials=pd.DataFrame(), hs300_history=pd.DataFrame(),
        daily_quotes=daily_quotes,
    )
    engine = BacktestEngine(
        strategies=[mock_strategy],
        market_state_engine=_make_market_state_engine(),
        universe_filter=uf,
        scorer=scorer,
        signal_engine=sig_engine,
        position_engine=pos_engine,
        price_provider=None,
        calendar=_make_calendar(trade_dates),
    )
    return engine.run(config, data)


# ─── INT-BE-03：T+1 撮合 ───
def test_int_be_03_t1_open_execution() -> None:
    """T 日生成 BUY 信号，T+1 日开盘价撮合（OPEN_T1，B3-2）。"""
    result = _build_two_day_run(
        quotes_t0={"open": 10.0, "close": 10.0},
        quotes_t1={"open": 11.0, "close": 12.0},  # T+1 开盘 11
        execution_price="OPEN_T1",
    )
    # T+1 撮合发生：trade_records 中 price ≈ 11（T+1 开盘价），不是 10（T 收盘）
    buys = [r for r in result.signal_history if r["signal_type"] == "BUY"]
    assert len(buys) == 1, "T+1 模式下 BUY 应在 T+1 日撮合"
    assert abs(buys[0]["price"] - 11.0) < 1e-6, (
        f"T+1 撮合价应 = T+1 open=11.0，实际 {buys[0]['price']}"
    )


# ─── INT-BE-04：涨停日 BUY 不成交 ───
def test_int_be_04_limit_up_blocks_buy() -> None:
    """T+1 涨停（limit_up=True）→ BUY 不成交（B3-1）。"""
    result = _build_two_day_run(
        quotes_t0={"open": 10.0, "close": 10.0},
        quotes_t1={"open": 11.0, "close": 11.0, "limit_up": True},
        execution_price="OPEN_T1",
    )
    buys = [r for r in result.signal_history if r["signal_type"] == "BUY"]
    assert len(buys) == 0, "涨停日 BUY 应不成交"


# ─── INT-BE-05：停牌日不交易 ───
def test_int_be_05_suspended_blocks_trade() -> None:
    """T+1 停牌（is_suspended=True）→ BUY 跳过（B3-1）。"""
    result = _build_two_day_run(
        quotes_t0={"open": 10.0, "close": 10.0},
        quotes_t1={"open": 11.0, "close": 11.0, "is_suspended": True},
        execution_price="OPEN_T1",
    )
    buys = [r for r in result.signal_history if r["signal_type"] == "BUY"]
    assert len(buys) == 0, "停牌日 BUY 应跳过"


# ─── INT-BE-06：退市日过滤 ───
def test_int_be_06_delist_filter() -> None:
    """delist_date 早于 trade_date → stock_info_at 返回空，无信号生成（B3-6）。"""
    result = _build_two_day_run(
        quotes_t0={"open": 10.0, "close": 10.0},
        quotes_t1={"open": 11.0, "close": 11.0},
        stock_info_extra={"delist_date": date(2023, 1, 2)},  # 已退市
        execution_price="OPEN_T1",
    )
    assert len(result.signal_history) == 0, "退市股不应进入候选池，无成交记录"


# ─── INT-BE-07：RiskChecker BLOCK 集中度 ───
def test_int_be_07_risk_checker_blocks_concentration() -> None:
    """suggested_pct=0.99（远超 max_single_stock_pct=0.20）→ BUY 被 RiskChecker BLOCK（B3-4）。"""
    from quantpilot.engine.signal import TradeSignal
    from quantpilot.engine.strategies.base import StrategyScore

    t0, t1 = date(2023, 1, 3), date(2023, 1, 4)
    ts_code = "0001.SZ"

    adj_prices = pd.DataFrame(
        {ts_code: [10.0, 11.0]}, index=pd.DatetimeIndex([t0, t1]),
    )
    adj_prices.index.name = "trade_date"

    rows = [{
        "trade_date": t0, "ts_code": ts_code,
        "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
        "vol": 1_000_000, "amount": 10_000_000.0, "adj_factor": 1.0,
        "is_suspended": False, "is_st": False, "limit_up": False, "limit_down": False,
        "sw_industry_l1": "制造",
    }, {
        "trade_date": t1, "ts_code": ts_code,
        "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0,
        "vol": 1_000_000, "amount": 10_000_000.0, "adj_factor": 1.0,
        "is_suspended": False, "is_st": False, "limit_up": False, "limit_down": False,
        "sw_industry_l1": "制造",
    }]
    daily_quotes = pd.DataFrame(rows).set_index(["trade_date", "ts_code"]).sort_index()

    stock_info = pd.DataFrame(
        [{"list_date": date(2020, 1, 1), "delist_date": None, "sw_industry_l1": "制造"}],
        index=pd.Index([ts_code], name="ts_code"),
    )

    # PositionSizer 给 suggested_pct=0.99（极高，触发集中度 BLOCK）
    extreme_buy = TradeSignal(
        ts_code=ts_code, signal_type="BUY",
        trade_date=t0, score=85.0, suggested_pct=0.99,
    )
    sig_engine = MagicMock()
    sig_engine.generate.return_value = [extreme_buy]
    pos_engine = MagicMock()
    pos_engine.suggest.return_value = [extreme_buy]

    uf = MagicMock()
    uf.filter.return_value = pd.Index([ts_code])
    mock_strategy = MagicMock()
    mock_strategy.score.return_value = [
        StrategyScore(ts_code=ts_code, raw_factors={}, score=85.0, reason="mock")
    ]
    scorer = MagicMock()
    scorer.aggregate_legacy.return_value = [
        CompositeScore(
            ts_code=ts_code, composite_score=85.0,
            trend_score=85.0, momentum_score=None, reversion_score=None, value_score=None,
            market_state=MarketStateEnum.OSCILLATION,
            score_breakdown={"trend": {"score": 85.0, "weight": 1.0, "contribution": 85.0}},
            explanation="mock",
        )
    ]
    config = BacktestConfig(
        start_date=t0, end_date=t1,
        initial_capital=1_000_000.0,
        strategy_config={}, account_config={},
        commission_rate=0.0, stamp_tax_rate=0.0, slippage_rate=0.0,
        execution_price="OPEN_T1",
    )
    data = BacktestDataBundle(
        adj_prices=adj_prices, stock_info=stock_info,
        financials=pd.DataFrame(), hs300_history=pd.DataFrame(),
        daily_quotes=daily_quotes,
    )
    engine = BacktestEngine(
        strategies=[mock_strategy],
        market_state_engine=_make_market_state_engine(),
        universe_filter=uf, scorer=scorer,
        signal_engine=sig_engine, position_engine=pos_engine,
        price_provider=None, calendar=_make_calendar([t0, t1]),
    )
    result = engine.run(config, data)

    # suggested_pct=0.99 > max_single_stock_pct=0.20 → BLOCK → 无成交
    buys = [r for r in result.signal_history if r["signal_type"] == "BUY"]
    assert len(buys) == 0, "集中度 BLOCK 应阻止 BUY 撮合（B3-4）"


# ─── INT-BE-09：真实 MarketStateEngine + 整数索引 hs300（回归 bug #2）───
def test_int_be_09_market_state_real_engine_int_indexed_hs300() -> None:
    """回归：_get_market_state 须把 _load_data_bundle 产出的 hs300_history（整数 RangeIndex
    + trade_date 列）正确转 date 索引再喂 MarketStateEngine.identify_latest，否则报
    'int object has no attribute date' 被吞 → 恒回落 OSCILLATION → 回测退化。

    既有 INT-BE-01~08 全 mock market_state_engine，从不跑真实引擎，故漏掉此 bug。
    本测试用真实 MarketStateEngine + 强上行序列，断言识别出 UPTREND（非异常回落）。
    """
    from quantpilot.engine.market_state import MarketStateEngine

    # 80 个交易日的强单调上行（close 10→26），与 _load_data_bundle 同构：
    # 整数 RangeIndex + trade_date 列（而非 date 索引）
    n = 80
    dates = pd.bdate_range("2023-01-02", periods=n).date
    closes = [10.0 + i * 0.2 for i in range(n)]
    hs300_history = pd.DataFrame({
        "trade_date": list(dates),
        "open": [c - 0.05 for c in closes],
        "high": [c + 0.1 for c in closes],
        "low": [c - 0.1 for c in closes],
        "close": closes,
        "vol": [1_000_000.0] * n,
    })  # 默认 RangeIndex（关键：模拟 _load_data_bundle 的 hs300_history 形状）

    engine = BacktestEngine(
        strategies=[],
        market_state_engine=MarketStateEngine(),  # 真实，非 mock
        universe_filter=_make_universe_filter(),
        scorer=_make_scorer(),
        signal_engine=_make_signal_engine(),
        position_engine=_make_position_engine(),
        price_provider=None,
        calendar=_make_calendar([dates[-1]]),
    )
    state = engine._get_market_state(hs300_history, dates[-1])
    assert state == MarketStateEnum.UPTREND, (
        f"强上行序列应识别 UPTREND，实际 {state}——整数索引未转 date 索引，"
        "identify 报错被吞 → 回落 OSCILLATION"
    )


# ─── INT-BE-10：真实 UniverseFilter + stock_info 缺 is_st（回归 bug #1）───
def test_int_be_10_universe_real_filter_pit_is_st_injected() -> None:
    """回归：_load_data_bundle 的 stock_info 只有 list_date/delist_date/sw_industry_l1，
    缺 is_st/is_suspended（这俩随日期变化、应从当日 quotes_t PIT 注入）。引擎须在过滤前
    把 PIT is_st/is_suspended 并进 stock_info_t，否则真实 UniverseFilter F-1/F-3 抛 KeyError
    → universe 整日空 → 回测退化（NAV 恒 1.0）。

    既有 INT-BE 全 mock universe_filter，从不跑真实过滤，故漏掉此 bug。
    本测试用真实 UniverseFilter；断言 universe 非空（strategy.score 被调用）。
    """
    from quantpilot.engine.strategies.base import StrategyScore
    from quantpilot.engine.universe import UniverseFilter

    t0, t1 = date(2023, 1, 3), date(2023, 1, 4)
    ts_code = "0001.SZ"
    adj_prices = pd.DataFrame({ts_code: [10.0, 10.0]}, index=pd.DatetimeIndex([t0, t1]))
    adj_prices.index.name = "trade_date"

    rows = []
    for d in (t0, t1):
        rows.append({
            "trade_date": d, "ts_code": ts_code,
            "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
            "vol": 1_000_000, "amount": 100_000_000.0, "adj_factor": 1.0,
            "is_suspended": False, "is_st": False, "limit_up": False, "limit_down": False,
            "sw_industry_l1": "制造",
        })
    daily_quotes = pd.DataFrame(rows).set_index(["trade_date", "ts_code"]).sort_index()

    # 关键：stock_info 故意缺 is_st/is_suspended（与 _load_data_bundle 的 si_map 一致）
    stock_info = pd.DataFrame(
        [{"list_date": date(2020, 1, 1), "delist_date": None, "sw_industry_l1": "制造"}],
        index=pd.Index([ts_code], name="ts_code"),
    )

    mock_strategy = MagicMock()
    mock_strategy.score.return_value = [
        StrategyScore(ts_code=ts_code, raw_factors={}, score=85.0, reason="mock")
    ]
    scorer = MagicMock()
    scorer.aggregate_legacy.return_value = []  # 不关心后续撮合，只验 universe 非空

    data = BacktestDataBundle(
        adj_prices=adj_prices, stock_info=stock_info,
        financials=pd.DataFrame(), hs300_history=pd.DataFrame(),
        daily_quotes=daily_quotes,
    )
    engine = BacktestEngine(
        strategies=[mock_strategy],
        market_state_engine=_make_market_state_engine(),  # mock，隔离 bug #1
        universe_filter=UniverseFilter(),  # 真实
        scorer=scorer,
        signal_engine=_make_signal_engine(),
        position_engine=_make_position_engine(),
        price_provider=None,
        calendar=_make_calendar([t0, t1]),
    )
    engine.run(_make_config([t0, t1]), data)

    assert mock_strategy.score.called, (
        "真实 UniverseFilter 应通过该股（universe 非空）；strategy.score 未被调用 = "
        "universe 整日空——引擎未把 PIT is_st/is_suspended 注入 stock_info_t"
    )


# ─── INT-BE-08：T+1 vs CLOSE_T 撮合差异 ───
def test_int_be_08_open_vs_close_execution_diff() -> None:
    """T+1 开盘 11.0 vs CLOSE_T close 10.0 → 撮合价不同（B3-2 闭环）。"""
    open_t1 = _build_two_day_run(
        quotes_t0={"open": 10.0, "close": 10.0},
        quotes_t1={"open": 11.0, "close": 12.0},
        execution_price="OPEN_T1",
    )
    close_t = _build_two_day_run(
        quotes_t0={"open": 10.0, "close": 10.0},
        quotes_t1={"open": 11.0, "close": 12.0},
        execution_price="CLOSE_T",
    )
    open_buys = [r for r in open_t1.signal_history if r["signal_type"] == "BUY"]
    close_buys = [r for r in close_t.signal_history if r["signal_type"] == "BUY"]
    assert len(open_buys) == 1 and len(close_buys) == 1
    assert abs(open_buys[0]["price"] - 11.0) < 1e-6, "OPEN_T1 撮合价 = T+1 open=11.0"
    assert abs(close_buys[0]["price"] - 10.0) < 1e-6, "CLOSE_T 撮合价 = T 收盘 10.0"
