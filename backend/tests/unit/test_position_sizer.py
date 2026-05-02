"""PSZ-01~04: PositionSizer 单元测试（纯函数，无 DB）。"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from quantpilot.engine.market_state import MarketStateEnum
from quantpilot.engine.position import PositionConfig, PositionSizer
from quantpilot.engine.signal import TradeSignal
from quantpilot.models.account import Position

TRADE_DATE = date(2026, 4, 8)


def _make_buy_signal(ts_code: str, score: float = 85.0) -> TradeSignal:
    return TradeSignal(
        ts_code=ts_code,
        signal_type="BUY",
        trade_date=TRADE_DATE,
        score=score,
    )


def _make_position(
    ts_code: str,
    *,
    market_value: float,
    cost_price: float = 10.0,
    pnl_pct: float = 0.0,
) -> Position:
    p = MagicMock(spec=Position)
    p.ts_code = ts_code
    p.market_value = market_value
    p.cost_price = cost_price
    p.pnl_pct = pnl_pct
    return p


sizer = PositionSizer()


# ---------------------------------------------------------------------------
# PSZ-01: UPTREND，无持仓，总资产10万 → suggested_pct≈0.10
# ---------------------------------------------------------------------------
def test_psz_01_uptrend_no_position() -> None:
    """PSZ-01: 上升趋势，无持仓，总资产10万 → suggested_pct=0.10（单笔10%约束）"""
    signals = [_make_buy_signal("000001.SZ")]
    result = sizer.suggest(
        signals=signals,
        account_total_assets=100_000.0,
        account_cash=100_000.0,
        current_positions=[],
        market_state=MarketStateEnum.UPTREND,
    )
    assert len(result) == 1
    assert result[0].suggested_pct == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# PSZ-02: DOWNTREND，可用仓位不足单笔一半 → suggested_pct=None
# ---------------------------------------------------------------------------
def test_psz_02_downtrend_insufficient_capacity() -> None:
    """PSZ-02: 下跌趋势有效仓位40%，已用35%，可用5% < 单笔一半(5%) → suggested_pct=None"""
    config = PositionConfig(
        single_pct=0.10,
        max_total_pct=0.80,
        min_cash_pct=0.20,
        downtrend_multiplier=0.50,
    )
    # DOWNTREND: 有效上限 = 0.80 * 0.50 = 0.40
    # 当前已用 = 35,000 / 100,000 = 35%
    # 可用 = max(0, 0.40 - 0.35 - 0.20) < 0 → capped to 0
    # 实际可用 = max(0, 0.40 - 0.35 - 0.20) = max(0, -0.15) = 0
    # actually: 可用 = max(0, 0.40 - 0.35 - 0.20) = max(0, -0.15) = 0
    # since 0 < 0.05 (half of single_pct=0.10), suggested_pct=None

    # Wait, let me recalculate per spec:
    # DOWNTREND multiplier=0.50, max_total_pct=0.80 → effective_max = 0.80 * 0.50 = 0.40
    # current_used = 35,000 / 100,000 = 0.35
    # available = max(0, 0.40 - 0.35 - 0.20) = max(0, -0.15) = 0
    # 0 < 0.05 (single_pct * 0.5) → suggested_pct = None

    signals = [_make_buy_signal("000001.SZ")]
    positions = [_make_position("000002.SZ", market_value=35_000.0)]
    result = sizer.suggest(
        signals=signals,
        account_total_assets=100_000.0,
        account_cash=65_000.0,
        current_positions=positions,
        market_state=MarketStateEnum.DOWNTREND,
        config=config,
    )
    assert len(result) == 1
    assert result[0].suggested_pct is None


# ---------------------------------------------------------------------------
# PSZ-03: 单股已持仓15%，买入后接近20%上限 → suggested_pct调整为剩余5%
# ---------------------------------------------------------------------------
def test_psz_03_single_stock_cap() -> None:
    """PSZ-03: 该股已持仓15%（15000/100000），上限20% → 剩余额度5%"""
    signals = [_make_buy_signal("000001.SZ")]
    existing = _make_position("000001.SZ", market_value=15_000.0)
    # 总资产100,000，cash充足，无总仓位约束
    result = sizer.suggest(
        signals=signals,
        account_total_assets=100_000.0,
        account_cash=85_000.0,
        current_positions=[existing],
        market_state=MarketStateEnum.UPTREND,
    )
    assert len(result) == 1
    pct = result[0].suggested_pct
    assert pct is not None
    # 单股剩余额度 = 20% - 15% = 5% < single_pct(10%) → 取 5%
    assert pct == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# PSZ-04: OSCILLATION，可用仓位充足 → 系数0.75正确应用
# ---------------------------------------------------------------------------
def test_psz_04_oscillation_multiplier() -> None:
    """PSZ-04: 震荡市 0.75×，有效仓位上限 = 80% × 0.75 = 60%"""
    config = PositionConfig(
        single_pct=0.10,
        max_total_pct=0.80,
        min_cash_pct=0.20,
        oscillation_multiplier=0.75,
    )
    # OSCILLATION: effective_max = 0.80 * 0.75 = 0.60
    # current_used = 0 (无持仓)
    # available = max(0, 0.60 - 0.0 - 0.20) = 0.40 >> single_pct=0.10
    # single_pct min constraint → suggested_pct = 0.10
    signals = [_make_buy_signal("000001.SZ")]
    result = sizer.suggest(
        signals=signals,
        account_total_assets=100_000.0,
        account_cash=100_000.0,
        current_positions=[],
        market_state=MarketStateEnum.OSCILLATION,
        config=config,
    )
    assert len(result) == 1
    assert result[0].suggested_pct == pytest.approx(0.10)
