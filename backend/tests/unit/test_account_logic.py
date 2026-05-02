"""unit/test_account_logic.py: WAC 成本价纯函数单元测试（无 IO）。"""
from __future__ import annotations

import pytest

from quantpilot.services.account_service import compute_wac


class TestComputeWac:
    """WAC 加权平均成本计算。"""

    def test_first_buy_no_commission(self) -> None:
        """首次建仓，无佣金：成本 = 买入价。"""
        result = compute_wac(
            old_shares=0, old_cost=0.0,
            new_shares=1000, new_price=10.0,
            commission=0.0,
        )
        assert result == pytest.approx(10.0)

    def test_first_buy_with_commission(self) -> None:
        """首次建仓，含佣金：成本摊薄到每股。"""
        # 买 1000 股 @ 10 元，佣金 25 元 → cost = (10000 + 25) / 1000 = 10.025
        result = compute_wac(
            old_shares=0, old_cost=0.0,
            new_shares=1000, new_price=10.0,
            commission=25.0,
        )
        assert result == pytest.approx(10.025)

    def test_add_position_same_price(self) -> None:
        """加仓，价格不变：WAC 不变。"""
        result = compute_wac(
            old_shares=1000, old_cost=10.0,
            new_shares=500, new_price=10.0,
            commission=0.0,
        )
        assert result == pytest.approx(10.0)

    def test_add_position_higher_price(self) -> None:
        """加仓，价格上涨：WAC 上升。"""
        # 已有 1000 股 @ 10.0；再买 500 股 @ 12.0
        # WAC = (1000*10 + 500*12) / 1500 = 16000/1500 ≈ 10.667
        result = compute_wac(
            old_shares=1000, old_cost=10.0,
            new_shares=500, new_price=12.0,
            commission=0.0,
        )
        assert result == pytest.approx(10000 / 1000 * 1000 / 1500 + 12.0 * 500 / 1500)

    def test_add_position_lower_price(self) -> None:
        """加仓，价格下跌：WAC 下降（摊低）。"""
        # 已有 1000 股 @ 12.0；再买 1000 股 @ 10.0
        # WAC = (12000 + 10000) / 2000 = 11.0
        result = compute_wac(
            old_shares=1000, old_cost=12.0,
            new_shares=1000, new_price=10.0,
            commission=0.0,
        )
        assert result == pytest.approx(11.0)

    def test_add_position_with_commission(self) -> None:
        """加仓含佣金：佣金摊入 WAC。"""
        # 已有 1000 股 @ 10.0；再买 500 股 @ 10.0 + 佣金 12.5
        # 新总成本 = 1000*10 + 500*10 + 12.5 = 15012.5
        # WAC = 15012.5 / 1500 ≈ 10.00833...
        result = compute_wac(
            old_shares=1000, old_cost=10.0,
            new_shares=500, new_price=10.0,
            commission=12.5,
        )
        assert result == pytest.approx(15012.5 / 1500)

    @pytest.mark.parametrize(
        "old_shares, old_cost, new_shares, new_price",
        [
            (0, 0.0, 1, 0.0),    # 买入价为 0
            (0, 0.0, 1, 1.0),    # 最小合法输入
        ],
    )
    def test_edge_cases(
        self, old_shares: int, old_cost: float, new_shares: int, new_price: float
    ) -> None:
        """边界情况不抛异常，返回合理值。"""
        result = compute_wac(old_shares, old_cost, new_shares, new_price)
        assert isinstance(result, float)
