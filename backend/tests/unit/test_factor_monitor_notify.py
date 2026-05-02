"""unit/test_factor_monitor_notify.py: Phase 10 §7.3 FactorMonitorService 告警接入验证。

通过 monkeypatch _FACTOR_MAP 只计算 1 个因子、mock session 和 engine，
覆盖最关键的通知点：alert 触发时 notify_factor_alert 带 ic_mean 参数被调用。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from quantpilot.services import factor_monitor_service as fms_module
from quantpilot.services.factor_monitor_service import FactorMonitorService

_CALC_MONTH = date(2026, 3, 31)


def _make_session_with_pool(pool_rows: list[tuple]) -> MagicMock:
    """mock async session：第一次 execute 返回候选池，后续返回空历史 IC。"""
    session = MagicMock()

    call_count = {"n": 0}

    async def _execute(stmt):  # noqa: ANN001
        call_count["n"] += 1
        result = MagicMock()
        if call_count["n"] == 1:
            # 候选池查询
            result.all = MagicMock(return_value=pool_rows)
            return result
        # 后续：历史 IC 查询（空）；upsert 无返回
        result.all = MagicMock(return_value=[])
        result.__iter__ = MagicMock(return_value=iter([]))
        return result

    session.execute = AsyncMock(side_effect=_execute)
    session.flush = AsyncMock()
    return session


async def test_factor_alert_notifies_with_ic_mean(monkeypatch: pytest.MonkeyPatch) -> None:
    """当 detect_alert 返回非 None → notify_factor_alert 被调用，且 ic_mean 参数与计算值一致。"""
    # 缩减因子表只剩一个 → 测试简洁
    monkeypatch.setattr(
        fms_module, "_FACTOR_MAP",
        {"composite_score": ("AllStrategies", "composite_score")},
    )

    pool_rows = [
        ("A.SZ", 85.0, 85.0, 80.0, 70.0, 60.0),
        ("B.SZ", 75.0, 75.0, 70.0, 65.0, 55.0),
        ("C.SZ", 65.0, 65.0, 60.0, 55.0, 50.0),
    ]
    session = _make_session_with_pool(pool_rows)

    engine = MagicMock()
    engine.calc_ic = MagicMock(return_value=0.05)
    engine.calc_ic_ir = MagicMock(return_value=(0.01, 0.02, 0.5))  # ic_mean=0.01
    engine.calc_half_life = MagicMock(return_value=10.0)
    engine.detect_alert = MagicMock(return_value="INEFFICIENT")

    svc = FactorMonitorService(session, engine)

    # mock _calc_forward_returns 避免真实 SQL
    async def _fake_fwd(ts_codes, base_date, window):  # noqa: ANN001
        return pd.Series({"A.SZ": 0.03, "B.SZ": 0.01, "C.SZ": -0.01})

    svc._calc_forward_returns = _fake_fwd  # type: ignore[method-assign]

    notifier = AsyncMock()
    notifier.notify_factor_alert = AsyncMock()

    written = await svc.run_monthly(_CALC_MONTH, return_window=20, notifier=notifier)

    assert written == 1
    notifier.notify_factor_alert.assert_awaited_once()
    # 验证 ic_mean 参数（位置或关键字均可）
    call = notifier.notify_factor_alert.await_args
    # (alert_type, strategy, factor, ic_mean=...) or positional 3 + kwarg ic_mean
    # 我们要求 Phase 10 改造后使用 kw：ic_mean=...
    assert call.kwargs.get("ic_mean") == pytest.approx(0.01)
    # 前 3 个位置参数：alert_type, strategy, factor
    assert call.args[0] == "INEFFICIENT"
    assert call.args[1] == "AllStrategies"
    assert call.args[2] == "composite_score"


async def test_factor_no_alert_no_notify(monkeypatch: pytest.MonkeyPatch) -> None:
    """detect_alert 返回 None → notify_factor_alert 不被调用。"""
    monkeypatch.setattr(
        fms_module, "_FACTOR_MAP",
        {"composite_score": ("AllStrategies", "composite_score")},
    )

    pool_rows = [
        ("A.SZ", 85.0, 85.0, 80.0, 70.0, 60.0),
        ("B.SZ", 75.0, 75.0, 70.0, 65.0, 55.0),
    ]
    session = _make_session_with_pool(pool_rows)

    engine = MagicMock()
    engine.calc_ic = MagicMock(return_value=0.05)
    engine.calc_ic_ir = MagicMock(return_value=(0.05, 0.02, 2.5))
    engine.calc_half_life = MagicMock(return_value=25.0)
    engine.detect_alert = MagicMock(return_value=None)

    svc = FactorMonitorService(session, engine)

    async def _fake_fwd(ts_codes, base_date, window):  # noqa: ANN001
        return pd.Series({"A.SZ": 0.03, "B.SZ": 0.01})

    svc._calc_forward_returns = _fake_fwd  # type: ignore[method-assign]

    notifier = AsyncMock()
    await svc.run_monthly(_CALC_MONTH, return_window=20, notifier=notifier)

    notifier.notify_factor_alert.assert_not_awaited()
