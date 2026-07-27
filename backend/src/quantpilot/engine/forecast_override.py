"""V1.5-A A5b（SDD-EXT-03）：业绩快报/预告前瞻 ROE 覆盖（Engine 层纯函数，无 IO）。

信息真空期（快报/预告已发、正式财报未发）用 est_net_profit 派生前瞻 ROE 修正估值，
覆盖 ValueStrategy 真消费的 financials.roe（见 v1_5_a §6.3）。生产 ScoringService +
回测 BacktestEngine 两路径共用本函数，保证同一建模。
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def apply_forecast_roe_override(
    financials: pd.DataFrame,
    forecast: pd.DataFrame,
) -> pd.DataFrame:
    """真空期前瞻 ROE 覆盖（纯函数，返回新 DataFrame，不改入参）。

    参数：
      financials — index=ts_code；须含 ``roe`` / ``report_period`` / ``total_equity`` 列
                   （最近一期已发布正式财报）。
      forecast   — index=ts_code；须含 ``report_period`` / ``est_net_profit`` 列
                   （PIT 最新快报/预告，元）。空则原样返回。

    规则：对每股，若 ``forecast.report_period > financials.report_period``（快报期晚于
    正式财报期 = 真空期）且 ``total_equity > 0`` 且 ``est_net_profit`` 非空 →
    ``roe = est_net_profit / total_equity``（一阶近似：快报净利润 ÷ 上期净资产）。
    非真空期 / 数据缺失 → 不覆盖（保守，C-4 不占位）。
    """
    if forecast is None or forecast.empty or financials is None or financials.empty:
        return financials
    required = {"roe", "report_period", "total_equity"}
    if not required.issubset(financials.columns):
        return financials
    if not {"report_period", "est_net_profit"}.issubset(forecast.columns):
        return financials

    out = financials.copy()
    overridden = 0
    # 仅遍历两侧都有的 ts_code（forecast 通常远少于 financials）
    common = forecast.index.intersection(out.index)
    for ts_code in common:
        try:
            f_period = forecast.at[ts_code, "report_period"]
            o_period = out.at[ts_code, "report_period"]
            est_np = forecast.at[ts_code, "est_net_profit"]
            equity = out.at[ts_code, "total_equity"]
            if (
                f_period is not None and o_period is not None
                and pd.Timestamp(f_period) > pd.Timestamp(o_period)
                and est_np is not None and pd.notna(est_np)
                and equity is not None and pd.notna(equity) and float(equity) > 0
            ):
                out.at[ts_code, "roe"] = float(est_np) / float(equity)
                overridden += 1
        except Exception:
            # 单股异常不阻断整体（数据脏行）；不静默——记 warning
            logger.warning(
                "forecast_roe_override_skip_row ts_code=%s", ts_code, exc_info=True,
            )
    if overridden:
        logger.info("forecast_roe_override_applied count=%d", overridden)
    return out
