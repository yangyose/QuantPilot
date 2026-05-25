"""unit/test_account_logic.py: WAC + FundFlowCreate idempotency_key 纯函数单元测试。"""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from quantpilot.schemas.account import FundFlowCreate
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


class TestFundFlowCreateIdempotencyKey:
    """Phase 14 §14-1 RM-13：idempotency_key 字段 pydantic 校验（pattern + length）。"""

    def _base(self, **extra: object) -> dict:
        return {
            "account_id": 1, "amount": 10000.0,
            "trade_date": date(2026, 4, 10),
            **extra,
        }

    def test_idempotency_key_none_ok(self) -> None:
        """idempotency_key 缺省 = None → 兼容旧客户端，校验通过。"""
        m = FundFlowCreate(**self._base())
        assert m.idempotency_key is None

    def test_idempotency_key_uuid4_ok(self) -> None:
        """标准 UUID4（36 字符 + `-`）通过校验。"""
        m = FundFlowCreate(**self._base(
            idempotency_key="a1b2c3d4-e5f6-4789-abcd-ef0123456789",
        ))
        assert m.idempotency_key == "a1b2c3d4-e5f6-4789-abcd-ef0123456789"

    def test_idempotency_key_short_alphanum_ok(self) -> None:
        """短字母数字 + 下划线 + `-` 通过校验（前端可自定义短 key）。"""
        m = FundFlowCreate(**self._base(idempotency_key="deposit_2026-04-10_1"))
        assert m.idempotency_key == "deposit_2026-04-10_1"

    def test_idempotency_key_over_36_chars_rejected(self) -> None:
        """超过 36 字符 → 422。"""
        too_long = "a" * 37
        with pytest.raises(ValidationError):
            FundFlowCreate(**self._base(idempotency_key=too_long))

    def test_idempotency_key_invalid_pattern_rejected(self) -> None:
        """含非法字符（@、空格等）→ 422，防止注入。"""
        for bad in ("key with space", "key@host", "key/slash", "key.dot"):
            with pytest.raises(ValidationError):
                FundFlowCreate(**self._base(idempotency_key=bad))
