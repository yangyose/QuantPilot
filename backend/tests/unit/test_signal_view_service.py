"""unit/test_signal_view_service.py: SignalViewService（V1.5-G G-4d-2 API 期账户叠加）。

V1.5-G G-4d-2（§2 派生语义）：管线产账户无关的**共享信号**（G-4d-1），本服务在
`GET /signals` 响应组装期按当前账户叠加 **is_holding 标记 + 仓位建议 suggested_pct**。
不落库（signals 表仍共享），纯视图变换——只改响应 dict，**绝不写 ORM 列**。

本文件验证：
- 持仓 ts_code → dict["is_holding"]=True，非持仓 → False
- BUY 信号叠加 suggested_pct（PositionSizer 按账户总资产/现金/持仓计算）
- SELL 信号 suggested_pct 保持不变（不 sizing）
- 空 dict 列表 → 直接返回，无账户/市场 IO
- 账户数据加载失败 → 降级（is_holding 保持 False，suggested_pct 不变），不抛出
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd  # noqa: F401  (mirror生产依赖，占位)

from quantpilot.core.config_defaults import DEFAULT_RISK_LIMITS
from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.services.signal_view_service import SignalViewService

TRADE_DATE = date(2026, 4, 8)


def _dict(
    ts_code: str,
    signal_type: str = "BUY",
    *,
    suggested_pct: float | None = None,
) -> dict:
    """构造 SignalResponse.model_dump() 形态的响应 dict（叠加前）。"""
    return {
        "id": 1,
        "ts_code": ts_code,
        "signal_type": signal_type,
        "trade_date": TRADE_DATE,
        "score": 85.0,
        "suggested_pct": suggested_pct,
        "is_holding": False,
    }


def _position(ts_code: str, market_value: float = 50_000.0) -> SimpleNamespace:
    return SimpleNamespace(ts_code=ts_code, market_value=market_value)


def _make_services(
    positions: list,
    *,
    total_assets: float = 1_000_000.0,
    cash: float = 800_000.0,
    market_state: str | None = "UPTREND",
    positions_raises: bool = False,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    repo = MagicMock()
    if market_state is None:
        repo.get_latest_market_state = AsyncMock(return_value=None)
    else:
        repo.get_latest_market_state = AsyncMock(
            return_value=SimpleNamespace(market_state=market_state, trade_date=TRADE_DATE)
        )

    acc_svc = MagicMock()
    if positions_raises:
        acc_svc.get_positions = AsyncMock(side_effect=RuntimeError("db down"))
    else:
        acc_svc.get_positions = AsyncMock(return_value=positions)
    acc_svc.get_account = AsyncMock(
        return_value=SimpleNamespace(id=1, total_assets=total_assets, cash=cash)
    )

    cfg = MagicMock()
    cfg.get_risk_limits = AsyncMock(return_value=DEFAULT_RISK_LIMITS)
    return repo, acc_svc, cfg


async def test_overlay_marks_is_holding() -> None:
    """持仓 ts_code → is_holding=True；非持仓 → False。"""
    repo, acc_svc, cfg = _make_services([_position("000001.SZ")])
    svc = SignalViewService(repo, account_service=acc_svc, config_service=cfg)

    dicts = [_dict("000001.SZ"), _dict("000002.SZ")]
    await svc.apply_account_overlay(dicts, account_id=1)

    by_code = {d["ts_code"]: d for d in dicts}
    assert by_code["000001.SZ"]["is_holding"] is True
    assert by_code["000002.SZ"]["is_holding"] is False


async def test_overlay_fills_suggested_pct_for_buy() -> None:
    """BUY 信号叠加 suggested_pct（PositionSizer 计算，非 None）。"""
    repo, acc_svc, cfg = _make_services([])  # 无持仓 → 充足可用仓位
    svc = SignalViewService(repo, account_service=acc_svc, config_service=cfg)

    dicts = [_dict("000002.SZ", "BUY")]
    await svc.apply_account_overlay(dicts, account_id=1)

    assert dicts[0]["suggested_pct"] is not None
    assert dicts[0]["suggested_pct"] > 0


async def test_overlay_leaves_sell_suggested_pct_untouched() -> None:
    """SELL 信号不 sizing → suggested_pct 保持传入值（None）。"""
    repo, acc_svc, cfg = _make_services([_position("000003.SZ")])
    svc = SignalViewService(repo, account_service=acc_svc, config_service=cfg)

    dicts = [_dict("000003.SZ", "SELL", suggested_pct=None)]
    await svc.apply_account_overlay(dicts, account_id=1)

    assert dicts[0]["suggested_pct"] is None
    assert dicts[0]["is_holding"] is True


async def test_overlay_empty_dicts_no_io() -> None:
    """空 dict 列表 → 不查账户/市场（无 IO），直接返回。"""
    repo, acc_svc, cfg = _make_services([])
    svc = SignalViewService(repo, account_service=acc_svc, config_service=cfg)

    await svc.apply_account_overlay([], account_id=1)

    acc_svc.get_positions.assert_not_awaited()
    repo.get_latest_market_state.assert_not_awaited()


async def test_overlay_market_state_fallback_oscillation() -> None:
    """market_state 缺失 → 默认 OSCILLATION（不抛错，BUY 仍可 sizing）。"""
    repo, acc_svc, cfg = _make_services([], market_state=None)
    svc = SignalViewService(repo, account_service=acc_svc, config_service=cfg)

    dicts = [_dict("000004.SZ", "BUY")]
    await svc.apply_account_overlay(dicts, account_id=1)

    # OSCILLATION 系数 0.75 → 仍有可用仓位
    assert dicts[0]["suggested_pct"] is not None


async def test_overlay_account_load_failure_degrades() -> None:
    """账户数据加载失败 → 降级（is_holding 保持 False，suggested_pct 不变），不抛出。"""
    repo, acc_svc, cfg = _make_services([], positions_raises=True)
    svc = SignalViewService(repo, account_service=acc_svc, config_service=cfg)

    dicts = [_dict("000005.SZ", "BUY")]
    # 不应抛出
    await svc.apply_account_overlay(dicts, account_id=1)

    assert dicts[0]["is_holding"] is False
    assert dicts[0]["suggested_pct"] is None


def test_market_state_enum_importable() -> None:
    """守卫：MarketStateEnum 可用（sizing 系数依赖）。"""
    assert MarketStateEnum.OSCILLATION.value
