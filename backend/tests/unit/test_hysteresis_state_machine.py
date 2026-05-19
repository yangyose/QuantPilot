"""HysteresisStateMachine 单元测试（Phase 11 §4.3）。

8 状态转换组合 + 边界条件：
- first month (prev=None)
- stable → stable (same order)
- stable → pending_switch (new order, first conflict)
- pending_switch → stable (sustained new order, confirm switch)
- pending_switch → pending_switch (when same order returns) — N/A, by design
- pending_switch → stable (same order returns)
- 4 维与 2 维列表
- 异常路径：非法 status / 空列表
"""
from __future__ import annotations

import pytest

from quantpilot.engine.hysteresis import HysteresisStateMachine


class TestFirstMonth:
    def test_prev_none_returns_this_order_stable(self) -> None:
        hsm = HysteresisStateMachine()
        order, status = hsm.evaluate(
            prev_month_order=None,
            this_month_order=["trend", "momentum", "value", "mean_reversion"],
            last_status="stable",
        )
        assert order == ["trend", "momentum", "value", "mean_reversion"]
        assert status == "stable"

    def test_prev_none_returns_this_order_stable_even_if_last_status_pending(self) -> None:
        """首次部署 prev=None 时，last_status 被忽略。"""
        hsm = HysteresisStateMachine()
        order, status = hsm.evaluate(
            prev_month_order=None,
            this_month_order=["momentum", "trend", "value", "mean_reversion"],
            last_status="pending_switch",
        )
        assert order == ["momentum", "trend", "value", "mean_reversion"]
        assert status == "stable"


class TestSameOrder:
    def test_stable_to_stable_when_order_unchanged(self) -> None:
        hsm = HysteresisStateMachine()
        order, status = hsm.evaluate(
            prev_month_order=["trend", "momentum", "value", "mean_reversion"],
            this_month_order=["trend", "momentum", "value", "mean_reversion"],
            last_status="stable",
        )
        assert order == ["trend", "momentum", "value", "mean_reversion"]
        assert status == "stable"

    def test_pending_switch_to_stable_when_order_returns_to_match(self) -> None:
        """pending_switch 状态下，若本月排序又回到与上月（prev_order）一致 →
        视为'前次扰动消失'，回到 stable。"""
        hsm = HysteresisStateMachine()
        order, status = hsm.evaluate(
            prev_month_order=["trend", "momentum", "value", "mean_reversion"],
            this_month_order=["trend", "momentum", "value", "mean_reversion"],
            last_status="pending_switch",
        )
        assert order == ["trend", "momentum", "value", "mean_reversion"]
        assert status == "stable"


class TestDifferentOrder:
    def test_stable_to_pending_when_order_differs_first_time(self) -> None:
        """stable 状态下首次冲突：不切，标记 pending_switch，保留 prev_order。"""
        hsm = HysteresisStateMachine()
        prev = ["trend", "momentum", "value", "mean_reversion"]
        this = ["momentum", "trend", "value", "mean_reversion"]
        order, status = hsm.evaluate(
            prev_month_order=prev,
            this_month_order=this,
            last_status="stable",
        )
        assert order == prev, "first conflict should keep prev order"
        assert status == "pending_switch"

    def test_pending_to_stable_when_order_differs_again(self) -> None:
        """连续 2 月不一致：确认切换 → 采用 this_order，回到 stable。"""
        hsm = HysteresisStateMachine()
        prev = ["trend", "momentum", "value", "mean_reversion"]
        this = ["momentum", "trend", "value", "mean_reversion"]
        order, status = hsm.evaluate(
            prev_month_order=prev,
            this_month_order=this,
            last_status="pending_switch",
        )
        assert order == this, "second consecutive conflict should adopt new order"
        assert status == "stable"


class TestReturnedListsAreCopies:
    def test_returned_order_does_not_alias_input(self) -> None:
        """evaluate 返回的列表应为副本，调用方修改不影响后续调用。"""
        hsm = HysteresisStateMachine()
        prev = ["trend", "momentum", "value", "mean_reversion"]
        this = ["trend", "momentum", "value", "mean_reversion"]
        order, _ = hsm.evaluate(prev, this, "stable")
        order.append("HACK")
        # prev / this 未被修改
        assert "HACK" not in prev
        assert "HACK" not in this


class TestRealWorldScenario:
    """两月制动 + 三月制动场景（与 §7.4 描述一致）。"""

    def test_two_month_oscillation_does_not_flip(self) -> None:
        """月 1: stable, prev=A;
        月 2: this=B (≠A) → pending_switch, effective stays A;
        月 3: this=A 又回来 → stable, effective=A（保护住"假切换"）。"""
        hsm = HysteresisStateMachine()
        order_a = ["trend", "momentum", "value", "mean_reversion"]
        order_b = ["momentum", "trend", "value", "mean_reversion"]

        # 月 2
        eff_2, st_2 = hsm.evaluate(prev_month_order=order_a, this_month_order=order_b,
                                     last_status="stable")
        assert eff_2 == order_a
        assert st_2 == "pending_switch"

        # 月 3：this 回到 A
        eff_3, st_3 = hsm.evaluate(prev_month_order=eff_2, this_month_order=order_a,
                                     last_status=st_2)
        assert eff_3 == order_a
        assert st_3 == "stable"

    def test_three_month_sustained_flip_succeeds(self) -> None:
        """月 1: stable, prev=A;
        月 2: this=B → pending_switch, effective=A;
        月 3: this=B 持续 → stable, effective=B（确认切换）。"""
        hsm = HysteresisStateMachine()
        order_a = ["trend", "momentum", "value", "mean_reversion"]
        order_b = ["momentum", "trend", "value", "mean_reversion"]

        eff_2, st_2 = hsm.evaluate(prev_month_order=order_a, this_month_order=order_b,
                                     last_status="stable")
        assert eff_2 == order_a
        assert st_2 == "pending_switch"

        eff_3, st_3 = hsm.evaluate(prev_month_order=eff_2, this_month_order=order_b,
                                     last_status=st_2)
        assert eff_3 == order_b
        assert st_3 == "stable"


class TestErrors:
    def test_invalid_last_status_raises(self) -> None:
        hsm = HysteresisStateMachine()
        with pytest.raises(ValueError, match="last_status"):
            hsm.evaluate(
                prev_month_order=["trend"],
                this_month_order=["trend"],
                last_status="frozen",
            )

    def test_empty_this_order_raises(self) -> None:
        hsm = HysteresisStateMachine()
        with pytest.raises(ValueError, match="this_month_order"):
            hsm.evaluate(prev_month_order=None, this_month_order=[], last_status="stable")
