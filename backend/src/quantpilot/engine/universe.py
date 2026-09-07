"""UniverseFilter：基本面底线过滤，生成每日可投资宇宙（Phase 4）。"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from quantpilot.core.config_defaults import DEFAULT_UNIVERSE, UniverseConfig
from quantpilot.data.calendar import TradingCalendar

logger = logging.getLogger(__name__)

# 基本面字段覆盖率告警阈值：低于此值即认为该条过滤规则已实质失效。
# ⚠️ 原实现只在**恰好 100% 全 NULL** 时才告警，那是个报不出「几乎全死」的阈值。
# 2026-09-07 于 5434 实测：total_equity 在 5 年 21 个采样日中 10 天覆盖率低于 19%
# （最低 1.2%），而全 5y 面板 1114 日里只有 18 天触发过那条 WARNING——因为它要求
# 100%。「F-4 对 98.8% 的股票不生效」在日志里与完全健康长得一模一样。
# 取 0.5：正常年份实测 92~99%，季末真空期跌到 1~19%，两者之间没有别的形态。
_MIN_FIELD_COVERAGE = 0.5


def _warn_if_low_coverage(values: pd.Series, field: str, rule: str) -> None:
    """字段覆盖率低于阈值时告警，并**报出真实覆盖率**。

    只说「跳过了」而不说「覆盖多少」，下次仍然只能分辨 0% 与非 0%。
    """
    n = len(values)
    if n == 0:
        return
    cov = float(values.notna().sum()) / n
    if cov < _MIN_FIELD_COVERAGE:
        logger.warning(
            "universe_filter_low_coverage: %s 覆盖率 %.1f%%（%d/%d）→ %s 实质失效",
            field, cov * 100, int(values.notna().sum()), n, rule,
        )


class UniverseFilter:
    """SDD §5.4：八条硬性过滤规则（F-1~F-8），Engine 层纯函数，无 IO。

    Phase 10：`config` 参数注入自 `config_service.get_universe_params()`；
    `filter()` 的 `min_avg_amount` 缺省回退 `self._cfg.min_liquidity_amount`。
    """

    FINANCIAL_INDUSTRIES: frozenset[str] = frozenset({
        "银行", "证券", "保险", "多元金融",
    })
    MIN_AVG_AMOUNT_DEFAULT: int = int(DEFAULT_UNIVERSE.min_liquidity_amount)

    def __init__(self, config: UniverseConfig | None = None) -> None:
        self._cfg = config or DEFAULT_UNIVERSE

    def filter(
        self,
        stock_info: pd.DataFrame,
        financials: pd.DataFrame,
        daily_quotes: pd.DataFrame,
        today: date,
        calendar: TradingCalendar,
        min_avg_amount: int | None = None,
        financials_history: pd.DataFrame | None = None,
    ) -> pd.Index:
        """
        返回通过全部过滤条件的 ts_code 集合（pd.Index）。纯函数，无 IO。

        参数：
          stock_info         — index=ts_code，含 is_st/list_date/is_suspended/sw_industry_l1
          financials         — index=ts_code，含 total_equity/net_profit_yoy/debt_to_asset
          daily_quotes       — index=ts_code，含 amount/vol/limit_up（F-7/F-8 专用）；
                               若含 avg_amount 列（P5-PRE-4），F-7 优先使用该列（20日均量）
          today              — 评分日
          calendar           — 交易日历（F-2 精确计算用）
          min_avg_amount     — F-7 成交额阈值（元）
          financials_history — MultiIndex(ts_code, report_period)，含 net_profit_yoy；
                               非 None 时 F-5 执行最近两期连续亏损检查（P5-PRE-4 恢复），
                               None 时降级为单期检查（向后兼容）
        """
        if min_avg_amount is None:
            min_avg_amount = int(self._cfg.min_liquidity_amount)
        idx = stock_info.index
        mask = pd.Series(True, index=idx)

        # F-1：非 ST/*ST
        mask &= ~stock_info["is_st"].fillna(False).astype(bool)

        # F-2：上市满 60 交易日（list_date <= get_prev_trade_date(today, 60)）
        # get_prev_trade_date(today, 60) = 60 个交易日之前的那天（today 不含）
        # 即"距 today 恰好有 60 个交易日的 list_date"是可接受的最晚上市日
        min_list_date = calendar.get_prev_trade_date(today, 60)
        list_date_ok = stock_info["list_date"].apply(
            lambda d: (d is not None) and (not _is_missing(d))
            and (pd.Timestamp(d).date() <= min_list_date)
        )
        mask &= list_date_ok

        # F-3：非停牌
        mask &= ~stock_info["is_suspended"].fillna(False).astype(bool)

        # 金融股标识（F-4/F-5/F-6 豁免）
        is_financial = stock_info["sw_industry_l1"].isin(self.FINANCIAL_INDUSTRIES)

        # F-4：净资产为正（NaN → 跳过该条件）
        equity = _get_col(financials, "total_equity", idx)
        _warn_if_low_coverage(equity, "total_equity", "F-4 净资产过滤")
        equity_ok = equity.isna() | (equity > 0)
        mask &= (equity_ok | is_financial)

        # F-5：非连续亏损（net_profit_yoy < 0 为亏损，NaN → 跳过）
        # P5-PRE-4 恢复：当 financials_history (MultiIndex ts_code × report_period) 可用时，
        # 检查最近 2 期是否全为负；不足 2 期则降级为单期；无数据时跳过过滤。
        if financials_history is not None and not financials_history.empty:
            if "net_profit_yoy" in financials_history.columns:
                hist_yoy = financials_history["net_profit_yoy"]

                def _is_consistently_losing(ts_yoy: pd.Series) -> bool:
                    non_nan = ts_yoy.dropna()
                    if len(non_nan) == 0:
                        return False  # 无数据 → 保留（不能确认亏损）
                    return bool((non_nan < 0).all())

                losing_mask = hist_yoy.groupby(level=0).apply(_is_consistently_losing)
                losing = losing_mask.reindex(idx).fillna(False)
                mask &= (~losing | is_financial)
            else:
                # financials_history 无 net_profit_yoy 列 → 降级为单期
                yoy = _get_col(financials, "net_profit_yoy", idx)
                yoy_ok = yoy.isna() | (yoy >= 0)
                mask &= (yoy_ok | is_financial)
        else:
            yoy = _get_col(financials, "net_profit_yoy", idx)
            _warn_if_low_coverage(yoy, "net_profit_yoy", "F-5 连亏过滤")
            yoy_ok = yoy.isna() | (yoy >= 0)
            mask &= (yoy_ok | is_financial)

        # F-6：非高杠杆（debt_to_asset >= 0.9 排除，NaN → 跳过）
        d2a = _get_col(financials, "debt_to_asset", idx)
        _warn_if_low_coverage(d2a, "debt_to_asset", "F-6 高杠杆过滤")
        d2a_ok = d2a.isna() | (d2a < 0.9)
        mask &= (d2a_ok | is_financial)

        # F-7：流动性过滤（20日均成交额 >= min_avg_amount，NaN → 跳过）
        # P5-PRE-4 恢复：优先使用 avg_amount 列（get_avg_amount() 预计算），
        # 降级回 amount 列（当日单日成交额）以保持向后兼容。
        if "avg_amount" in daily_quotes.columns:
            amount = daily_quotes["avg_amount"].reindex(idx)
            amount_ok = amount.isna() | (amount >= min_avg_amount)
            mask &= amount_ok
        elif "amount" in daily_quotes.columns:
            amount = daily_quotes["amount"].reindex(idx)
            amount_ok = amount.isna() | (amount >= min_avg_amount)
            mask &= amount_ok

        # F-8：涨停封死过滤（limit_up=True 且 vol=0 → 无法买入）
        if "limit_up" in daily_quotes.columns and "vol" in daily_quotes.columns:
            limit_up = daily_quotes["limit_up"].reindex(idx).fillna(False).astype(bool)
            vol = daily_quotes["vol"].reindex(idx).fillna(0)
            sealed = limit_up & (vol == 0)
            mask &= ~sealed

        return idx[mask]


def _is_missing(value: object) -> bool:
    """检查值是否为 None / NaT / NaN。"""
    try:
        return pd.isna(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return value is None


def _get_col(df: pd.DataFrame, col: str, idx: pd.Index) -> pd.Series:
    """从 DataFrame 安全提取列，缺列时返回全 NaN。"""
    if col in df.columns:
        return df[col].reindex(idx)
    return pd.Series(float("nan"), index=idx)
