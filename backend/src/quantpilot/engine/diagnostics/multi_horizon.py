"""多前向窗口解析（5/10/20/40 交易日）。V1.5-K K-3。Engine 层纯函数，严格无 IO。

## K-3 缺的不是「再写一遍 IC」

`compute_factor_ic` / `compute_decile_forward_return` 都已接受 `horizon`，
`compute_forward_returns` 也已存在。缺的是**把 horizon 解析成 end_date**，
以及——更要紧的——**把「这个 horizon 算不出来」与「算出来是空」区分开**。

## 两种「不可用」必须可区分

| 情形 | 底层行为 | 若不区分会被读成 |
|---|---|---|
| 日历没有第 N 个交易日 | `get_next_trade_date` 抛 `ValueError` | 整个面板批次崩掉 |
| 日历有、行情数据没到那天 | `compute_forward_returns` 静默返回空 | 「该 horizon 没信号」 |

第二种是**常态而非例外**：`trade_calendar` 表比 `daily_quote` 多 +90 天前瞻
（`bootstrap_trade_calendar` 的 `fill_end`），故窗口末端附近日历给得出日期、
价格却还不存在。若此时只返回一个空 Series，下游看到的是「这天没有观测」，
与「因子确实无预测力」**完全无法分辨**。

这正是 §4.11 元判据的应用：**一个判据若在两种情况下给出相同结果，它就不是判据。**
故本模块显式返回「不可用 horizon → 原因」，且两种原因文案不同——
分析侧据此才知道该补日历还是该补数据。

## h=20 必须与生产逐字一致

生产 `scripts/backfill_daily_ic.py` 用 `calendar.get_next_trade_date(td, 20)`
（`_FORWARD_WINDOW = 20`，SDD §7.4）。此处复用同一调用，不另写偏移逻辑——
差一天就会让跨 horizon 曲线在 20 这点出现口径断裂，而那个断裂**看起来会像
「20 日窗口特别好/特别差」的真实发现**。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from quantpilot.data.calendar import TradingCalendar
from quantpilot.engine.diagnostics.ic_aggregator import compute_forward_returns

__all__ = ["HORIZONS", "HorizonForward", "resolve_forward_returns"]

# 设计文档 §1.2 K-3：5 / 10 / 20 / 40 交易日。20 与生产运行时口径重合（SDD §7.4）。
HORIZONS: tuple[int, ...] = (5, 10, 20, 40)


@dataclass(frozen=True)
class HorizonForward:
    """某个 horizon 的前向收益及其解析出的窗口右端。

    `end_date` 一并返回是为了可追溯——出问题时能直接核对「这条 IC 到底用了哪两天」，
    而不是回头重算一遍偏移。
    """

    horizon: int
    end_date: date
    returns: pd.Series


def resolve_forward_returns(
    adj_close: pd.DataFrame,
    calendar: TradingCalendar,
    base_date: date,
    horizons: tuple[int, ...] = HORIZONS,
    *,
    excluded: set[str] | None = None,
) -> tuple[list[HorizonForward], dict[int, str]]:
    """逐 horizon 解析窗口右端并算前向收益。

    Args:
        adj_close: `get_adj_prices_bulk` 输出（index=ts_code，columns=trade_date，后复权）。
        calendar: 交易日历；窗口右端一律经 `get_next_trade_date(base, h)` 解析。
        base_date: 因子日。
        horizons: 前向交易日数元组。
        excluded: base 或 end 日涨跌停/停牌的 ts_code，对**每个** horizon 都剔除。

    Returns:
        `(可用列表, {不可用 horizon: 原因})`。

        ⚠️ **数据可达但收益全 NaN 不算不可用**——那属「算出来是空」，
        记进 missing 会让「因子无预测力」伪装成「数据不可用」。
    """
    available: list[HorizonForward] = []
    missing: dict[int, str] = {}

    for h in horizons:
        try:
            end_date = calendar.get_next_trade_date(base_date, h)
        except ValueError:
            # 面板要跑上千个交易日，末端几天必然触发——记原因跳过，不中断整批。
            missing[h] = f"日历不足：{base_date} 之后没有第 {h} 个交易日"
            continue

        if end_date not in adj_close.columns:
            missing[h] = f"行情缺失：{end_date} 不在价格矩阵中（日历有该日，数据未到）"
            continue

        available.append(HorizonForward(
            horizon=h,
            end_date=end_date,
            returns=compute_forward_returns(
                adj_close, base_date, end_date, excluded=excluded
            ),
        ))
    return available, missing
