"""HysteresisStateMachine：Phase 11 §4.3 / §7.4 防月度排序跳跃状态机。

业务目的：策略级 ICIR 月度 rebalance 时，若排序较上月变化，**仅在连续 2 个月
排序保持新顺序后才切换正交化顺序**。短期 1 个月单点排序切换被忽略，避免数值
跳跃污染信号生成（Q10 锁定决策）。

状态转换图（4 状态机）：
    prev_order 为 None
        ↓ 直接采纳
    第一个月： (this_order, "stable")

    prev_order == this_order
        ↓ 排序一致
    (this_order, "stable")

    prev_order != this_order:
        last_status == "stable"
            ↓ 第一次排序冲突，不切，标记 pending
        (prev_order, "pending_switch")

        last_status == "pending_switch"
            ↓ 连续 2 月不一致，确认切换
        (this_order, "stable")

Engine 层，无 IO；调用方（FactorMonitorService.apply_monthly_rebalance）
负责传 prev_month_order / last_status 并持久化 effective_order / new_status。
"""
from __future__ import annotations

_STATUS_STABLE = "stable"
_STATUS_PENDING_SWITCH = "pending_switch"

_VALID_STATUSES = frozenset({_STATUS_STABLE, _STATUS_PENDING_SWITCH})


class HysteresisStateMachine:
    """纯函数状态机，无 IO，无内部状态（每次 evaluate 独立计算）。"""

    def evaluate(
        self,
        prev_month_order: list[str] | None,
        this_month_order: list[str],
        last_status: str,
    ) -> tuple[list[str], str]:
        """返回 (effective_order, new_status)。

        Args:
            prev_month_order: 上月生效的正交化顺序；首次部署时为 None
            this_month_order: 本月新计算的 ICIR 排序顺序
            last_status:      上月持久化的 hysteresis_status
                              （"stable" / "pending_switch"）

        Returns:
            (effective_order, new_status)：
            - effective_order: 本月生效顺序（写入 strategy_weights_history 用）
            - new_status: 本月持久化的状态（下月调用时传入 last_status）

        Raises:
            ValueError: last_status 非法值
        """
        if last_status not in _VALID_STATUSES:
            raise ValueError(
                f"last_status must be 'stable' or 'pending_switch', got {last_status!r}"
            )

        if not this_month_order:
            raise ValueError("this_month_order must not be empty")

        # Case 1：首次部署（无历史）→ 直接采纳本月排序，标记 stable
        if prev_month_order is None:
            return (list(this_month_order), _STATUS_STABLE)

        # Case 2：排序与上月一致 → stable，沿用本月排序
        if list(prev_month_order) == list(this_month_order):
            return (list(this_month_order), _STATUS_STABLE)

        # Case 3：排序变化，且上月 stable → 第一次冲突，不切，标记 pending_switch
        if last_status == _STATUS_STABLE:
            return (list(prev_month_order), _STATUS_PENDING_SWITCH)

        # Case 4：排序变化，且上月已 pending_switch → 连续 2 月不一致，确认切换
        return (list(this_month_order), _STATUS_STABLE)
