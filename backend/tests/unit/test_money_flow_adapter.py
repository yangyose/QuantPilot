"""MF-ADP-01~05: TushareAdapter.fetch_money_flow（V1.5-C C4 / 设计 §6.3）。

真调核对（2026-08-28 设计 §6.1 + 2026-09-16 本批）：
- `moneyflow(trade_date=...)` 全市场单日约 5548 行（不是整数，未被截断；接口上限 6000）
- 金额列单位**万元**（量级反推：600519.SH 净流入按万元解释占成交额 −7.56%，按元荒谬）
- 原始 20 列，本表只留 5 列金额（设计 §6.2 列裁剪）

用例按 CLAUDE.md §4.11「测试输入比现实更配合」的判据：mock 返回的列名与
dtype **照真实返回抄**（含本表不要的 vol/sm/md 列），不是按实现反推一个刚好匹配的样本。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from quantpilot.data.adapters.tushare import TushareAdapter

_REAL_COLUMNS = [
    "ts_code", "trade_date",
    "buy_sm_vol", "buy_sm_amount", "sell_sm_vol", "sell_sm_amount",
    "buy_md_vol", "buy_md_amount", "sell_md_vol", "sell_md_amount",
    "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount",
    "buy_elg_vol", "buy_elg_amount", "sell_elg_vol", "sell_elg_amount",
    "net_mf_vol", "net_mf_amount",
]

_OUT_COLUMNS = [
    "ts_code", "trade_date",
    "net_mf_amount", "buy_elg_amount", "sell_elg_amount", "buy_lg_amount", "sell_lg_amount",
]


@pytest.fixture
def adapter() -> TushareAdapter:
    with patch("quantpilot.data.adapters.tushare.ts") as mock_ts:
        mock_ts.pro_api.return_value = MagicMock()
        yield TushareAdapter(token="test-token")


def _real_shaped_df(rows: list[dict]) -> pd.DataFrame:
    """按真实返回的 20 列构造；未给的列填 0.0（真实数据无 NaN）。"""
    df = pd.DataFrame(rows)
    for c in _REAL_COLUMNS:
        if c not in df.columns:
            df[c] = 0.0
    return df[_REAL_COLUMNS]


async def _run(adapter: TushareAdapter, df: pd.DataFrame, trade_date: date):
    captured: list[dict] = []

    async def _capture_call(func, **kwargs):
        captured.append({"func": func, "kwargs": kwargs})
        return df

    with patch.object(adapter, "_call", new=_capture_call):
        out = await adapter.fetch_money_flow(trade_date)
    return out, captured


async def test_mf_adp_01_calls_moneyflow_by_trade_date(adapter: TushareAdapter) -> None:
    """调的是 `pro.moneyflow`，且按 trade_date 全市场取（不是逗号多码 / 日期窗口）。"""
    df = _real_shaped_df([
        {"ts_code": "000001.SZ", "trade_date": "20260915", "net_mf_amount": 6026.29},
    ])
    _out, captured = await _run(adapter, df, date(2026, 9, 15))
    assert len(captured) == 1
    assert captured[0]["func"] is adapter._pro.moneyflow
    assert captured[0]["kwargs"].get("trade_date") == "20260915"
    assert "ts_code" not in captured[0]["kwargs"]


async def test_mf_adp_02_wan_to_yuan_and_column_pruning(adapter: TushareAdapter) -> None:
    """万元 → 元（×1e4），只保留 5 个金额列，vol/sm/md 列不出现。"""
    df = _real_shaped_df([
        {
            "ts_code": "000001.SZ", "trade_date": "20260915",
            "net_mf_amount": 6026.29, "buy_elg_amount": 12373.78, "sell_elg_amount": 16828.37,
            "buy_lg_amount": 18231.43, "sell_lg_amount": 24614.34,
            "buy_sm_amount": 99.0, "net_mf_vol": 123.0,
        },
    ])
    out, _ = await _run(adapter, df, date(2026, 9, 15))
    assert list(out.columns) == _OUT_COLUMNS
    row = out.iloc[0]
    assert row["ts_code"] == "000001.SZ"
    assert row["trade_date"] == date(2026, 9, 15)
    assert row["net_mf_amount"] == pytest.approx(60_262_900.0)
    assert row["buy_elg_amount"] == pytest.approx(123_737_800.0)
    assert row["sell_lg_amount"] == pytest.approx(246_143_400.0)


async def test_mf_adp_03_empty_or_none_returns_empty_frame_with_columns(
    adapter: TushareAdapter,
) -> None:
    """空返回 / None → 带列名的空表（下游 upsert 直接跳过，不抛）。"""
    for payload in (None, pd.DataFrame(columns=_REAL_COLUMNS)):
        out, _ = await _run(adapter, payload, date(2026, 9, 15))
        assert out.empty
        assert list(out.columns) == _OUT_COLUMNS


async def test_mf_adp_04_filters_rows_outside_requested_date(adapter: TushareAdapter) -> None:
    """§4.3 判据：日期类接口一律验「返回日期落在入参内」。混入别的日期的行必须被丢弃。"""
    df = _real_shaped_df([
        {"ts_code": "000001.SZ", "trade_date": "20260915", "net_mf_amount": 1.0},
        {"ts_code": "000002.SZ", "trade_date": "20260914", "net_mf_amount": 2.0},
    ])
    out, _ = await _run(adapter, df, date(2026, 9, 15))
    assert list(out["ts_code"]) == ["000001.SZ"]


async def test_mf_adp_05_non_numeric_amount_becomes_null_not_crash(
    adapter: TushareAdapter,
) -> None:
    """脏值（字符串）→ NaN，不抛；行保留（其余列仍有用）。"""
    df = _real_shaped_df([
        {"ts_code": "000001.SZ", "trade_date": "20260915", "net_mf_amount": "n/a",
         "buy_lg_amount": 5.0},
    ])
    out, _ = await _run(adapter, df, date(2026, 9, 15))
    assert len(out) == 1
    assert pd.isna(out.iloc[0]["net_mf_amount"])
    assert out.iloc[0]["buy_lg_amount"] == pytest.approx(50_000.0)
