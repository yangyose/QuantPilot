"""Phase 14 §14-9 backfill_daily_ic 脚本纯函数测试 — UT-P14-9-03/04。

覆盖（设计文档 §11.4）：
- UT-P14-9-03a：_plan_daily_ic — 末尾无完整前向窗口的日不处理；existing 跳过；force 覆盖
- UT-P14-9-03b：_extract_strategy_z — 从全 universe CompositeScore 抽 z_raw Series
- UT-P14-9-03c：_forward_complete_dates — get_next_trade_date(d,20) ≤ max_data_date 才完整
- UT-P14-9-04：_GracefulInterrupt — handler 调用后 stop=True（仿 UT-P14-2-03）
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

# 把 backend/scripts/ 加到 path 让 backfill_daily_ic 可 import
_BACKEND_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_BACKEND_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SCRIPTS))

from backfill_daily_ic import (  # noqa: E402
    _extract_strategy_z,
    _forward_complete_dates,
    _GracefulInterrupt,
    _plan_daily_ic,
)


# ============================================================
# UT-P14-9-03a：_plan_daily_ic
# ============================================================
def test_ut_p14_9_03a_plan_excludes_tail_and_skips_existing() -> None:
    dates = [date(2024, 1, d) for d in range(1, 11)]  # d1..d10
    forward_complete = set(dates[:7])  # 末尾 3 天无前向窗口
    existing = {dates[1], dates[2]}  # d2, d3 已回填
    to_process, to_skip = _plan_daily_ic(dates, existing, force=False,
                                         forward_complete=forward_complete)
    assert to_process == [dates[0], dates[3], dates[4], dates[5], dates[6]]
    assert to_skip == [dates[1], dates[2]]
    # 末尾 3 天既不处理也不跳过
    assert dates[7] not in to_process and dates[7] not in to_skip


def test_ut_p14_9_03a_force_reprocesses_existing() -> None:
    dates = [date(2024, 1, d) for d in range(1, 8)]
    forward_complete = set(dates)
    existing = {dates[1], dates[2]}
    to_process, to_skip = _plan_daily_ic(dates, existing, force=True,
                                         forward_complete=forward_complete)
    assert to_process == dates  # force → 全处理
    assert to_skip == []


# ============================================================
# UT-P14-9-03b：_extract_strategy_z
# ============================================================
def test_ut_p14_9_03b_extract_strategy_z_from_composites() -> None:
    composites = [
        SimpleNamespace(ts_code="000001.SZ", score_breakdown_raw={
            "trend": {"z_raw": 1.2}, "value": {"z_raw": -0.3}}),
        SimpleNamespace(ts_code="000002.SZ", score_breakdown_raw={
            "trend": {"z_raw": 0.5}, "value": {"z_raw": None}}),  # value 缺
    ]
    z = _extract_strategy_z(composites)
    assert set(z) == {"trend", "value"}
    assert z["trend"]["000001.SZ"] == 1.2
    assert z["trend"]["000002.SZ"] == 0.5
    assert list(z["value"].index) == ["000001.SZ"]  # 仅有效股


def test_ut_p14_9_03b_extract_skips_empty_and_nan() -> None:
    composites = [
        SimpleNamespace(ts_code="000001.SZ", score_breakdown_raw=None),
        SimpleNamespace(ts_code="000002.SZ", score_breakdown_raw={
            "momentum": {"z_raw": float("nan")}}),
    ]
    z = _extract_strategy_z(composites)
    assert z == {}  # 无任何有效 z_raw


# ============================================================
# UT-P14-9-03c：_forward_complete_dates
# ============================================================
class _StubCalendar:
    """简易交易日历：trade_dates 升序列表，get_next_trade_date 取第 n 个后续日。"""

    def __init__(self, trade_dates: list[date]) -> None:
        self._dates = sorted(trade_dates)
        self._idx = {d: i for i, d in enumerate(self._dates)}

    def get_next_trade_date(self, d: date, n: int = 1) -> date:
        i = self._idx[d] + n
        if i >= len(self._dates):
            raise IndexError("beyond calendar")
        return self._dates[i]


def test_ut_p14_9_03c_forward_complete_respects_max_data_date() -> None:
    # 30 个连续工作日
    base = date(2024, 1, 1)
    dates = [base + timedelta(days=i) for i in range(30)]
    cal = _StubCalendar(dates)
    max_data = dates[25]  # 数据只到第 26 个
    fc = _forward_complete_dates(dates, cal, max_data)
    # d 完整 ⟺ d+20 ≤ dates[25] ⟺ index(d)+20 ≤ 25 ⟺ index(d) ≤ 5
    assert dates[5] in fc
    assert dates[6] not in fc  # d+20 = dates[26] > max_data
    assert dates[0] in fc


# ============================================================
# UT-P14-9-04：_GracefulInterrupt
# ============================================================
def test_ut_p14_9_04_graceful_interrupt_sets_stop() -> None:
    g = _GracefulInterrupt()
    assert g.stop is False
    g._handler(2, None)  # 模拟 SIGINT
    assert g.stop is True
