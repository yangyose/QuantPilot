"""V1.5-A A2（SDD-EXT-02s）：回测涨停成交可行性精细化。

现状（B3-1）：`_execute_signals` 对 BUY 一律跳过 limit_up（过度保守）。
SDD-EXT-02s 简化规则：仅当「收盘涨停 AND 换手率 < 1%（无量一字板）」判定不可成交；
涨停但有量（turnover_rate ≥ 0.01，盘中打开过）→ 可成交。turnover_rate 入库为**小数**
（adapter `/100`），故阈值 0.01。turnover_rate NULL → 保守视为无量（跳过 BUY）。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd

from quantpilot.engine.backtest.engine import _execute_signals

_CONFIG = SimpleNamespace(
    initial_capital=1_000_000.0,
    commission_rate=0.0003,
    slippage_rate=0.001,
    stamp_tax_rate=0.001,
)


def _buy_signal(ts_code: str = "000001.SZ") -> SimpleNamespace:
    return SimpleNamespace(ts_code=ts_code, signal_type="BUY", suggested_pct=0.10)


def _quotes(limit_up: bool, turnover_rate: float | None) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close": [10.0],
            "limit_up": [limit_up],
            "is_suspended": [False],
            "turnover_rate": [turnover_rate],
        },
        index=pd.Index(["000001.SZ"], name="ts_code"),
    )


def test_a2_limit_up_illiquid_buy_skipped() -> None:
    """涨停 + 换手率 < 1%（无量一字板）→ BUY 不可成交（无交易记录、持仓不变）。"""
    records, cash, positions = _execute_signals(
        [_buy_signal()], {}, _quotes(limit_up=True, turnover_rate=0.005),
        _CONFIG.initial_capital, _CONFIG, date(2026, 5, 20),
    )
    assert records == []
    assert positions == {}
    assert cash == _CONFIG.initial_capital


def test_a2_limit_up_liquid_buy_executed() -> None:
    """涨停但有量（换手率 ≥ 1%，盘中打开）→ BUY 可成交。"""
    records, cash, positions = _execute_signals(
        [_buy_signal()], {}, _quotes(limit_up=True, turnover_rate=0.02),
        _CONFIG.initial_capital, _CONFIG, date(2026, 5, 20),
    )
    assert len(records) == 1
    assert records[0]["signal_type"] == "BUY"
    assert "000001.SZ" in positions
    assert cash < _CONFIG.initial_capital


def test_a2_non_limit_up_buy_executed() -> None:
    """非涨停 → BUY 正常成交（不受 turnover 影响）。"""
    records, _, positions = _execute_signals(
        [_buy_signal()], {}, _quotes(limit_up=False, turnover_rate=0.001),
        _CONFIG.initial_capital, _CONFIG, date(2026, 5, 20),
    )
    assert len(records) == 1
    assert "000001.SZ" in positions


def test_a2_limit_up_turnover_null_conservative_skip() -> None:
    """涨停 + turnover_rate 缺失（NULL）→ 保守视为无量，跳过 BUY。"""
    records, cash, positions = _execute_signals(
        [_buy_signal()], {}, _quotes(limit_up=True, turnover_rate=None),
        _CONFIG.initial_capital, _CONFIG, date(2026, 5, 20),
    )
    assert records == []
    assert positions == {}


def test_a2_limit_up_no_turnover_column_conservative_skip() -> None:
    """涨停 + quotes 无 turnover_rate 列（旧 bundle 降级）→ 保守跳过 BUY（不回退到成交）。"""
    quotes = pd.DataFrame(
        {"close": [10.0], "limit_up": [True], "is_suspended": [False]},
        index=pd.Index(["000001.SZ"], name="ts_code"),
    )
    records, _, positions = _execute_signals(
        [_buy_signal()], {}, quotes,
        _CONFIG.initial_capital, _CONFIG, date(2026, 5, 20),
    )
    assert records == []
    assert positions == {}
