"""V1.5-A A5（SDD-EXT-03）：TushareAdapter.fetch_forecast_express 单测（mock 数据）。

验证 forecast（万元→元 + 区间中值）/ express（元）归一化 + data_priority + 统一 schema。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from quantpilot.data.adapters.tushare import TushareAdapter


def _adapter() -> TushareAdapter:
    with patch("quantpilot.data.adapters.tushare.ts") as mock_ts:
        mock_ts.pro_api.return_value = MagicMock()
        return TushareAdapter(token="test-token")


async def test_a5_forecast_express_normalization() -> None:
    """forecast 万元×10000 + p_change 区间中值/100；express n_income 元；priority 1/2。"""
    adp = _adapter()
    forecast_df = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "ann_date": ["20260228"],
        "end_date": ["20251231"],
        "p_change_min": [10.0],   # %
        "p_change_max": [30.0],
        "net_profit_min": [8000.0],   # 万元
        "net_profit_max": [12000.0],
    })
    express_df = pd.DataFrame({
        "ts_code": ["000002.SZ"],
        "ann_date": ["20260225"],
        "end_date": ["20251231"],
        "n_income": [50_000_000.0],  # 元
        "yoy_net_profit": [15.0],    # %
    })

    async def fake_call(func, **kwargs):
        # 依据 func 身份区分 forecast / express（mock 的 _pro.forecast/_pro.express）
        if func is adp._pro.forecast:
            return forecast_df
        if func is adp._pro.express:
            return express_df
        return pd.DataFrame()

    with patch.object(adp, "_call", new=AsyncMock(side_effect=fake_call)):
        result = await adp.fetch_forecast_express(
            ["000001.SZ", "000002.SZ"], date(2026, 1, 1), date(2026, 3, 31),
        )

    assert set(result.columns) == set(adp._FORECAST_COLS)
    fc = result[result["source_type"] == "forecast"].iloc[0]
    # est_net_profit = mid(8000,12000)=10000 万元 ×10000 = 1e8 元
    assert fc["est_net_profit"] == 1e8
    # yoy = mid(10,30)/100 = 0.20
    assert abs(fc["est_net_profit_yoy"] - 0.20) < 1e-9
    assert fc["data_priority"] == 1
    assert fc["report_period"] == date(2025, 12, 31)
    assert fc["pre_announce_date"] == date(2026, 2, 28)

    ex = result[result["source_type"] == "express"].iloc[0]
    assert ex["est_net_profit"] == 50_000_000.0  # 元，原样
    assert abs(ex["est_net_profit_yoy"] - 0.15) < 1e-9
    assert ex["data_priority"] == 2


async def test_a5_forecast_express_empty_inputs() -> None:
    """空 ts_codes → 空 DataFrame（列齐全）。"""
    adp = _adapter()
    result = await adp.fetch_forecast_express([], date(2026, 1, 1), date(2026, 3, 31))
    assert result.empty
    assert set(result.columns) == set(adp._FORECAST_COLS)


async def test_a5_forecast_only_no_express() -> None:
    """仅 forecast 有数据、express 空 → 只返回 forecast 行。"""
    adp = _adapter()
    forecast_df = pd.DataFrame({
        "ts_code": ["000001.SZ"], "ann_date": ["20260228"], "end_date": ["20251231"],
        "p_change_min": [10.0], "p_change_max": [10.0],
        "net_profit_min": [5000.0], "net_profit_max": [5000.0],
    })

    async def fake_call(func, **kwargs):
        if func is adp._pro.forecast:
            return forecast_df
        return pd.DataFrame()

    with patch.object(adp, "_call", new=AsyncMock(side_effect=fake_call)):
        result = await adp.fetch_forecast_express(
            ["000001.SZ"], date(2026, 1, 1), date(2026, 3, 31),
        )
    assert len(result) == 1
    assert result.iloc[0]["source_type"] == "forecast"
    assert result.iloc[0]["est_net_profit"] == 5000.0 * 10_000  # 万元→元
