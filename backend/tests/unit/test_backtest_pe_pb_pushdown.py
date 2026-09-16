"""回测引擎 PE/PB 分位下推（2026-09-16）。

背景：每日管线 2026-09-04（b76f3d0）把 PE/PB 历史分位下推到 PostgreSQL，该项峰值
2313 MB → 1.5 MB；**回测引擎没跟**——`BacktestService._load_data_bundle` 仍把
[start−400d, end] 的 `financial_data` 切片整个 `.all()` 成 SQLAlchemy Row（含 Decimal）
再派生 `pe_pb_history`（6 交易日回测 ≈ 150 万行），2026-09-14 实测峰值工作集 **3530 MB**，
这是回测 503 不能放开的唯一前置（`docs/reviews/memory_premise_after_4gb_2026-09-14.md` §2②）。

改法（与生产同一条路）：Service 逐交易日 `get_latest_financial` → 当前 pe/pb →
`get_pe_pb_percentile_bulk`（5 年窗口），结果按日期放进 bundle；引擎把当日的
`pe_percentile` / `pb_percentile` 放进 `MarketSnapshot`，`ValueStrategy` 优先消费它们、
`pe_pb_history` 留空。顺带修掉一个既有的口径不一致：回测原来的分位窗口只有 ~400 天，
生产是 5 年。

判据按 §4.11：引擎侧用捕获快照的替身策略在**调用点**验；Service 侧用 AST 钉调用点
（替身测试是自证式的）。
"""
from __future__ import annotations

import ast
import inspect
from datetime import date

import pandas as pd
import pytest

from quantpilot.engine.backtest.engine import BacktestConfig, BacktestDataBundle, BacktestEngine
from tests.unit.test_backtest_engine_phase14_3 import (  # noqa: F401  (fixtures 按名导入)
    _build_data_bundle,
    _CapturingScorer,
    _NoopPositionEngine,
    _NoopSignalEngine,
    _stub_universe_filter,
    stub_calendar,
    stub_market_state_engine,
)


class _SnapshotCapturingStrategy:
    name = "trend"

    def __init__(self) -> None:
        self.snaps: list[dict] = []

    def compute_strategy_factors(self, universe: pd.Index, snap: dict) -> pd.DataFrame:
        self.snaps.append(snap)
        return pd.DataFrame({"f": [0.5] * len(universe)}, index=universe)

    def score(self, universe: pd.Index, snap: dict) -> list:
        self.snaps.append(snap)
        return []


@pytest.fixture
def _engine_parts(stub_calendar: object, stub_market_state_engine: object):  # noqa: F811
    strategy = _SnapshotCapturingStrategy()
    engine = BacktestEngine(
        strategies=[strategy],
        market_state_engine=stub_market_state_engine,
        universe_filter=_stub_universe_filter(40),
        scorer=_CapturingScorer(),
        signal_engine=_NoopSignalEngine(),
        position_engine=_NoopPositionEngine(),
        price_provider=None,
        calendar=stub_calendar,
    )
    return engine, strategy


def _cfg() -> BacktestConfig:
    return BacktestConfig(
        start_date=date(2024, 6, 3), end_date=date(2024, 6, 3),
        initial_capital=1_000_000.0, strategy_config={}, account_config={},
    )


def test_bundle_has_percentile_by_date_fields() -> None:
    fields = BacktestDataBundle.__dataclass_fields__
    assert "pe_percentile_by_date" in fields and "pb_percentile_by_date" in fields
    empty = _build_data_bundle(3, with_weights_history=False)
    assert empty.pe_percentile_by_date == {} and empty.pb_percentile_by_date == {}


def test_engine_feeds_that_days_percentiles_into_snapshot(_engine_parts) -> None:
    """当日的分位 Series 必须原样进 MarketSnapshot（键名与生产 `_build_market_snapshot` 一致）。"""
    engine, strategy = _engine_parts
    data = _build_data_bundle(40, with_weights_history=True)
    td = date(2024, 6, 3)
    pe = pd.Series({c: 0.25 for c in data.stock_info.index}, name="pe_percentile")
    pb = pd.Series({c: 0.75 for c in data.stock_info.index}, name="pb_percentile")
    data.pe_percentile_by_date[td] = pe
    data.pb_percentile_by_date[td] = pb
    # 另一天的分位不该被误用
    data.pe_percentile_by_date[date(2024, 6, 4)] = pe * 0

    engine.run(_cfg(), data)

    assert strategy.snaps, "策略没被调用，测试前提不成立"
    snap = strategy.snaps[0]
    assert snap["pe_percentile"] is pe
    assert snap["pb_percentile"] is pb
    assert snap["pe_pb_history"].empty


def test_engine_without_percentiles_passes_none_not_missing_key(_engine_parts) -> None:
    """没预计算（旧 bundle）→ 键存在且为 None，ValueStrategy 回落历史路径而非 KeyError。"""
    engine, strategy = _engine_parts
    data = _build_data_bundle(40, with_weights_history=True)
    engine.run(_cfg(), data)
    snap = strategy.snaps[0]
    assert "pe_percentile" in snap and snap["pe_percentile"] is None
    assert "pb_percentile" in snap and snap["pb_percentile"] is None


class TestServiceActuallyPushesDown:
    @staticmethod
    def _src() -> str:
        from quantpilot.services.backtest_service import BacktestService

        return inspect.getsource(BacktestService._load_data_bundle)

    def test_service_calls_latest_financial_and_percentile_pushdown(self) -> None:
        tree = ast.parse(self._src().lstrip())
        called = {getattr(n.func, "attr", None) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert "get_latest_financial" in called, "当前 pe/pb 必须与生产同源（get_latest_financial）"
        assert "get_pe_pb_percentile_bulk" in called, "分位没有下推——峰值仍是 150 万行 Row"

    def test_service_no_longer_materializes_pe_pb_history_from_rows(self) -> None:
        src = self._src()
        assert 'set_index(["ts_code", "publish_date"])' not in src, (
            "pe_pb_history 仍从 fin_df 派生——整段 5 年历史又回到内存里"
        )
        assert "pe_percentile_by_date=" in src and "pb_percentile_by_date=" in src

    def test_service_streams_financial_rows_instead_of_all(self) -> None:
        """`.all()` 把 150 万行先实例化成 Row（含 Decimal）——峰值主项；必须流式分块转 float。"""
        # 去掉注释行再判——注释里提到 `.all()`（解释为什么不用它）不算「用了」
        code_only = "\n".join(
            ln for ln in self._src().splitlines() if not ln.lstrip().startswith("#")
        )
        seg = code_only.split("FinancialData.ts_code", 1)[1].split("financials = ", 1)[0]
        assert ".all()" not in seg, "financial_data 切片仍在用 .all() 一次性实例化"
        assert "stream(" in seg
