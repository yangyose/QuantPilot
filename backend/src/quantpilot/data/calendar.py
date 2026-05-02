from __future__ import annotations

import bisect
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from quantpilot.data.adapters.base import DataSourceAdapter


class TradingCalendar:
    """A 股交易日历。

    初始化时接收交易日列表，在内存中缓存为有序结构。
    所有方法均为同步纯函数，无 IO。
    """

    def __init__(self, trade_dates: list[date]) -> None:
        """trade_dates: 交易日列表（顺序不限，内部自动排序）"""
        self._dates: list[date] = sorted(trade_dates)
        self._date_set: set[date] = set(trade_dates)

    def is_trade_date(self, d: date) -> bool:
        """d 是否为交易日"""
        return d in self._date_set

    def get_prev_trade_date(self, d: date, n: int = 1) -> date:
        """d 之前第 n 个交易日（d 本身不计入）。

        n=1 时返回上一个交易日。
        """
        # bisect_left 找到 d 在有序列表中的位置（≥d 的第一个位置）
        idx = bisect.bisect_left(self._dates, d)
        target = idx - n
        if target < 0:
            raise ValueError(f"Not enough trading dates before {d} (n={n})")
        return self._dates[target]

    def get_next_trade_date(self, d: date, n: int = 1) -> date:
        """d 之后第 n 个交易日（d 本身不计入）。"""
        # bisect_right 找到 d 之后第一个位置
        idx = bisect.bisect_right(self._dates, d)
        target = idx + n - 1
        if target >= len(self._dates):
            raise ValueError(f"Not enough trading dates after {d} (n={n})")
        return self._dates[target]

    def get_trade_dates(self, start: date, end: date) -> list[date]:
        """返回 [start, end] 范围内的全部交易日（升序，含两端）"""
        lo = bisect.bisect_left(self._dates, start)
        hi = bisect.bisect_right(self._dates, end)
        return self._dates[lo:hi]

    def count_trade_days(self, start: date, end: date) -> int:
        """start 到 end（含两端）之间的交易日数量"""
        return len(self.get_trade_dates(start, end))

    def offset_trade_date(self, d: date, n: int) -> date:
        """以 d 为基准偏移 n 个交易日（n>0 向后，n<0 向前）。

        若 d 本身不是交易日，先找到最近的前一个交易日再偏移。
        """
        if d in self._date_set:
            base_idx = bisect.bisect_left(self._dates, d)
        else:
            # 找最近的前一个交易日
            base_idx = bisect.bisect_right(self._dates, d) - 1
            if base_idx < 0:
                raise ValueError(f"No trading date at or before {d}")

        target = base_idx + n
        if target < 0 or target >= len(self._dates):
            raise ValueError(f"Offset {n} from {d} is out of range")
        return self._dates[target]

    @classmethod
    async def from_adapter(
        cls,
        adapter: DataSourceAdapter,
        start_date: date,
        end_date: date,
    ) -> TradingCalendar:
        """从数据源适配器加载交易日历"""
        dates = await adapter.fetch_trade_calendar(start_date, end_date)
        return cls(dates)
