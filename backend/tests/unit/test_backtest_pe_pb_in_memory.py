"""回测 PE/PB 分位改在内存里算（2026-09-21）——与 SQL 版 / Python 参考实现逐股相等。

背景：`_load_data_bundle` 此前逐交易日调 `get_pe_pb_percentile_bulk`（两列各一次 SQL，每日约
4 s，6 日回测第一大耗时项，且合并两列 / 覆盖索引实测都不更快）。改为把五年窗口的
(ts_code, publish_date, pe_ttm, pb) 以紧凑数组（24 B/行 ≈ 155 MB）载入内存，
`pe_pb_percentile_in_memory` 一次窗口掩码 + 两次 bincount 算完。

判据与 `tests/integration/test_int_pe_pb_percentile_pushdown.py` 同一套：逐股对照
`value._compute_historical_percentile`（严格 `<` / 分母非 NULL / `1 - pct` / 无历史 NaN /
逐股独立 / 窗口闭区间），外加随机面板多 seed；真实数据（5434 六个交易日）与 SQL 的
逐码比对记录在 `backtest_service.pe_pb_percentile_in_memory` docstring。
"""
from __future__ import annotations

import ast
import inspect
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from quantpilot.engine.strategies.value import _compute_historical_percentile
from quantpilot.services.backtest_service import (
    PePbHistoryArrays,
    pe_pb_percentile_in_memory,
)

_START = date(2026, 1, 1)
_END = date(2026, 6, 30)


def _arrays(rows: list[tuple[str, date, float | None, float | None]]) -> PePbHistoryArrays:
    """rows: (ts_code, publish_date, pe_ttm, pb)，乱序也行。"""
    codes = sorted({r[0] for r in rows})
    pos = {c: i for i, c in enumerate(codes)}
    return PePbHistoryArrays(
        code_labels=np.array(codes, dtype=object),
        code_idx=np.array([pos[r[0]] for r in rows], dtype=np.int32),
        day_ord=np.array([r[1].toordinal() for r in rows], dtype=np.int32),
        values={
            "pe_ttm": np.array([np.nan if r[2] is None else r[2] for r in rows], dtype=float),
            "pb": np.array([np.nan if r[3] is None else r[3] for r in rows], dtype=float),
        },
    )


def _reference(rows, current: dict[str, float], col: str, start: date, end: date) -> pd.Series:
    tuples = [(r[0], r[1]) for r in rows if start <= r[1] <= end]
    vals = [r[2] if col == "pe_ttm" else r[3] for r in rows if start <= r[1] <= end]
    mi = pd.MultiIndex.from_tuples(tuples, names=["ts_code", "trade_date"])
    df = pd.DataFrame({col: vals}, index=mi)
    return _compute_historical_percentile(
        pd.Index(list(current)), pd.Series(current), df, col, inverse=True
    )


def _assert_same(got: pd.Series, ref: pd.Series) -> None:
    assert list(got.index) == list(ref.index)
    for k in ref.index:
        if pd.isna(ref[k]):
            assert pd.isna(got[k]), f"{k}: 应为 NaN，得 {got[k]}"
        else:
            assert got[k] == pytest.approx(ref[k], abs=1e-12), f"{k}: {got[k]} vs {ref[k]}"


class TestSemanticsAgainstPythonReference:
    ROWS = [
        ("A", _START, 5.0, 2.5), ("A", _START + timedelta(1), 10.0, 5.0),
        ("A", _START + timedelta(2), 15.0, 7.5),                      # 当前值等于 10 → 严格 <
        ("B", _START, 5.0, 2.5), ("B", _START + timedelta(1), None, None),
        ("B", _START + timedelta(2), 15.0, 7.5), ("B", _START + timedelta(3), None, None),
        ("C", _START, 10.0, 5.0), ("C", _START + timedelta(1), 20.0, 10.0),
        ("C", _START + timedelta(2), 30.0, 15.0),                     # 低于全部 → 1.0
        ("D", _START - timedelta(10), 1.0, 1.0),                      # 窗口外 → 无历史
    ]

    @pytest.mark.parametrize("col", ["pe_ttm", "pb"])
    def test_matches_reference(self, col: str) -> None:
        scale = 1.0 if col == "pe_ttm" else 0.5
        cur = {"A": 10.0 * scale, "B": 10.0 * scale, "C": 1.0 * scale, "D": 3.0, "E": 4.0}
        got = pe_pb_percentile_in_memory(_arrays(self.ROWS), cur, _START, _END, col)
        _assert_same(got, _reference(self.ROWS, cur, col, _START, _END))
        assert got["A"] == pytest.approx(2.0 / 3.0)
        assert got["B"] == pytest.approx(0.5)
        assert got["C"] == pytest.approx(1.0)
        assert pd.isna(got["D"]) and pd.isna(got["E"])

    def test_current_nan_or_none_is_nan_not_zero(self) -> None:
        got = pe_pb_percentile_in_memory(
            _arrays(self.ROWS), {"A": float("nan"), "B": None, "C": 1.0}, _START, _END, "pe_ttm"
        )
        assert pd.isna(got["A"]) and pd.isna(got["B"]) and got["C"] == 1.0

    def test_window_is_closed_interval(self) -> None:
        rows = [
            ("A", _START, 1.0, 1.0), ("A", _END, 100.0, 100.0),
            ("A", _END + timedelta(1), 200.0, 200.0),
        ]
        got = pe_pb_percentile_in_memory(_arrays(rows), {"A": 150.0}, _START, _END, "pe_ttm")
        assert got["A"] == pytest.approx(0.0)  # [1, 100] 都小于 150；200 在窗口外不计
        got2 = pe_pb_percentile_in_memory(
            _arrays(rows), {"A": 150.0}, _START + timedelta(1), _END - timedelta(1), "pe_ttm"
        )
        assert pd.isna(got2["A"])

    def test_empty_history(self) -> None:
        empty = PePbHistoryArrays(
            code_labels=np.array([], dtype=object), code_idx=np.array([], dtype=np.int32),
            day_ord=np.array([], dtype=np.int32),
            values={"pe_ttm": np.array([], dtype=float), "pb": np.array([], dtype=float)},
        )
        got = pe_pb_percentile_in_memory(empty, {"A": 1.0}, _START, _END, "pe_ttm")
        assert list(got.index) == ["A"] and pd.isna(got["A"])


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
@pytest.mark.parametrize("col", ["pe_ttm", "pb"])
def test_random_panel_equals_reference(seed: int, col: str) -> None:
    rng = np.random.default_rng(seed)
    rows = []
    codes = [f"{i:06d}.SZ" for i in range(80)]
    for c in codes:
        n = int(rng.integers(0, 40))
        for _ in range(n):
            d = _START + timedelta(days=int(rng.integers(-30, 200)))
            pe = (None if rng.random() < 0.2
                  else float(rng.choice([5.0, 10.0, 15.0, rng.normal(10, 5)])))
            pb = None if rng.random() < 0.2 else float(rng.normal(2, 1))
            rows.append((c, d, pe, pb))
    rng.shuffle(rows)
    cur = {c: (float("nan") if rng.random() < 0.1 else float(rng.choice([10.0, rng.normal(10, 5)])))
           for c in codes + ["ZZZ.SZ"]}
    got = pe_pb_percentile_in_memory(_arrays(rows), cur, _START, _END, col)
    ref = _reference(rows, cur, col, _START, _END)
    _assert_same(got, ref)
    assert ref.notna().sum() > 20 and ref.isna().sum() > 0


class TestServiceUsesInMemoryPath:
    def test_load_bundle_calls_in_memory_not_sql_per_day(self) -> None:
        from quantpilot.services.backtest_service import BacktestService

        src = inspect.getsource(BacktestService._load_data_bundle)
        called = {
            (getattr(n.func, "attr", None) or getattr(n.func, "id", None))
            for n in ast.walk(ast.parse(src.lstrip())) if isinstance(n, ast.Call)
        }
        assert "pe_pb_percentile_in_memory" in called
        assert "get_pe_pb_percentile_bulk" not in called, "回测又回到逐日 SQL 分位（每日 4 s）"
        assert "_load_pe_pb_history_arrays" in called
