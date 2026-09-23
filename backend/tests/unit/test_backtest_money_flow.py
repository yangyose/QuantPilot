"""回测喂 `money_flow`（V1.5-C C4 转正前置 / L-FID 第三项，2026-09-23）。

## 此前的状态

`BacktestDataBundle` 无该字段、引擎构造快照时不填同名键 → `MoneyFlowStrategy` 逐日
两因子全 NaN → `Scorer.aggregate` 记一行 INFO 跳过。影子权重 0 故结果不受影响，但权重
一旦 > 0，回测会**静默按「少一个策略」的口径算**（`test_backtest_feeds_weighted_strategies.py`
就是为拦这一步而加的）。

## 现在的口径（必须与生产逐字相同）

生产 `ScoringService._build_market_snapshot` 调
`get_money_flow_window(ts_codes, trade_date, lookback_calendar_days=40)`：
`trade_date ∈ [td - 40 日历天, td]`、INNER JOIN `daily_quote` 取 `amount`、按
(ts_code, trade_date) 升序；策略再 `groupby.tail(N)` 取最近 N 行，不足 N 行 → NaN。

回测：Service 用**同一个 repo 方法**把窗口拉宽成 `[start - 40, end]` 一次取回，引擎
`_money_flow_at` 逐日按同一个 40 天下界 + `<= td` 上界切片。两处都不能省：
- 少了 `<= td` 上界 → **前视**（`tail(N)` 会取到未来行）；
- 少了下界 → 长期停牌股在生产是 NaN（窗口内行数不足），回测却拿到更老的行 → 有值。
"""
from __future__ import annotations

import ast
import inspect
from datetime import date, timedelta

import pandas as pd

from quantpilot.core.config_defaults import (
    DEFAULT_MONEY_FLOW_STRATEGY,
    MoneyFlowStrategyConfig,
)
from quantpilot.engine.backtest.engine import (
    BacktestDataBundle,
    BacktestEngine,
    _money_flow_at,
    resolve_money_flow_lookback_days,
)
from quantpilot.engine.strategies.money_flow import MoneyFlowStrategy

_COLS = [
    "ts_code", "trade_date", "net_mf_amount",
    "buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount", "amount",
]


def _flow(rows: list[tuple[str, date, float]]) -> pd.DataFrame:
    """rows: (ts_code, trade_date, main_net)；amount 固定 1e8，买卖拆成能凑出 main_net 的两列。"""
    out = []
    for code, td, net in rows:
        out.append({
            "ts_code": code, "trade_date": td, "net_mf_amount": net,
            "buy_elg_amount": max(net, 0.0), "sell_elg_amount": max(-net, 0.0),
            "buy_lg_amount": 0.0, "sell_lg_amount": 0.0, "amount": 1e8,
        })
    return pd.DataFrame(out, columns=_COLS)


def _bdays(n: int, end: date) -> list[date]:
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


class TestSliceMatchesProductionWindow:
    def test_excludes_future_rows(self) -> None:
        """上界：`> td` 的行必须不可见——否则 `tail(N)` 就是前视。"""
        td = date(2026, 8, 25)
        flow = _flow([("A", td, 1.0), ("A", td + timedelta(days=1), 999.0)])
        got = _money_flow_at(flow, td, 40)
        assert list(pd.to_datetime(got["trade_date"]).dt.date) == [td]

    def test_applies_lower_bound(self) -> None:
        """下界：窗口外的老行不可见（生产那条 SQL 有 `>= td - lookback`）。"""
        td = date(2026, 8, 25)
        flow = _flow([("A", td - timedelta(days=41), 1.0), ("A", td - timedelta(days=39), 2.0)])
        got = _money_flow_at(flow, td, 40)
        assert list(pd.to_datetime(got["trade_date"]).dt.date) == [td - timedelta(days=39)]

    def test_boundaries_are_inclusive(self) -> None:
        """两端闭区间，与 SQL 的 `>= start` / `<= end` 一致。"""
        td = date(2026, 8, 25)
        flow = _flow([("A", td - timedelta(days=40), 1.0), ("A", td, 2.0)])
        assert len(_money_flow_at(flow, td, 40)) == 2

    def test_empty_inputs(self) -> None:
        assert _money_flow_at(pd.DataFrame(), date(2026, 8, 25), 40).empty
        assert _money_flow_at(None, date(2026, 8, 25), 40).empty


class TestStrategyGetsRealValuesThroughTheSlice:
    def test_twenty_rows_gives_both_factors(self) -> None:
        """20 个交易日、主力净流入恒 +1e6/日、成交额 1e8/日 → 两因子都 = 0.01。"""
        days = _bdays(20, date(2026, 8, 25))
        flow = _flow([("A", d, 1e6) for d in days])
        snap = {"trade_date": days[-1], "money_flow": _money_flow_at(flow, days[-1], 40)}
        out = MoneyFlowStrategy().compute_raw_factors(pd.Index(["A"]), snap)  # type: ignore[arg-type]
        assert out.loc["A", "main_net_inflow_5d"] == 0.01
        assert out.loc["A", "main_net_inflow_20d"] == 0.01

    def test_stale_stock_is_nan_not_stale_value(self) -> None:
        """长期停牌：最近 20 行全在 40 天窗口之外 → 必须 NaN（与生产一致），不是老数据。"""
        td = date(2026, 8, 25)
        old_days = _bdays(20, td - timedelta(days=60))
        flow = _flow([("A", d, 1e6) for d in old_days])
        snap = {"trade_date": td, "money_flow": _money_flow_at(flow, td, 40)}
        out = MoneyFlowStrategy().compute_raw_factors(pd.Index(["A"]), snap)  # type: ignore[arg-type]
        assert out.loc["A"].isna().all()
        # 反证：不设下界（lookback 极大）就会拿到值——说明这条断言真在测下界
        snap_no_lb = {"trade_date": td, "money_flow": _money_flow_at(flow, td, 3650)}
        out2 = MoneyFlowStrategy().compute_raw_factors(pd.Index(["A"]), snap_no_lb)  # type: ignore[arg-type]
        assert out2.loc["A", "main_net_inflow_20d"] == 0.01

    def test_fewer_than_window_rows_is_nan(self) -> None:
        days = _bdays(4, date(2026, 8, 25))
        flow = _flow([("A", d, 1e6) for d in days])
        snap = {"trade_date": days[-1], "money_flow": _money_flow_at(flow, days[-1], 40)}
        out = MoneyFlowStrategy().compute_raw_factors(pd.Index(["A"]), snap)  # type: ignore[arg-type]
        assert out.loc["A"].isna().all()


class TestLookbackResolvedFromStrategy:
    def test_reads_strategy_config(self) -> None:
        s = MoneyFlowStrategy(MoneyFlowStrategyConfig(lookback_calendar_days=77))
        assert resolve_money_flow_lookback_days([s]) == 77

    def test_falls_back_to_default(self) -> None:
        assert resolve_money_flow_lookback_days(None) == (
            DEFAULT_MONEY_FLOW_STRATEGY.lookback_calendar_days
        )
        assert resolve_money_flow_lookback_days([object()]) == (
            DEFAULT_MONEY_FLOW_STRATEGY.lookback_calendar_days
        )


class TestCallSites:
    """§4.11：只在调用点上验——bundle 有字段、引擎把它放进快照、Service 真去取。"""

    def test_bundle_has_money_flow_field(self) -> None:
        assert "money_flow" in BacktestDataBundle.__dataclass_fields__
        # 缺省空表 → 旧行为（策略全 NaN 被跳过），旧 bundle / 单测替身不受影响
        empty = pd.DataFrame()
        bundle = BacktestDataBundle(
            adj_prices=empty, stock_info=empty, financials=empty, hs300_history=empty,
        )
        assert bundle.money_flow.empty

    def test_engine_run_puts_money_flow_into_snapshot(self) -> None:
        src = inspect.getsource(BacktestEngine.run)
        assert '"money_flow": _money_flow_at(' in src, "取了没放进快照 = 策略永远全 NaN"
        called = {
            (getattr(n.func, "attr", None) or getattr(n.func, "id", None))
            for n in ast.walk(ast.parse(src.lstrip())) if isinstance(n, ast.Call)
        }
        assert "resolve_money_flow_lookback_days" in called, "下界写死 = 与生产配置脱钩"

    def test_service_loads_money_flow_with_production_query(self) -> None:
        from quantpilot.services.backtest_service import BacktestService

        src = inspect.getsource(BacktestService._load_data_bundle)
        called = {
            (getattr(n.func, "attr", None) or getattr(n.func, "id", None))
            for n in ast.walk(ast.parse(src.lstrip())) if isinstance(n, ast.Call)
        }
        assert "get_money_flow_window" in called, (
            "回测另写一份取数 SQL = 与生产口径分叉（L-FID 要消除的就是这个）"
        )
        assert "money_flow=money_flow" in src


def test_engine_slice_keeps_sort_order() -> None:
    """策略靠 `tail(N)` 取最近 N 行 → 切片必须保持 (ts_code, trade_date) 升序。"""
    td = date(2026, 8, 25)
    days = _bdays(6, td)
    rows = [("A", d, float(i)) for i, d in enumerate(days)]
    shuffled = _flow(rows).sample(frac=1.0, random_state=1)
    ordered = shuffled.sort_values(["ts_code", "trade_date"], kind="mergesort")
    got = _money_flow_at(ordered, td, 40)
    assert list(pd.to_datetime(got["trade_date"]).dt.date) == days
