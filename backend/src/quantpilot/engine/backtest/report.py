"""BacktestReport：回测绩效报告生成（SDD 附录 C，Phase 8）。Engine 层纯函数，无 IO。"""
from __future__ import annotations

import math
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from quantpilot.engine.backtest.engine import BacktestConfig

DISCLAIMER = (
    "V1.0 回测引擎已知局限：撮合方式与 A 股 T+1 规则存在差异（当日 close 撮合，"
    "实盘为次日开盘），未排除涨停/停牌/已退市股，PE/PB 历史分位与指数收益数据切片"
    "在回测中以空集合降级，主流程不调用 RiskChecker（集中度/行业/回撤限制不生效）。"
    "回测净值、Sharpe 等指标与实盘可达成收益**无系统性对应关系**，仅供策略相对排序"
    "参考，不构成任何投资建议。"
)


class BacktestReport:
    """回测绩效报告生成器（SDD §7.7.4 + 附录 C）。"""

    @staticmethod
    def generate(
        nav: dict[date, float],
        signal_history: list[dict],
        config: "BacktestConfig",
    ) -> dict:
        """
        生成标准绩效报告（SDD 附录 C）：
        - cumulative_return / annualized_return / max_drawdown
        - sharpe_ratio（rf=0.03，从 config 中无法拿到 user_config，使用默认值）
        - win_rate / profit_loss_ratio（基于 signal_history 中已平仓交易）
        - total_trading_days

        参数：
          nav             — {trade_date: nav_value}，初始净值 = 1.0
          signal_history  — 每日信号记录列表（用于 win_rate / profit_loss_ratio）
          config          — BacktestConfig（initial_capital 来自 config.initial_capital）
        """
        if not nav:
            return _empty_report()

        nav_values = [v for _, v in sorted(nav.items())]
        total_days = len(nav_values)

        # 累计收益率：最终净值 / 初始净值 - 1（初始净值 = 1.0）
        first_nav = nav_values[0]
        last_nav = nav_values[-1]
        cumulative_return = (last_nav - first_nav) / first_nav if first_nav > 0 else 0.0

        # 年化收益率：(1 + cumulative_return)^(252/days) - 1
        if total_days > 1 and cumulative_return > -1:
            annualized_return = (1 + cumulative_return) ** (252 / (total_days - 1)) - 1
        else:
            annualized_return = 0.0

        # 最大回撤
        max_drawdown = _calc_max_drawdown(nav_values)

        # 夏普比率（rf=0.03 默认值，Phase 8 BacktestEngine 不读取 user_config DB）
        sharpe_ratio = _calc_sharpe(nav_values, rf=0.03)

        # win_rate / profit_loss_ratio（基于 signal_history 中配对交易）
        win_rate, profit_loss_ratio = _calc_win_rate_pl(signal_history, config.initial_capital)

        return {
            "cumulative_return": round(cumulative_return, 6),
            "annualized_return": round(annualized_return, 6),
            "max_drawdown": round(max_drawdown, 6),
            "sharpe_ratio": round(sharpe_ratio, 6),
            "win_rate": win_rate,
            "profit_loss_ratio": profit_loss_ratio,
            "total_trading_days": total_days,
        }


def _empty_report() -> dict:
    return {
        "cumulative_return": 0.0,
        "annualized_return": 0.0,
        "max_drawdown": 0.0,
        "sharpe_ratio": 0.0,
        "win_rate": None,
        "profit_loss_ratio": None,
        "total_trading_days": 0,
    }


def _calc_max_drawdown(nav_values: list[float]) -> float:
    """max(1 - nav_t / running_max_nav_t)，均非负。"""
    if len(nav_values) < 2:
        return 0.0
    running_max = nav_values[0]
    max_dd = 0.0
    for v in nav_values[1:]:
        if v > running_max:
            running_max = v
        drawdown = (running_max - v) / running_max if running_max > 0 else 0.0
        if drawdown > max_dd:
            max_dd = drawdown
    return max_dd


def _calc_sharpe(nav_values: list[float], rf: float = 0.03) -> float:
    """年化夏普比率 = (ann_return - rf) / ann_volatility。"""
    if len(nav_values) < 2:
        return 0.0
    import numpy as np
    navs = np.array(nav_values, dtype=float)
    daily_returns = np.diff(navs) / navs[:-1]
    n = len(daily_returns)
    if n < 2:
        return 0.0
    ann_return = (navs[-1] / navs[0]) ** (252 / n) - 1
    ann_vol = float(daily_returns.std(ddof=1)) * math.sqrt(252)
    if ann_vol == 0:
        return 0.0
    return (ann_return - rf) / ann_vol


def _calc_win_rate_pl(
    signal_history: list[dict],
    initial_capital: float,
) -> tuple[float | None, float | None]:
    """
    从 signal_history 计算胜率和盈亏比（仅已平仓配对交易）。

    signal_history 中每条记录格式（BacktestEngine._execute_signals 写入）：
      {"ts_code": str, "signal_type": "BUY"/"SELL", "trade_date": date,
       "price": float, "shares": int, "cost": float, "proceeds": float}

    按 ts_code 配对 BUY → SELL，计算每笔已平仓收益。
    """
    # 按 ts_code 分组，提取 BUY/SELL 配对
    buys: dict[str, list[dict]] = {}
    sells: dict[str, list[dict]] = {}
    for rec in signal_history:
        code = rec["ts_code"]
        if rec["signal_type"] == "BUY":
            buys.setdefault(code, []).append(rec)
        elif rec["signal_type"] == "SELL":
            sells.setdefault(code, []).append(rec)

    pnls: list[float] = []
    for code, sell_list in sells.items():
        buy_list = buys.get(code, [])
        if not buy_list:
            continue
        total_buy_cost = sum(r["cost"] for r in buy_list)
        total_proceeds = sum(r["proceeds"] for r in sell_list)
        if total_buy_cost > 0:
            pnls.append(total_proceeds - total_buy_cost)

    if not pnls:
        return None, None

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(pnls)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    profit_loss_ratio = round(avg_win / avg_loss, 4) if avg_loss > 0 else None
    return round(win_rate, 4), profit_loss_ratio
