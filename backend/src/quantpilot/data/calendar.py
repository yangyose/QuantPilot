from __future__ import annotations

import bisect
from collections.abc import Iterable
from datetime import date, timedelta
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from quantpilot.data.adapters.base import DataSourceAdapter


class _CalendarRepo(Protocol):
    """from_repo 所需的最小 repo 接口（避免对 MarketDataRepository 的硬依赖）。"""

    async def get_trade_calendar_dates(
        self, start: date, end: date, *, only_open: bool = ..., exchange: str = ...
    ) -> list[date]: ...


def build_calendar_rows(
    open_days: Iterable[date],
    start: date,
    end: date,
    exchange: str = "SSE",
) -> list[dict]:
    """把开市日列表 + [start, end] 范围重建为「全历法日 + is_open」行。

    每个自然日一行（含闭市日 is_open=False），忠实落库为 trade_calendar。
    open_days 中落在 [start, end] 外的日期忽略（以范围为准）。
    """
    open_set = set(open_days)
    rows: list[dict] = []
    d = start
    while d <= end:
        rows.append({"exchange": exchange, "cal_date": d, "is_open": d in open_set})
        d += timedelta(days=1)
    return rows


def missing_trading_days(
    open_days: Iterable[date],
    present_days: Iterable[date],
) -> list[date]:
    """开市日中未出现在 present_days 的交易日（升序）= 数据缺口。"""
    return sorted(set(open_days) - set(present_days))


def resolve_audit_range(
    arg_start: date | None,
    arg_end: date | None,
    coverage: tuple[date, date],
    data_min: date | None,
    data_max: date | None,
) -> tuple[date, date]:
    """解析完整性审计的差集范围。

    默认范围 = 数据实际覆盖区间（参照表 daily_quote 的 min/max trade_date），并夹在
    日历 coverage 内——避免拿日历的未来前瞻日（尚无行情）或早于回填起点的历法日做
    差集而误报假缺口。显式 arg_start/arg_end 优先。
    """
    cov_start, cov_end = coverage
    start = arg_start or (max(cov_start, data_min) if data_min is not None else cov_start)
    end = arg_end or (min(cov_end, data_max) if data_max is not None else cov_end)
    return start, end


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

    @classmethod
    async def from_repo(
        cls,
        repo: _CalendarRepo,
        start_date: date,
        end_date: date,
        exchange: str = "SSE",
    ) -> TradingCalendar:
        """从持久化的 trade_calendar 表加载交易日历（DB 优先路径）。

        只取 is_open=True 的开市日构造，与 from_adapter 等价但不依赖联网。
        """
        dates = await repo.get_trade_calendar_dates(
            start_date, end_date, only_open=True, exchange=exchange
        )
        return cls(dates)
