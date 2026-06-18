"""unit/test_account_logic.py: WAC + FundFlowCreate idempotency_key 纯函数单元测试。"""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from quantpilot.schemas.account import FundFlowCreate
from quantpilot.services.account_service import (
    OversellError,
    ReplayEvent,
    compute_wac,
    replay_position,
)


def _buy(d: date, seq: int, shares: int, price: float, commission: float = 0.0) -> ReplayEvent:
    return ReplayEvent("BUY", d, seq, shares=shares, price=price, commission=commission)


def _sell(d: date, seq: int, shares: int, price: float = 0.0) -> ReplayEvent:
    return ReplayEvent("SELL", d, seq, shares=shares, price=price)


def _div(d: date, seq: int, amount: float) -> ReplayEvent:
    return ReplayEvent("DIVIDEND", d, seq, amount=amount)


class TestReplayPosition:
    """replay_position：持仓 = 非作废成交+分红的派生视图（订正机制核心纯函数）。"""

    def test_empty_is_flat(self) -> None:
        r = replay_position([])
        assert r.shares == 0
        assert r.cost_price == 0.0
        assert r.open_date is None
        assert r.phase is None

    def test_single_buy(self) -> None:
        r = replay_position([_buy(date(2026, 1, 5), 1, 1000, 10.0, commission=25.0)])
        assert r.shares == 1000
        assert r.cost_price == pytest.approx(10.025)
        assert r.open_date == date(2026, 1, 5)
        assert r.phase == "BUILD"

    def test_two_buys_wac(self) -> None:
        """两次买入 → WAC 累积，open_date 取首笔。"""
        r = replay_position([
            _buy(date(2026, 1, 5), 1, 1000, 10.0),
            _buy(date(2026, 1, 8), 2, 1000, 12.0),
        ])
        assert r.shares == 2000
        assert r.cost_price == pytest.approx(11.0)
        assert r.open_date == date(2026, 1, 5)

    def test_partial_sell_keeps_cost(self) -> None:
        """部分卖出：成本不变，phase=REDUCE。"""
        r = replay_position([
            _buy(date(2026, 1, 5), 1, 1000, 10.0),
            _sell(date(2026, 1, 9), 2, 400),
        ])
        assert r.shares == 600
        assert r.cost_price == pytest.approx(10.0)
        assert r.phase == "REDUCE"

    def test_void_polluting_buy_restores_cost(self) -> None:
        """订正核心场景：误买污染 WAC，删掉该买入后 replay 还原原成本。

        正确持仓 200 股@12（seq1）；误买 100 股@50（seq2）已被排除（不在事件里）。
        replay 仅剩 seq1 → 成本回到 12，而非 SELL 冲正残留的 24.67。
        """
        r = replay_position([_buy(date(2026, 1, 5), 1, 200, 12.0)])
        assert r.shares == 200
        assert r.cost_price == pytest.approx(12.0)

    def test_flatten_then_rebuy_resets(self) -> None:
        """清仓后再建仓：成本/open_date 复位，新建仓从干净状态起算。"""
        r = replay_position([
            _buy(date(2026, 1, 5), 1, 1000, 10.0),
            _sell(date(2026, 1, 9), 2, 1000),
            _buy(date(2026, 2, 3), 3, 500, 20.0),
        ])
        assert r.shares == 500
        assert r.cost_price == pytest.approx(20.0)
        assert r.open_date == date(2026, 2, 3)
        assert r.phase == "BUILD"

    def test_dividend_lowers_cost(self) -> None:
        """分红摊低成本：1000 股，分红 500 元 → 成本 -0.5。"""
        r = replay_position([
            _buy(date(2026, 1, 5), 1, 1000, 10.0),
            _div(date(2026, 3, 1), 2, 500.0),
        ])
        assert r.shares == 1000
        assert r.cost_price == pytest.approx(9.5)

    def test_dividend_after_flat_ignored(self) -> None:
        """已平仓后的分红事件不影响成本（无持股可摊）。"""
        r = replay_position([
            _buy(date(2026, 1, 5), 1, 1000, 10.0),
            _sell(date(2026, 1, 9), 2, 1000),
            _div(date(2026, 3, 1), 3, 500.0),
        ])
        assert r.shares == 0
        assert r.cost_price == 0.0

    def test_same_day_buy_before_sell(self) -> None:
        """同日买卖：BUY 先于 SELL 处理（否则会误判超卖）。"""
        r = replay_position([
            _sell(date(2026, 1, 5), 2, 500),
            _buy(date(2026, 1, 5), 1, 1000, 10.0),
        ])
        assert r.shares == 500

    def test_oversell_raises(self) -> None:
        """撤销买入导致后续卖出无券 → OversellError。"""
        with pytest.raises(OversellError):
            replay_position([_sell(date(2026, 1, 9), 1, 400)])


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
