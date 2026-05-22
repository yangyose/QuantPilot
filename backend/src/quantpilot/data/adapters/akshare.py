"""AKShare 适配器（备用/补充数据源）。

Phase 2 仅实现 fetch_trade_calendar 和 fetch_stock_list；
Phase 13（V1.0 P13-C）补全 fetch_daily_quotes / fetch_index_history 作为 Tushare 失败降级路径。
财务/指数成分股/namechange 仍未实现（接口不对齐）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

import pandas as pd

from quantpilot.data.adapters.base import DataSourceAdapter

logger = logging.getLogger(__name__)

_AKSHARE_MAX_ROWS_PER_CALL = 1000


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
        """AKShare 日线降级路径（V1.0 Phase 13 实现）。

        【降级说明】仅在 TushareAdapter 失败时由 DataService 自动调用。
        - AKShare 无单日全市场快照接口，必须按 ts_code 单股调用 stock_zh_a_hist
        - 字段覆盖：ts_code / trade_date / open / high / low / close / pct_chg / vol / amount
        - adj_factor / turnover_rate / float_mkt_cap / is_suspended / is_st / limit_up/down
          AKShare 无完整对应接口，置默认值（adj_factor=1.0，is_*/limit_*=False）。
          CP1 已知此降级数据精度低，主要保 OHLCV + pct_chg 不丢失。
        - 单次调用最多 _AKSHARE_MAX_ROWS_PER_CALL=1000 行；超过则抛 NotImplementedError，
          由 DataService 决定是否阻断（避免无限拉取卡死）。
        """
        if ts_codes is None:
            logger.error(
                "akshare_daily_quotes_no_universe ts_codes=None 不支持全市场拉取"
            )
            raise NotImplementedError(
                "AKShareAdapter.fetch_daily_quotes 需要显式 ts_codes（无全市场接口）"
            )
        if len(ts_codes) > _AKSHARE_MAX_ROWS_PER_CALL:
            logger.error(
                "akshare_daily_quotes_universe_too_large size=%d limit=%d",
                len(ts_codes), _AKSHARE_MAX_ROWS_PER_CALL,
            )
            raise NotImplementedError(
                f"AKShare 单日拉取超过 {_AKSHARE_MAX_ROWS_PER_CALL} 只股票，"
                "降级模式不支持（建议恢复 Tushare 后重试）"
            )

        import akshare as ak

        date_str = trade_date.strftime("%Y%m%d")
        rows: list[dict] = []
        for ts_code in ts_codes:
            symbol = ts_code.split(".")[0]
            try:
                df = await asyncio.to_thread(
                    ak.stock_zh_a_hist,
                    symbol=symbol,
                    period="daily",
                    start_date=date_str,
                    end_date=date_str,
                    adjust="",
                )
            except Exception as exc:
                logger.warning(
                    "akshare_stock_hist_failed ts_code=%s reason=%s",
                    ts_code, exc,
                )
                continue
            if df is None or df.empty:
                continue
            r = df.iloc[0]
            rows.append({
                "ts_code": ts_code,
                "trade_date": trade_date,
                "open": float(r.get("开盘", r.get("open", 0))),
                "high": float(r.get("最高", r.get("high", 0))),
                "low": float(r.get("最低", r.get("low", 0))),
                "close": float(r.get("收盘", r.get("close", 0))),
                "pre_close": (
                    float(r.get("收盘", 0)) - float(r.get("涨跌额", 0))
                    if "涨跌额" in r.index else None
                ),
                "pct_chg": float(r.get("涨跌幅", r.get("pct_chg", 0))) / 100,
                "vol": float(r.get("成交量", r.get("vol", 0))) * 100,
                "amount": float(r.get("成交额", r.get("amount", 0))),
                "turnover_rate": (
                    float(r.get("换手率", 0)) / 100 if "换手率" in r.index else None
                ),
                "float_mkt_cap": None,
                "adj_factor": 1.0,
                "is_suspended": False,
                "is_st": False,
                "limit_up": False,
                "limit_down": False,
            })

        if not rows:
            return pd.DataFrame(columns=[
                "ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
                "pct_chg", "vol", "amount", "turnover_rate", "float_mkt_cap",
                "adj_factor", "is_suspended", "is_st", "limit_up", "limit_down",
            ])
        return pd.DataFrame(rows).reset_index(drop=True)

    async def fetch_financial_data(
        self,
        as_of_date: date,
        ts_codes: list[str] | None = None,
    ) -> pd.DataFrame:
        raise NotImplementedError("AKShare financial data not implemented in Phase 2")

    async def fetch_index_history(
        self, index_code: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """AKShare 指数行情降级路径（V1.0 Phase 13 实现）。

        【降级说明】调 stock_zh_index_daily（如 sh000300 / sh000016），
        返回 OHLCV + pct_chg；vol 单位手→股已换算。
        """
        import akshare as ak

        symbol = self._akshare_index_symbol(index_code)
        df = await asyncio.to_thread(ak.stock_zh_index_daily, symbol=symbol)
        if df is None or df.empty:
            return pd.DataFrame(columns=[
                "index_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg",
            ])
        df = df.copy()
        df["trade_date"] = pd.to_datetime(df["date"]).dt.date
        mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
        df = df.loc[mask].copy()
        if df.empty:
            return pd.DataFrame(columns=[
                "index_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg",
            ])
        df["index_code"] = index_code
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["pct_chg"] = df["close"].pct_change().fillna(0.0)
        if "volume" in df.columns:
            df["vol"] = df["volume"].astype(float) * 100  # 手 → 股
        else:
            df["vol"] = 0.0
        cols = ["index_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg"]
        return df[[c for c in cols if c in df.columns]].reset_index(drop=True)

    @staticmethod
    def _akshare_index_symbol(index_code: str) -> str:
        """ts_code（如 000300.SH）→ AKShare symbol（如 sh000300）。"""
        if "." not in index_code:
            return index_code
        code, suffix = index_code.split(".")
        return f"{'sh' if suffix.upper() == 'SH' else 'sz'}{code}"

    async def fetch_index_components(
        self, index_code: str, trade_date: date
    ) -> list[str]:
        raise NotImplementedError("AKShare index components not implemented in Phase 2")

    async def fetch_namechange(
        self, start_date: date, end_date: date
    ) -> pd.DataFrame:
        raise NotImplementedError("AKShare namechange not implemented in Phase 2")
