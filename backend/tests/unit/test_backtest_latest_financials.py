"""回测 PIT 财务快照 `_latest_financials_at` ≡ 生产 `get_latest_financial`（2026-09-22）。

## 缺陷

`BacktestEngine._get_financials_at` 原用 `groupby(level=0).last()` 取「最新一期」——那取的是
**帧内顺序**的末行（逐列末个非空），而 bundle 的 SELECT 没有 ORDER BY。2026-09-08
`repair_financial_lookahead` 改写 318 万行后堆序被打乱：5434 实测 5554/5665 只股票行序非
时间序，回测拿到的「最新」publish_date 与真值不同的占 **97%**（pe_ttm 不同 95%）。
生产走确定性 SQL，不受影响；自 09-08 起的所有回测（含 09-17 L-FID 对齐后的）都受影响。
这是 L-FID 一族：回测与生产取数路径不同，且**只在数据物理顺序变化后才显形**。

## 判据

样本与 `tests/integration/test_data_repository.py` 的 03b/03c/03d/03h/03i/03j **逐字相同**，
断言也相同——那边钉 SQL，这边钉内存实现，两边合起来才是「同语义」。另加：
**行序打乱后结果必须不变**（缺陷本体）、回看常量与 repository 相等。
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from quantpilot.engine.backtest import engine as bt_engine
from quantpilot.engine.backtest.engine import (
    _financials_history_at,
    _latest_financials_at,
    _prepare_financials,
)

_COLS = [
    "ts_code", "report_period", "publish_date", "pe_ttm", "pb", "roe",
    "net_profit_yoy", "revenue_yoy", "dividend_yield", "total_equity", "debt_to_asset",
]


def _fin_row(
    ts_code, report_period, publish_date, *,
    pe=float("nan"), pb=float("nan"), roe=float("nan"),
    teq=float("nan"), npyoy=float("nan"),
) -> dict:
    return {
        "ts_code": ts_code, "report_period": report_period, "publish_date": publish_date,
        "pe_ttm": pe, "pb": pb, "roe": roe, "net_profit_yoy": npyoy,
        "revenue_yoy": float("nan"), "dividend_yield": float("nan"),
        "total_equity": teq, "debt_to_asset": float("nan"),
    }


def _bundle_frame(rows: list[dict], seed: int | None = None) -> pd.DataFrame:
    """bundle 形态：MultiIndex(ts_code, report_period)；seed 非 None 时打乱行序。"""
    df = pd.DataFrame(rows, columns=_COLS)
    if seed is not None:
        df = df.sample(frac=1.0, random_state=seed)
    return df.set_index(["ts_code", "report_period"])


@pytest.mark.parametrize("seed", [None, 1, 2])
class TestMatchesRepositoryCases:
    def test_03b_locf_vacuum(self, seed) -> None:
        q1, q2 = date(2025, 3, 31), date(2025, 6, 30)
        rows = [
            _fin_row("000001.SZ", q1, date(2025, 4, 30),
                     pe=10.0, pb=1.0, roe=0.15, teq=1e10, npyoy=0.2),
            _fin_row("000001.SZ", q1, date(2025, 6, 27),
                     pe=10.5, pb=1.05, roe=0.15, teq=1e10, npyoy=0.2),
            _fin_row("000001.SZ", q2, date(2025, 7, 31), pe=11.0, pb=1.1),
            _fin_row("000001.SZ", q2, date(2025, 8, 29), pe=11.5, pb=1.15),
        ]
        res = _latest_financials_at(_bundle_frame(rows, seed), date(2025, 9, 1))
        assert len(res) == 1
        r = res.loc["000001.SZ"]
        assert r["publish_date"] == date(2025, 8, 29)
        assert float(r["pe_ttm"]) == 11.5
        assert r["report_period"] == q1
        assert float(r["roe"]) == pytest.approx(0.15)
        assert float(r["total_equity"]) == pytest.approx(1e10)

    def test_03c_merges_split_rows(self, seed) -> None:
        q2 = date(2025, 6, 30)
        rows = [
            _fin_row("000002.SZ", q2, date(2025, 8, 15), pe=12.0, pb=1.2, roe=0.18, npyoy=0.25),
            _fin_row("000002.SZ", q2, date(2025, 8, 10), teq=3e10),
        ]
        r = _latest_financials_at(_bundle_frame(rows, seed), date(2025, 9, 1)).loc["000002.SZ"]
        assert r["report_period"] == q2
        assert float(r["roe"]) == pytest.approx(0.18)
        assert float(r["total_equity"]) == pytest.approx(3e10)
        assert r["publish_date"] == date(2025, 8, 15)
        assert float(r["pe_ttm"]) == 12.0

    def test_03d_beyond_lookback(self, seed) -> None:
        rows = [
            _fin_row("000003.SZ", date(2022, 12, 31), date(2023, 1, 1),
                     pe=9.0, pb=0.9, roe=0.1, teq=5e9, npyoy=0.05),
            _fin_row("000003.SZ", date(2026, 6, 30), date(2026, 8, 3), pe=9.5, pb=0.95),
        ]
        r = _latest_financials_at(_bundle_frame(rows, seed), date(2026, 8, 5)).loc["000003.SZ"]
        assert r["publish_date"] == date(2026, 8, 3)
        assert float(r["pe_ttm"]) == 9.5
        assert pd.isna(r["report_period"])
        assert pd.isna(r["roe"])
        assert pd.isna(r["total_equity"])

    def test_03h_total_equity_survives_dense_new_period(self, seed) -> None:
        q1, q2 = date(2025, 3, 31), date(2025, 6, 30)
        rows = [
            _fin_row("000011.SZ", q1, date(2025, 4, 30),
                     pe=10.0, pb=1.0, roe=0.15, teq=1e10, npyoy=0.2),
            _fin_row("000011.SZ", q2, date(2025, 7, 1), pe=11.0, pb=1.1, roe=0.15, npyoy=0.2),
        ]
        r = _latest_financials_at(_bundle_frame(rows, seed), date(2025, 7, 1)).loc["000011.SZ"]
        assert r["report_period"] == q2
        assert float(r["roe"]) == pytest.approx(0.15)
        assert float(r["total_equity"]) == pytest.approx(1e10)

    def test_03i_prefers_newest_period_with_value(self, seed) -> None:
        q1, q2 = date(2025, 3, 31), date(2025, 6, 30)
        rows = [
            _fin_row("000012.SZ", q1, date(2025, 4, 30),
                     pe=10.0, pb=1.0, roe=0.15, teq=1e10, npyoy=0.2),
            _fin_row("000012.SZ", q2, date(2025, 8, 28),
                     pe=11.0, pb=1.1, roe=0.18, teq=2e10, npyoy=0.3),
        ]
        r = _latest_financials_at(_bundle_frame(rows, seed), date(2025, 9, 1)).loc["000012.SZ"]
        assert r["report_period"] == q2
        assert float(r["total_equity"]) == pytest.approx(2e10)

    def test_03j_takes_newest_period_that_has_it(self, seed) -> None:
        q1, q2, q3 = date(2024, 12, 31), date(2025, 3, 31), date(2025, 6, 30)
        rows = [
            _fin_row("000013.SZ", q1, date(2025, 1, 31),
                     pe=9.0, pb=0.9, roe=0.10, teq=1e10, npyoy=0.1),
            _fin_row("000013.SZ", q2, date(2025, 4, 30),
                     pe=10.0, pb=1.0, roe=0.15, teq=2e10, npyoy=0.2),
            _fin_row("000013.SZ", q3, date(2025, 7, 1), pe=11.0, pb=1.1, roe=0.18, npyoy=0.3),
        ]
        r = _latest_financials_at(_bundle_frame(rows, seed), date(2025, 7, 1)).loc["000013.SZ"]
        assert r["report_period"] == q3
        assert float(r["total_equity"]) == pytest.approx(2e10)


class TestOrderIndependenceIsTheDefect:
    """缺陷本体：同一批行，倒序喂进去，旧实现给出不同的 publish_date / pe；新实现必须相同。"""

    @staticmethod
    def _rows() -> list[dict]:
        rows = []
        rng = np.random.default_rng(0)
        for i in range(40):
            code = f"{i:06d}.SZ"
            for k, (rp, pub) in enumerate([
                (date(2025, 3, 31), date(2025, 4, 30)), (date(2025, 3, 31), date(2025, 6, 27)),
                (date(2025, 6, 30), date(2025, 7, 31)), (date(2025, 6, 30), date(2025, 8, 29)),
            ]):
                rows.append(_fin_row(code, rp, pub, pe=10.0 + k + rng.random(), pb=1.0 + k,
                                     roe=0.1 + 0.01 * k, teq=1e10 * (k + 1), npyoy=0.1 * k))
        return rows

    def test_shuffled_input_gives_identical_snapshot(self) -> None:
        rows = self._rows()
        a = _latest_financials_at(_bundle_frame(rows), date(2025, 9, 1))
        b = _latest_financials_at(_bundle_frame(rows, seed=7), date(2025, 9, 1))
        c = _latest_financials_at(_bundle_frame(rows[::-1]), date(2025, 9, 1))
        pd.testing.assert_frame_equal(a.sort_index(), b.sort_index())
        pd.testing.assert_frame_equal(a.sort_index(), c.sort_index())
        assert (a["publish_date"] == date(2025, 8, 29)).all()

    def test_old_groupby_last_was_order_dependent(self) -> None:
        """证伪用：旧写法在倒序输入上确实给出不同结果（否则本文件在守一个不存在的缺陷）。"""
        rows = self._rows()
        fwd = _bundle_frame(rows).groupby(level=0).last()
        rev = _bundle_frame(rows[::-1]).groupby(level=0).last()
        assert not fwd["publish_date"].equals(rev["publish_date"])


class TestPreparedFrameFastPath:
    def test_prepare_is_idempotent_and_history_matches_raw_path(self) -> None:
        rows = TestOrderIndependenceIsTheDefect._rows()
        raw = _bundle_frame(rows, seed=3)
        flat = _prepare_financials(raw)
        assert _prepare_financials(flat) is flat
        td = date(2025, 8, 1)
        pd.testing.assert_frame_equal(
            _latest_financials_at(raw, td).sort_index(),
            _latest_financials_at(flat, td).sort_index(),
        )
        h_raw = _financials_history_at(raw, td, n=4).sort_index()
        h_flat = _financials_history_at(flat, td, n=4).sort_index()
        pd.testing.assert_frame_equal(
            h_raw.drop(columns=[c for c in h_raw.columns if c.startswith("_")]),
            h_flat.drop(columns=[c for c in h_flat.columns if c.startswith("_")]),
        )

    def test_null_publish_date_rows_are_invisible(self) -> None:
        rows = [_fin_row("A", date(2025, 3, 31), None, pe=1.0),
                _fin_row("A", date(2025, 3, 31), date(2025, 4, 30), pe=2.0)]
        r = _latest_financials_at(_bundle_frame(rows), date(2025, 9, 1)).loc["A"]
        assert float(r["pe_ttm"]) == 2.0


def test_lookback_constant_equals_repository() -> None:
    from quantpilot.data import repository

    assert bt_engine._FUND_LOOKBACK_DAYS == repository._FUND_LOOKBACK_DAYS


def test_engine_run_prepares_financials_once() -> None:
    import ast
    import inspect

    from quantpilot.engine.backtest.engine import BacktestEngine

    src = inspect.getsource(BacktestEngine.run)
    called = [
        n.func.id for n in ast.walk(ast.parse(src.lstrip()))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert called.count("_prepare_financials") == 1
    assert "data.financials" not in src.split("_prepare_financials(data.financials)")[1], (
        "主循环里仍有直接用 data.financials 的地方——那是未排序的原帧"
    )
