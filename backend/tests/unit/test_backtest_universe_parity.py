"""L-FID：回测的 universe 取数与生产同口径（用户 2026-09-17 拍板 6-A）。

此前 `BacktestEngine` 调 `UniverseFilter.filter()` **不传** `financials_history`、快照里也没有
`avg_amount` → F-5 走「单期为负即剔」降级分支、F-7 用单日成交额；生产是「最近 2 个有值期皆负
才剔」与 20 日均成交额。2026-08-25 实测回测 universe 比生产少约 977 只。原先用三条测试把分歧
钉成显式事实（`test_universe_f5.py::TestBacktestDivergenceIsExplicit`）；本文件把它们换成
「钉等价」：引擎在内存里复现生产两个 repo 方法的语义——

- `get_latest_n_financials(n=4)`：`publish_date <= td`，每 (ts_code, report_period) 只留
  publish_date 最新一行，再按 report_period 倒序取前 4
- `get_avg_amount(window=20)`：`trade_date < td` 的最近 20 个交易日 `AVG(amount)`（不含当日；
  不足 20 日取现有均值；一行都没有 → 缺失）
"""
from __future__ import annotations

import ast
import inspect
from datetime import date

import numpy as np
import pandas as pd

from quantpilot.engine.backtest.engine import (
    BacktestEngine,
    _avg_amount_before,
    _financials_history_at,
)


def _fin(rows: list[tuple[str, date, date, float | None]]) -> pd.DataFrame:
    """rows: (ts_code, report_period, publish_date, net_profit_yoy) → bundle.financials 形状。"""
    df = pd.DataFrame(rows, columns=["ts_code", "report_period", "publish_date", "net_profit_yoy"])
    return df.set_index(["ts_code", "report_period"])


class TestFinancialsHistoryAt:
    def test_pit_dedupe_and_top_n_periods(self) -> None:
        """同期多行只留 publish 最新；未来 publish 排除；按报告期倒序取前 4。"""
        td = date(2025, 11, 15)
        fin = _fin([
            ("A", date(2025, 9, 30), date(2025, 10, 28), 0.1),   # Q3 首次
            ("A", date(2025, 9, 30), date(2025, 11, 10), 0.2),   # Q3 更正（更新的 publish）→ 取这行
            ("A", date(2025, 12, 31), date(2026, 1, 20), 0.9),   # 未来 → 排除
            ("A", date(2025, 6, 30), date(2025, 8, 20), -0.1),
            ("A", date(2025, 3, 31), date(2025, 4, 25), -0.2),
            ("A", date(2024, 12, 31), date(2025, 4, 20), -0.3),
            ("A", date(2024, 9, 30), date(2024, 10, 25), -0.4),  # 第 5 期 → 截掉
        ])
        hist = _financials_history_at(fin, td, n=4)
        a = hist.xs("A", level=0).sort_index(ascending=False)
        assert list(a.index) == [
            date(2025, 9, 30), date(2025, 6, 30), date(2025, 3, 31), date(2024, 12, 31),
        ]
        assert a.loc[date(2025, 9, 30), "net_profit_yoy"] == 0.2
        assert isinstance(hist.index, pd.MultiIndex)
        assert hist.index.names == ["ts_code", "report_period"]

    def test_empty_when_nothing_published_yet(self) -> None:
        fin = _fin([("A", date(2025, 9, 30), date(2025, 10, 28), 0.1)])
        assert _financials_history_at(fin, date(2025, 10, 1), n=4).empty


class TestAvgAmountBefore:
    def test_window_excludes_today_and_uses_last_20(self) -> None:
        days = pd.bdate_range("2025-01-01", periods=30)
        wide = pd.DataFrame(
            {"A": np.arange(30, dtype=float) + 1, "B": [np.nan] * 25 + [5.0] * 5},
            index=days,
        )
        td = days[29].date()
        got = _avg_amount_before(wide, td, window=20)
        # A：不含当日（30），取 10..29 的均值 = 19.5
        assert got["A"] == 19.5
        # B：窗口内只有 4 个非空值（第 26~29 天）→ SQL AVG 忽略 NULL → 5.0
        assert got["B"] == 5.0

    def test_first_day_has_no_history(self) -> None:
        days = pd.bdate_range("2025-01-01", periods=5)
        wide = pd.DataFrame({"A": [1.0] * 5}, index=days)
        got = _avg_amount_before(wide, days[0].date(), window=20)
        assert pd.isna(got["A"])


class TestEnginePassesProductionInputs:
    """调用点：`filter()` 必须收到 `financials_history`，快照行情必须带 `avg_amount`（AST 钉）。"""

    def test_filter_call_passes_financials_history(self) -> None:
        src = inspect.getsource(BacktestEngine.run)
        kwargs = {
            kw.arg
            for n in ast.walk(ast.parse(src.lstrip()))
            if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "filter"
            for kw in n.keywords
        }
        assert "financials_history" in kwargs, "回测 F-5 仍走降级分支（与生产不同口径）"

    def test_quotes_snapshot_carries_avg_amount(self) -> None:
        src = inspect.getsource(BacktestEngine.run)
        assert '"avg_amount"' in src and "_avg_amount_before" in src, (
            "回测 F-7 仍用单日成交额（生产是 20 日均量）"
        )
