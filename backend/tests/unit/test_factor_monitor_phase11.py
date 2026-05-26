"""Phase 11 §4.1 rolling_icir_state 单元测试（不需要 DB，mock repo）。

覆盖：
- sample_size < 60 → None（冷启动 fallback 触发）
- sample_size ≥ 60 → ICIRSnapshot ic_mean / ic_std / icir / CI / t_stat 数学正确
- bootstrap CI 复现性（同 seed 同输入 → 同结果）
- ic_std=0 (退化) → None
"""
from __future__ import annotations

import math
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np

from quantpilot.engine.factor_monitor import FactorMonitorEngine
from quantpilot.services.factor_monitor_service import (
    FactorMonitorService,
)


def _mk_rows(ic_values: list[float | None]) -> list[SimpleNamespace]:
    """构造 mock 的 FactorICWindowState 行（仅含 ic_value 字段，其它 None）。"""
    return [SimpleNamespace(ic_value=v) for v in ic_values]


async def test_returns_none_when_sample_size_below_60() -> None:
    """sample_size = 59 → None（触发冷启动）。"""
    service = FactorMonitorService(session=AsyncMock(), engine=FactorMonitorEngine())
    service._repo = SimpleNamespace(
        get_ic_daily_window=AsyncMock(return_value=_mk_rows([0.05] * 59)),
    )
    snapshot = await service.rolling_icir_state(
        session=AsyncMock(),
        trade_date=date(2025, 12, 31),
        strategy="trend",
        factor="ma_alignment",
        state="UPTREND",
    )
    assert snapshot is None


async def test_returns_snapshot_when_sample_size_at_60() -> None:
    """sample_size = 60 → 返回非 None Snapshot。"""
    rng = np.random.default_rng(123)
    ic_values = [float(v) for v in rng.normal(0.05, 0.02, 60)]
    service = FactorMonitorService(session=AsyncMock(), engine=FactorMonitorEngine())
    service._repo = SimpleNamespace(
        get_ic_daily_window=AsyncMock(return_value=_mk_rows(ic_values)),
    )
    snapshot = await service.rolling_icir_state(
        session=AsyncMock(),
        trade_date=date(2025, 12, 31),
        strategy="trend",
        factor="ma_alignment",
        state="UPTREND",
    )
    assert snapshot is not None
    assert snapshot.sample_size == 60
    # IC mean 接近输入均值 0.05
    assert abs(snapshot.ic_mean - 0.05) < 0.02
    # IC std 接近输入 std 0.02
    assert abs(snapshot.ic_std - 0.02) < 0.01
    # ICIR ≈ 0.05 / 0.02 = 2.5（实际略偏）
    assert 1.0 < snapshot.icir < 4.0
    # t_stat = ICIR * sqrt(60) ≈ 19
    assert abs(snapshot.t_stat - snapshot.icir * math.sqrt(60)) < 1e-9


async def test_filters_null_ic_values() -> None:
    """ic_value=None 的行被过滤；剩余 < 60 → 返回 None。"""
    # 70 行总，含 15 个 None → 有效 55 < 60
    values: list[float | None] = [0.04] * 55 + [None] * 15
    service = FactorMonitorService(session=AsyncMock(), engine=FactorMonitorEngine())
    service._repo = SimpleNamespace(
        get_ic_daily_window=AsyncMock(return_value=_mk_rows(values)),
    )
    snapshot = await service.rolling_icir_state(
        session=AsyncMock(),
        trade_date=date(2025, 12, 31),
        strategy="trend",
        factor="ma_alignment",
        state="UPTREND",
    )
    assert snapshot is None


async def test_zero_std_returns_none() -> None:
    """全相同 IC 值 → std=0 → 返回 None（避免除零）。"""
    service = FactorMonitorService(session=AsyncMock(), engine=FactorMonitorEngine())
    service._repo = SimpleNamespace(
        get_ic_daily_window=AsyncMock(return_value=_mk_rows([0.05] * 80)),
    )
    snapshot = await service.rolling_icir_state(
        session=AsyncMock(),
        trade_date=date(2025, 12, 31),
        strategy="trend",
        factor="ma_alignment",
        state="UPTREND",
    )
    assert snapshot is None


async def test_ci_reproducibility() -> None:
    """bootstrap CI 用固定 seed → 同输入两次调用应得相同 CI。"""
    rng = np.random.default_rng(42)
    ic_values = [float(v) for v in rng.normal(0.08, 0.03, 100)]

    def _new_service() -> FactorMonitorService:
        s = FactorMonitorService(session=AsyncMock(), engine=FactorMonitorEngine())
        s._repo = SimpleNamespace(
            get_ic_daily_window=AsyncMock(return_value=_mk_rows(ic_values)),
        )
        return s

    snap_a = await _new_service().rolling_icir_state(
        session=AsyncMock(),
        trade_date=date(2025, 12, 31),
        strategy="trend",
        factor="ma_alignment",
        state="UPTREND",
    )
    snap_b = await _new_service().rolling_icir_state(
        session=AsyncMock(),
        trade_date=date(2025, 12, 31),
        strategy="trend",
        factor="ma_alignment",
        state="UPTREND",
    )
    assert snap_a is not None
    assert snap_b is not None
    assert abs(snap_a.ic_ci_low - snap_b.ic_ci_low) < 1e-12
    assert abs(snap_a.ic_ci_high - snap_b.ic_ci_high) < 1e-12


async def test_ci_brackets_ic_mean() -> None:
    """CI 应该包含 ic_mean（除非数据极度偏态，本测试构造正态）。"""
    rng = np.random.default_rng(7)
    ic_values = [float(v) for v in rng.normal(0.06, 0.015, 80)]
    service = FactorMonitorService(session=AsyncMock(), engine=FactorMonitorEngine())
    service._repo = SimpleNamespace(
        get_ic_daily_window=AsyncMock(return_value=_mk_rows(ic_values)),
    )
    snapshot = await service.rolling_icir_state(
        session=AsyncMock(),
        trade_date=date(2025, 12, 31),
        strategy="trend",
        factor="ma_alignment",
        state="UPTREND",
    )
    assert snapshot is not None
    assert snapshot.ic_ci_low < snapshot.ic_mean < snapshot.ic_ci_high


async def test_window_dates_passed_to_repo() -> None:
    """验证 trade_date=t 时 repo 收到 start=t-272, end=t-20。"""
    captured: dict = {}

    async def _capture(session, *, strategy, factor, state, start_date, end_date):
        captured["start"] = start_date
        captured["end"] = end_date
        return _mk_rows([])

    service = FactorMonitorService(session=AsyncMock(), engine=FactorMonitorEngine())
    service._repo = SimpleNamespace(get_ic_daily_window=_capture)

    t = date(2026, 5, 14)
    await service.rolling_icir_state(
        session=AsyncMock(),
        trade_date=t,
        strategy="trend",
        factor="ma_alignment",
        state="UPTREND",
    )
    # 旧路径（无 calendar 注入）：272 日历日 ≈ 9 个月；20 日历日 ≈ 3 周
    # 【降级说明】Phase 14 §14-5：未注入 calendar 时回退到日历日窗口（兼容旧测试）；
    # 生产路径全部通过构造器注入 TradingCalendar 走严格交易日窗口（见
    # test_window_dates_use_strict_trade_days_when_calendar_set）。
    from datetime import timedelta
    assert captured["end"] == t - timedelta(days=20)
    assert captured["start"] == t - timedelta(days=272)


# ============================================================
# Phase 14 §14-5：rolling_icir_state 改严格交易日窗口
# ============================================================


async def test_p14_5_01_window_dates_use_strict_trade_days_when_calendar_set() -> None:
    """UT-P14-5-01：注入 TradingCalendar → 窗口端点严格按交易日推算。

    断言 service 调用 calendar.get_prev_trade_date(trade_date, n=20) 拿到 end，
    再调用 calendar.get_prev_trade_date(end, n=252) 拿到 start，
    且最终 repo.get_ic_daily_window 收到的 start/end 与 calendar 返回值一致。
    """
    captured: dict = {}

    async def _capture(session, *, strategy, factor, state, start_date, end_date):
        captured["start"] = start_date
        captured["end"] = end_date
        return _mk_rows([])

    # mock TradingCalendar：仅捕获 get_prev_trade_date 调用顺序与参数
    calls: list[tuple[date, int]] = []
    fake_end = date(2026, 4, 15)        # mock: t - 20 trade days
    fake_start = date(2025, 4, 11)      # mock: fake_end - 252 trade days

    class _MockCalendar:
        def get_prev_trade_date(self, d: date, n: int = 1) -> date:
            calls.append((d, n))
            if len(calls) == 1:
                # 第一次：t - 20 → 返回 fake_end
                return fake_end
            # 第二次：fake_end - 252 → 返回 fake_start
            return fake_start

    service = FactorMonitorService(
        session=AsyncMock(),
        engine=FactorMonitorEngine(),
        calendar=_MockCalendar(),
    )
    service._repo = SimpleNamespace(get_ic_daily_window=_capture)

    t = date(2026, 5, 14)
    await service.rolling_icir_state(
        session=AsyncMock(),
        trade_date=t,
        strategy="trend",
        factor="ma_alignment",
        state="UPTREND",
    )

    # calendar 被调用 2 次：先 (t, 20) 拿 end，再 (end, 252) 拿 start
    assert calls == [(t, 20), (fake_end, 252)]
    # repo 收到的窗口与 calendar 返回值一致（严格交易日）
    assert captured["end"] == fake_end
    assert captured["start"] == fake_start
