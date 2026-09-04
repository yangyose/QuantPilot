"""holdout 区间约定的机制化。V1.5-K K-7 / §2.3。Engine 层纯函数，严格无 IO。

## 为什么不是写一段约定就完事

§2.3 定的是纪律：开发集随便试、holdout 改造期不看、定案时解锁一次且解锁后冻结。
但纪律靠人记就等于没有——真实风险是**有人跑一次全窗口分析，不知不觉把 holdout
也算进去了**，而且事后完全看不出来（数字照样合理）。

按 CLAUDE.md C-6「修在源头，让错误不可能再犯，而不是靠下次记得」，本模块把它变成机制：

- `split_by_segment` **默认只给开发集**——「不看 holdout」成为默认行为而非自觉
- 要 holdout / 实盘期必须**显式解锁并写明理由**，否则 `PermissionError`
- 解锁留 WARNING 日志，事后能查「谁在什么时候为什么看了 holdout」

## ⚠️ 边界是常量，绝不随时间滚动

§2.3 原文：「滚动 holdout 会让『今天的 holdout』变成『明天的开发集』，
看过就无法假装没看过。」

若实现成「最近 12 个月」，代码今天跑和下个月跑会给出不同划分，**且一次都不会报错**。
`tests/unit/test_holdout.py::TestBoundariesAreFixed` 两条用例钉它：一条断言分类
不随「今天」变化，一条直接扫源码禁止出现 `today()` / `now()`。

## 解锁语义

§2.3：某个因子决策**定案时**解锁一次；解锁后该决策即冻结，
**不得依据 holdout 结果回头调参**（那等于把 holdout 变成第二个开发集）。
本模块只能保证「解锁被记录」，**保证不了「解锁后不回头调参」**——后者是人的纪律，
代码拦不住。故日志里带上理由，让回头调参这件事在事后可被看见。

## 局限（§2.3 自述，此处重复以免脱离上下文引用）

20 日前向窗口下 holdout 的 242 个交易日只有约 12 个非重叠观测（面板实际覆盖
219 日 → 10 个）。它的作用是**纪律性的，不是统计功效**：能证伪一个被强烈声称的
效应，**不能确认一个弱效应**。「holdout 上也是正的所以有效」在 10 个观测上不成立。
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date
from enum import Enum

logger = logging.getLogger(__name__)

__all__ = [
    "DEV_END",
    "HOLDOUT_END",
    "HOLDOUT_START",
    "Segment",
    "classify",
    "split_by_segment",
]

# 设计文档 §2.3 定案（用户 2026-09-01 拍板方案 D）。**写死，不从今天推算。**
DEV_END = date(2025, 7, 31)          # 开发集最后一天
HOLDOUT_START = date(2025, 8, 1)     # holdout 第一天（须紧接 DEV_END）
HOLDOUT_END = date(2026, 7, 31)      # holdout 最后一天；之后为实盘期


class Segment(Enum):
    """样本区间。`DEV` 可随意使用；另两者须显式解锁。"""

    DEV = "dev"
    HOLDOUT = "holdout"
    LIVE = "live"


def classify(d: date) -> Segment:
    """把一个交易日归入三段之一。边界为闭区间常量，与「今天」无关。"""
    if d <= DEV_END:
        return Segment.DEV
    if d <= HOLDOUT_END:
        return Segment.HOLDOUT
    return Segment.LIVE


def split_by_segment(
    dates: Iterable[date],
    *,
    segment: Segment = Segment.DEV,
    unlock_reason: str | None = None,
) -> list[date]:
    """筛出属于指定区间的日期，**默认开发集**，保持输入顺序。

    Args:
        dates: 待筛日期。
        segment: 目标区间，默认 `Segment.DEV`。
        unlock_reason: 取 `HOLDOUT` / `LIVE` 时**必填**，写明是哪个决策要定案。

    Returns:
        属于该区间的日期（原顺序）。

    Raises:
        PermissionError: 取 HOLDOUT / LIVE 却未提供 `unlock_reason`。
        ValueError: `unlock_reason` 为空白串——空理由不算解锁，
            否则这个机制退化成一个多余的参数。
    """
    if segment is not Segment.DEV:
        if unlock_reason is None:
            raise PermissionError(
                f"取 {segment.value} 区间需显式解锁：请传 unlock_reason 写明哪个"
                "因子决策要定案（§2.3：解锁后该决策即冻结，不得据其回头调参）"
            )
        if not unlock_reason.strip():
            raise ValueError("unlock_reason 不得为空——空理由不算解锁")
        # 留痕：事后要能查「谁在什么时候为什么看了 holdout」。
        # 代码保证不了「解锁后不回头调参」，但能让回头调参这件事可被看见。
        logger.warning(
            "holdout_unlocked segment=%s reason=%s", segment.value, unlock_reason
        )

    return [d for d in dates if classify(d) is segment]
