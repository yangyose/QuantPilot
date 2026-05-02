"""AKShare 适配器（备用/补充数据源）。

Phase 2 仅实现 fetch_trade_calendar 和 fetch_stock_list，
其余方法抛 NotImplementedError（Phase 3+ 按需补充）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

import pandas as pd

from quantpilot.data.adapters.base import DataSourceAdapter

logger = logging.getLogger(__name__)


class AKShareAdapter(DataSourceAdapter):
    """AKShare 适配器（备用/补充数据源）。"""

    async def fetch_stock_list(self) -> pd.DataFrame:
        import akshare as ak  # 延迟导入，避免启动时加载

        df = await asyncio.to_thread(ak.stock_info_a_code_name)
        return pd.DataFrame(
            {
                "ts_code": df["code"].apply(
                    lambda c: f"{c}.SH" if c.startswith("6") else f"{c}.SZ"
                ),
                "name": df["name"],
                "market": None,
                "sw_industry_l1": None,
                "sw_industry_l2": None,
                "list_date": None,
                "delist_date": None,
                "is_active": True,
            }
        )

    async def fetch_trade_calendar(
        self, start_date: date, end_date: date
    ) -> list[date]:
        import akshare as ak

        df = await asyncio.to_thread(
            ak.tool_trade_date_hist_sina,
        )
        dates = pd.to_datetime(df["trade_date"]).dt.date.tolist()
        return sorted(d for d in dates if start_date <= d <= end_date)

    async def fetch_daily_quotes(
        self,
        trade_date: date,
        ts_codes: list[str] | None = None,
    ) -> pd.DataFrame:
        raise NotImplementedError("AKShare daily quotes not implemented in Phase 2")

    async def fetch_financial_data(
        self,
        as_of_date: date,
        ts_codes: list[str] | None = None,
    ) -> pd.DataFrame:
        raise NotImplementedError("AKShare financial data not implemented in Phase 2")

    async def fetch_index_history(
        self, index_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        raise NotImplementedError("AKShare index history not implemented in Phase 2")

    async def fetch_index_components(
        self, index_code: str, trade_date: date
    ) -> list[str]:
        raise NotImplementedError("AKShare index components not implemented in Phase 2")

    async def fetch_namechange(
        self, start_date: date, end_date: date
    ) -> pd.DataFrame:
        raise NotImplementedError("AKShare namechange not implemented in Phase 2")
