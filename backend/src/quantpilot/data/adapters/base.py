from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataSourceAdapter(ABC):
    """数据源适配器基类。

    所有输出 DataFrame 的列名遵循 SDD 附录 D 标准格式（snake_case，元，小数比率）。
    所有方法均为异步，内部用 asyncio.to_thread() 包装同步 SDK。
    接口签名与 system_design_v1.1 §5.1 保持一致：
      - fetch_daily_quotes / fetch_financial_data 支持可选的 ts_codes 过滤，
        ts_codes=None 时取全市场（Phase 2 日线入库场景），
        ts_codes 非 None 时按列表过滤（Phase 3+ 策略评分引擎场景）。
    """

    @abstractmethod
    async def fetch_stock_list(self) -> pd.DataFrame:
        """获取全市场股票基础信息（含已退市股）。

        输出列：ts_code, name, market, sw_industry_l1, sw_industry_l2,
                list_date, delist_date, is_active
        """

    @abstractmethod
    async def fetch_daily_quotes(
        self,
        trade_date: date,
        ts_codes: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取指定交易日日线数据。

        ts_codes=None：取全市场（日线批量入库场景）。
        ts_codes 非 None：只取指定股票（策略评分按需查询场景）。
        输出列：ts_code, trade_date, open, high, low, close, pre_close,
                pct_chg, vol, amount, turnover_rate, float_mkt_cap,
                adj_factor, is_suspended, is_st, limit_up, limit_down
        单位：价格（元）、vol（股）、amount（元）、rate（小数）、市值（元）
        """

    @abstractmethod
    async def fetch_financial_data(
        self,
        as_of_date: date,
        ts_codes: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取截至 as_of_date 最新公告的财务数据（PIT：以 publish_date 为准）。

        ts_codes=None：取全市场。ts_codes 非 None：只取指定股票。
        输出列：ts_code, report_period, publish_date, pe_ttm, pb, roe,
                net_profit_yoy, revenue_yoy, dividend_yield,
                total_equity, debt_to_asset
        单位：比率（小数）、金额（元）
        """

    @abstractmethod
    async def fetch_index_history(
        self, index_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """获取指数历史日线数据（含 OHLCV，Phase 3 ADX 计算所需）。

        输出列：index_code, trade_date, open, high, low, close, vol, pct_chg
        单位：价格（元）、vol（股）、pct_chg（小数）
        """

    @abstractmethod
    async def fetch_trade_calendar(
        self, start_date: date, end_date: date
    ) -> list[date]:
        """获取指定范围内的 A 股交易日列表（升序）。"""

    @abstractmethod
    async def fetch_index_components(
        self, index_code: str, trade_date: date
    ) -> list[str]:
        """获取指定指数在 trade_date 时点的成分股列表（ts_code 列表，升序）。

        用于回测时还原历史可投资宇宙，消除幸存者偏差（SDD §5.2）。
        返回空列表时记录 WARNING（该日期数据源可能无数据，属正常情况）。
        """

    async def fetch_index_components_range(
        self, index_code: str, start_date: date, end_date: date
    ) -> dict[date, list[str]]:
        """范围批量版本：返回 [start_date, end_date] 内所有 snapshot
        {snapshot_date: sorted_components}。

        默认实现按 trade_date 逐日调用 fetch_index_components；速度慢，子类可覆盖
        提供更高效的批量调用（如 Tushare 的 index_weight range query）。
        不支持时抛 NotImplementedError。
        """
        raise NotImplementedError

    @abstractmethod
    async def fetch_namechange(
        self, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """获取历史改名记录，用于历史回填时按 PIT 还原 is_st 状态。

        输出列：ts_code, name, start_date, end_date（end_date=None 表示至今有效）
        不支持时抛 NotImplementedError（AKShare 等备用源无需实现）。
        """
