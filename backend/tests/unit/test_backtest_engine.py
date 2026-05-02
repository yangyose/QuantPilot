"""单元测试：BacktestEngine 交易成本计算纯函数（INV-BT-01~03）。"""
from __future__ import annotations

import pytest

from quantpilot.engine.backtest.engine import (
    BacktestConfig,
    _buy_cost_per_unit,
    _sell_proceeds_per_unit,
)


def _cfg(
    commission: float = 0.00025,
    stamp_tax: float = 0.0005,
    slippage: float = 0.001,
) -> BacktestConfig:
    from datetime import date
    return BacktestConfig(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 12, 31),
        initial_capital=1_000_000.0,
        strategy_config={},
        account_config={},
        commission_rate=commission,
        stamp_tax_rate=stamp_tax,
        slippage_rate=slippage,
    )


class TestBuyCost:
    """INV-BT-01：BUY 成本公式验证。"""

    def test_bt_01_buy_cost(self) -> None:
        """INV-BT-01：BUY price=10000 → 实际成本 = 10000 × (1 + commission + slippage)。"""
        cfg = _cfg()
        price = 10_000.0
        cost = _buy_cost_per_unit(price, cfg)
        expected = price * (1 + cfg.commission_rate + cfg.slippage_rate)
        assert cost == pytest.approx(expected, rel=1e-9)


class TestSellProceeds:
    """INV-BT-02：SELL 净收入公式验证。"""

    def test_bt_02_sell_proceeds(self) -> None:
        """INV-BT-02：SELL price=10000 → 净收入 = price × (1 - 各费率之和)。"""
        cfg = _cfg()
        price = 10_000.0
        proceeds = _sell_proceeds_per_unit(price, cfg)
        expected = price * (1 - cfg.commission_rate - cfg.stamp_tax_rate - cfg.slippage_rate)
        assert proceeds == pytest.approx(expected, rel=1e-9)


class TestZeroCost:
    """INV-BT-03：无交易成本时 BUY cost == SELL proceeds。"""

    def test_bt_03_zero_cost_symmetry(self) -> None:
        """INV-BT-03：commission=0, stamp_tax=0, slippage=0 → BUY cost == SELL proceeds。"""
        cfg = _cfg(commission=0.0, stamp_tax=0.0, slippage=0.0)
        price = 12_345.67
        cost = _buy_cost_per_unit(price, cfg)
        proceeds = _sell_proceeds_per_unit(price, cfg)
        assert cost == pytest.approx(price, rel=1e-9)
        assert proceeds == pytest.approx(price, rel=1e-9)
        assert cost == pytest.approx(proceeds, rel=1e-9)
