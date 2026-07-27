"""V1.5-A A5b（SDD-EXT-03）：apply_forecast_roe_override 纯函数单测。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from quantpilot.engine.forecast_override import apply_forecast_roe_override


def _fin(rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows).set_index("ts_code")


def test_a5b_override_in_vacuum_period() -> None:
    """快报报告期晚于正式财报期（真空期）→ roe = est_net_profit / total_equity。"""
    fin = _fin({
        "ts_code": ["000001.SZ"],
        "roe": [0.10],
        "report_period": [date(2025, 9, 30)],   # 最近正式财报=三季报
        "total_equity": [1.0e9],                # 元
    })
    fc = _fin({
        "ts_code": ["000001.SZ"],
        "report_period": [date(2025, 12, 31)],  # 快报=年报期（晚于三季报）→ 真空期
        "est_net_profit": [2.0e8],              # 元 → roe=2e8/1e9=0.20
    })
    out = apply_forecast_roe_override(fin, fc)
    assert abs(out.at["000001.SZ", "roe"] - 0.20) < 1e-9
    # 不改入参
    assert fin.at["000001.SZ", "roe"] == 0.10


def test_a5b_no_override_when_not_vacuum() -> None:
    """快报报告期 ≤ 正式财报期（正式财报已出）→ 不覆盖。"""
    fin = _fin({
        "ts_code": ["000001.SZ"], "roe": [0.10],
        "report_period": [date(2025, 12, 31)], "total_equity": [1.0e9],
    })
    fc = _fin({
        "ts_code": ["000001.SZ"],
        "report_period": [date(2025, 12, 31)],  # 同期，正式财报已出
        "est_net_profit": [2.0e8],
    })
    out = apply_forecast_roe_override(fin, fc)
    assert out.at["000001.SZ", "roe"] == 0.10


def test_a5b_no_override_when_equity_missing_or_zero() -> None:
    """total_equity NULL / 0 → 不覆盖（保守，不产生 inf/NaN）。"""
    for equity in (None, 0.0):
        fin = _fin({
            "ts_code": ["000001.SZ"], "roe": [0.10],
            "report_period": [date(2025, 9, 30)], "total_equity": [equity],
        })
        fc = _fin({
            "ts_code": ["000001.SZ"],
            "report_period": [date(2025, 12, 31)], "est_net_profit": [2.0e8],
        })
        out = apply_forecast_roe_override(fin, fc)
        assert out.at["000001.SZ", "roe"] == 0.10


def test_a5b_empty_forecast_returns_financials() -> None:
    """forecast 空 → 原样返回。"""
    fin = _fin({
        "ts_code": ["000001.SZ"], "roe": [0.10],
        "report_period": [date(2025, 9, 30)], "total_equity": [1.0e9],
    })
    out = apply_forecast_roe_override(fin, pd.DataFrame())
    assert out.at["000001.SZ", "roe"] == 0.10


def test_a5b_only_affects_common_codes() -> None:
    """forecast 仅覆盖交集股，其余不动。"""
    fin = _fin({
        "ts_code": ["000001.SZ", "000002.SZ"], "roe": [0.10, 0.11],
        "report_period": [date(2025, 9, 30), date(2025, 9, 30)],
        "total_equity": [1.0e9, 1.0e9],
    })
    fc = _fin({
        "ts_code": ["000001.SZ"],
        "report_period": [date(2025, 12, 31)], "est_net_profit": [3.0e8],
    })
    out = apply_forecast_roe_override(fin, fc)
    assert abs(out.at["000001.SZ", "roe"] - 0.30) < 1e-9
    assert out.at["000002.SZ", "roe"] == 0.11  # 无 forecast，不动
