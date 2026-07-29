"""V1.5-A A5（SDD-EXT-03）：TushareAdapter.fetch_forecast_express 单测（mock 数据）。

验证 forecast（万元→元 + 区间中值）/ express（元）归一化 + data_priority + 统一 schema。

A5c 本地实证订正（2026-07-29，真实 Tushare 数据）：
1. forecast/express **不支持逗号多码 ts_code**（实测 batch 返回空）→ 必须逐股单码调用。
2. express ``yoy_net_profit`` = **去年同期净利润(元)**，非增长率 %（实测 600161.SH
   n_income=1.103e9 / yoy_net_profit=8.809e8 → 增长 25.2%）→ est_net_profit_yoy 须由
   (n_income - yoy_net_profit)/|yoy_net_profit| 派生，非 /100。
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


def _forecast_row(ts_code: str) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": [ts_code], "ann_date": ["20260228"], "end_date": ["20251231"],
        "p_change_min": [10.0], "p_change_max": [30.0],   # %
        "net_profit_min": [8000.0], "net_profit_max": [12000.0],   # 万元
    })


def _express_row(ts_code: str) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": [ts_code], "ann_date": ["20260225"], "end_date": ["20251231"],
        "n_income": [50_000_000.0],       # 元
        "yoy_net_profit": [40_000_000.0],  # 去年同期净利润(元) → 增长 (5e7-4e7)/4e7 = 0.25
    })


async def test_a5_forecast_express_normalization() -> None:
    """forecast 万元×10000 + p_change 中值/100；express n_income 元 + yoy 由绝对值派生。"""
    adp = _adapter()
    calls: list[tuple[str, str]] = []  # (interface, ts_code)

    async def fake_call(func, **kwargs):
        code = kwargs.get("ts_code", "")
        # A5c 订正核心断言：绝不逗号多码
        assert "," not in code, f"forecast/express 不支持逗号多码，收到 {code!r}"
        if func is adp._pro.forecast:
            calls.append(("forecast", code))
            return _forecast_row(code) if code == "000001.SZ" else pd.DataFrame()
        if func is adp._pro.express:
            calls.append(("express", code))
            return _express_row(code) if code == "000002.SZ" else pd.DataFrame()
        return pd.DataFrame()

    with patch.object(adp, "_call", new=AsyncMock(side_effect=fake_call)):
        result = await adp.fetch_forecast_express(
            ["000001.SZ", "000002.SZ"], date(2026, 1, 1), date(2026, 3, 31),
        )

    # 逐股单码：forecast/express 各被每只股票调用一次
    assert ("forecast", "000001.SZ") in calls and ("forecast", "000002.SZ") in calls
    assert ("express", "000001.SZ") in calls and ("express", "000002.SZ") in calls

    assert set(result.columns) == set(adp._FORECAST_COLS)
    fc = result[result["source_type"] == "forecast"].iloc[0]
    assert fc["est_net_profit"] == 1e8              # mid(8000,12000)=1e4 万元 ×1e4 = 1e8
    assert abs(fc["est_net_profit_yoy"] - 0.20) < 1e-9   # mid(10,30)/100
    assert fc["data_priority"] == 1
    assert fc["report_period"] == date(2025, 12, 31)
    assert fc["pre_announce_date"] == date(2026, 2, 28)

    ex = result[result["source_type"] == "express"].iloc[0]
    assert ex["est_net_profit"] == 50_000_000.0     # 元，原样
    # A5c：yoy = (n_income - 去年同期)/|去年同期| = (5e7-4e7)/4e7 = 0.25
    assert abs(ex["est_net_profit_yoy"] - 0.25) < 1e-9
    assert ex["data_priority"] == 2


async def test_a5_express_yoy_zero_prior_profit_nan() -> None:
    """去年同期净利润 = 0 / NaN → est_net_profit_yoy 为 NaN（不除零）。"""
    adp = _adapter()
    ex_df = pd.DataFrame({
        "ts_code": ["000002.SZ"], "ann_date": ["20260225"], "end_date": ["20251231"],
        "n_income": [50_000_000.0], "yoy_net_profit": [0.0],
    })

    async def fake_call(func, **kwargs):
        if func is adp._pro.express:
            return ex_df
        return pd.DataFrame()

    with patch.object(adp, "_call", new=AsyncMock(side_effect=fake_call)):
        result = await adp.fetch_forecast_express(
            ["000002.SZ"], date(2026, 1, 1), date(2026, 3, 31),
        )
    ex = result[result["source_type"] == "express"].iloc[0]
    assert ex["est_net_profit"] == 50_000_000.0
    assert pd.isna(ex["est_net_profit_yoy"])


async def test_a5_forecast_dedup_update_flag_rows() -> None:
    """A5c：forecast 同一公告返 update_flag 0/1 两行 / 原始+修正快报（同 ts_code+period+
    source_type）→ adapter 须去重为一行（保留 pre_announce_date **最早**者，最大化真空期
    覆盖 + PIT 首发时点），否则 upsert ON CONFLICT 在同一 INSERT 内命中同键两次 →
    CardinalityViolation。"""
    adp = _adapter()
    # 同键两行：ann_date 20240130（原始）/ 20240215（修正）→ 保留最早 20240130
    fc_df = pd.DataFrame({
        "ts_code": ["002594.SZ", "002594.SZ"],
        "ann_date": ["20240215", "20240130"],   # 乱序，验证按日期排序而非行序
        "end_date": ["20231231", "20231231"],
        "p_change_min": [78.0, 74.0], "p_change_max": [90.0, 86.0],
        "net_profit_min": [3000000.0, 2900000.0], "net_profit_max": [3200000.0, 3100000.0],
    })

    async def fake_call(func, **kwargs):
        if func is adp._pro.forecast:
            return fc_df
        return pd.DataFrame()

    with patch.object(adp, "_call", new=AsyncMock(side_effect=fake_call)):
        result = await adp.fetch_forecast_express(
            ["002594.SZ"], date(2024, 1, 1), date(2024, 3, 31),
        )
    # 同键仅一行
    key = result[["ts_code", "report_period", "source_type"]]
    assert not key.duplicated().any()
    assert len(result) == 1
    # 保留最早公告（20240130，mid(2900000,3100000)=3.0e6 万元 ×1e4 = 3.0e10 元）
    row = result.iloc[0]
    assert row["pre_announce_date"] == date(2024, 1, 30)
    assert row["est_net_profit"] == 3.0e10


async def test_a5_forecast_express_empty_inputs() -> None:
    """空 ts_codes → 空 DataFrame（列齐全）。"""
    adp = _adapter()
    result = await adp.fetch_forecast_express([], date(2026, 1, 1), date(2026, 3, 31))
    assert result.empty
    assert set(result.columns) == set(adp._FORECAST_COLS)


async def test_a5_forecast_only_no_express() -> None:
    """仅 forecast 有数据、express 空 → 只返回 forecast 行。"""
    adp = _adapter()

    async def fake_call(func, **kwargs):
        code = kwargs.get("ts_code", "")
        assert "," not in code
        if func is adp._pro.forecast and code == "000001.SZ":
            return pd.DataFrame({
                "ts_code": ["000001.SZ"], "ann_date": ["20260228"], "end_date": ["20251231"],
                "p_change_min": [10.0], "p_change_max": [10.0],
                "net_profit_min": [5000.0], "net_profit_max": [5000.0],
            })
        return pd.DataFrame()

    with patch.object(adp, "_call", new=AsyncMock(side_effect=fake_call)):
        result = await adp.fetch_forecast_express(
            ["000001.SZ"], date(2026, 1, 1), date(2026, 3, 31),
        )
    assert len(result) == 1
    assert result.iloc[0]["source_type"] == "forecast"
    assert result.iloc[0]["est_net_profit"] == 5000.0 * 10_000  # 万元→元
