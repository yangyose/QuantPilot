"""DataService 纯函数单元测试：_build_st_map 等"""
from datetime import date

import pandas as pd

from quantpilot.services.data_service import _build_st_map


def test_build_st_map_old_st_within_period() -> None:
    """RM-16 回归：早就叫 *ST 的股票（公告日远在 ingest 窗口之前）必须被识别。

    场景：股票 X 在 2022-01-01 改名为 *ST X 至今，ingest 窗口 2026-03-01 ~ 2026-05-08。
    namechange 必须用 5 年回溯（fetch 起点 2021）才能拉到该记录；本测试假设上层已
    用宽窗口拉到记录，验证 _build_st_map 正确把 X 加入窗口内所有交易日的 st_map。
    """
    namechange_df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "name": ["*ST X"],
            "start_date": [date(2022, 1, 1)],
            "end_date": [None],  # 至今仍 ST
        }
    )
    trade_dates = [date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 8)]
    st_map = _build_st_map(namechange_df, trade_dates)

    assert all("000001.SZ" in st_map[td] for td in trade_dates)


def test_build_st_map_de_st_within_period() -> None:
    """RM-16 回归：曾 ST 后又摘帽的股票，仅在 ST 期内被标记。

    场景：A 在 2020-01-01 改 *ST A，2024-07-01 摘帽改回 A。
    trade_date=2023-06-01 → ST；trade_date=2025-01-01 → 不 ST。
    """
    namechange_df = pd.DataFrame(
        {
            "ts_code": ["000002.SZ", "000002.SZ"],
            "name": ["*ST A", "A"],
            "start_date": [date(2020, 1, 1), date(2024, 7, 1)],
            "end_date": [date(2024, 6, 30), None],
        }
    )
    trade_dates = [date(2023, 6, 1), date(2025, 1, 1)]
    st_map = _build_st_map(namechange_df, trade_dates)

    assert "000002.SZ" in st_map[date(2023, 6, 1)]
    assert "000002.SZ" not in st_map[date(2025, 1, 1)]


def test_build_st_map_non_st_renames_ignored() -> None:
    """非 ST 改名（如 美的电器 → 美的集团）不应进入 st_map。"""
    namechange_df = pd.DataFrame(
        {
            "ts_code": ["000333.SZ"],
            "name": ["美的集团"],
            "start_date": [date(2020, 1, 1)],
            "end_date": [None],
        }
    )
    trade_dates = [date(2026, 5, 1)]
    st_map = _build_st_map(namechange_df, trade_dates)

    assert st_map[date(2026, 5, 1)] == set()


def test_build_st_map_empty_namechange_returns_empty_sets() -> None:
    """namechange 为空 DataFrame → st_map 每天仍为空 set（非 None）。"""
    namechange_df = pd.DataFrame(columns=["ts_code", "name", "start_date", "end_date"])
    trade_dates = [date(2026, 5, 1), date(2026, 5, 2)]
    st_map = _build_st_map(namechange_df, trade_dates)

    assert len(st_map) == 2
    assert all(st_map[td] == set() for td in trade_dates)
