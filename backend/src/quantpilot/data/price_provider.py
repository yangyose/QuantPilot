from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from quantpilot.data.repository import MarketDataRepository


class AdjustedPriceProvider:
    """复权价格按需派生器。

    禁止将结果持久化为唯一历史数据（SDD §4.1）。
    采用双层接口设计（system_design §5.2）：
      - 私有纯函数层：输入 Series，无 IO，可直接用于单元测试和 Engine 内部调用
      - 公共 DB 层：输入 ts_code + 日期范围，内部查询 Repository 后调用纯函数

    adj_factor 语义（SDD 附录 D.1 / Tushare 定义）：
      - 以上市首日为基准值 1.0
      - 后复权（回测用）：close[t] × adj_factor[t]，历史序列稳定
      - 前复权（展示用）：close[t] × (adj_factor[-1] / adj_factor[t])，以最新价为基准
    """

    def __init__(self, repo: MarketDataRepository) -> None:
        self._repo = repo

    # ── 私有纯函数层（无 IO，直接用于单元测试和策略引擎内部） ───────────────

    @staticmethod
    def _compute_backward(
        close: pd.Series, adj_factor: pd.Series
    ) -> pd.Series:
        """后复权公式：close[t] × adj_factor[t]

        序列稳定，不随新除权事件变化。
        入参：close 和 adj_factor 均以 trade_date 为 index，升序。
        """
        return close * adj_factor

    @staticmethod
    def _compute_forward(
        close: pd.Series, adj_factor: pd.Series
    ) -> pd.Series:
        """前复权公式：close[t] × (adj_factor.iloc[-1] / adj_factor[t])

        动态计算，随新除权事件变化。以最新价为基准，向历史调整。
        """
        return close * (adj_factor.iloc[-1] / adj_factor)

    # ── 公共 DB 层（符合 system_design §5.2 接口，内部查询 Repository） ──────

    async def backward_adjusted(
        self, ts_code: str, start_date: date, end_date: date
    ) -> pd.Series:
        """后复权序列（DB 查询版）。

        内部调用 _repo.get_daily_quotes() 获取 close 和 adj_factor，
        再委托 _compute_backward()。
        """
        df = await self._repo.get_daily_quotes(ts_code, start_date, end_date)
        return self._compute_backward(df["close"], df["adj_factor"])

    async def forward_adjusted(
        self, ts_code: str, start_date: date, end_date: date
    ) -> pd.Series:
        """前复权序列（DB 查询版）。

        内部调用 _repo.get_daily_quotes() 获取 close 和 adj_factor，
        再委托 _compute_forward()。
        """
        df = await self._repo.get_daily_quotes(ts_code, start_date, end_date)
        return self._compute_forward(df["close"], df["adj_factor"])
